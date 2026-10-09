"""Fase 0: escuta a telemetria UDP do IL-2 Great Battles, mostra ao vivo e grava.

Uso:
    python tools/sniff_telemetry.py              # escuta, mostra e grava em captures/
    python tools/sniff_telemetry.py --no-record  # só mostra
    python tools/sniff_telemetry.py --raw        # despeja cada pacote em hexadecimal

Ctrl+C encerra e gera o resumo (campos vistos, faixas de valores, frequência).

O formato dos pacotes é autodescritivo (contagens e tamanhos vêm no próprio
pacote), então a decodificação não depende das tabelas de nomes abaixo. Nomes,
unidades e layouts de evento são o que se espera encontrar; o resumo mostra o
que de fato chegou, inclusive IDs desconhecidos e eventos de tamanho inesperado.
"""

from __future__ import annotations

import argparse
import selectors
import socket
import struct
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

TELEMETRY_PORT = 4322
MOTION_PORT = 4321

TELEMETRY_PACKET_ID = 0x54000101
MOTION_PACKET_ID = 0x494C0100

# ID do pacote (u32), tamanho (u16), tick (u32), quantidade de indicadores (u8)
TELEMETRY_HEADER = struct.Struct("<IHIB")
# ID do indicador ou evento (u16), quantidade de floats ou tamanho em bytes (u8)
ITEM_HEADER = struct.Struct("<HB")
# ID (u32), tick (u32), guinada/arfagem/rolagem, rotação xyz, aceleração xyz
MOTION_PACKET = struct.Struct("<II9f")

# O jogo converte a "ata" do manômetro por atmosfera padrão, não pela atmosfera
# técnica: no Bf 109 F-4 os patamares medidos foram 143 760 Pa a 2700 rpm
# (emergência, 1,42 ata) e ~117 500 Pa a 2300 rpm (contínua, 1,15 ata).
PA_PER_ATA = 101325.0

RPM, MANIFOLD, ENG_SHAKE_FRQ, ENG_SHAKE_AMP, GEAR, GEAR_PRESS = range(6)
EAS, AOA, ACCEL, COCKPIT_SHAKE, AGL, FLAPS, AIR_BRAKES = range(6, 13)

# id -> (nome, unidade esperada)
INDICATORS = {
    RPM: ("ENG_RPM", "rpm, um valor por motor"),
    MANIFOLD: ("ENG_MP", "Pa, um valor por motor"),
    ENG_SHAKE_FRQ: ("ENG_SHAKE_FRQ", "Hz"),
    ENG_SHAKE_AMP: ("ENG_SHAKE_AMP", "amplitude"),
    GEAR: ("LGEARS_STATE", "0..1, um valor por perna"),
    GEAR_PRESS: ("LGEARS_PRESS", "0..1, um valor por perna"),
    EAS: ("EAS", "m/s"),
    AOA: ("AOA", "rad"),
    ACCEL: ("ACCELERATION", "m/s², xyz"),
    COCKPIT_SHAKE: ("COCKPIT_SHAKE", "frequência, amplitude"),
    AGL: ("AGL", "m"),
    FLAPS: ("FLAPS", "0..1"),
    AIR_BRAKES: ("AIR_BRAKES", "0..1"),
}

AIRCRAFT_NAME = 0
GUN_FIRE = 9
# Reenviados pelo jogo a cada ~10 s; ao vivo só aparecem quando mudam.
SETUP_EVENTS = {0, 1, 2, 3, 13, 14}

# id -> (nome, layout, campos). Layout None = sem layout fixo ou ainda não decifrado.
# Confirmados em voo (Bf 109 F-4): 0, 1, 2, 3 e 9. Os de 4 a 8 são presumidos,
# ainda não vistos. 13 traz o nome do jogador; 14 é desconhecido.
EVENTS = {
    AIRCRAFT_NAME: ("AIRCRAFT_NAME", None, ""),
    1: ("SETUP_ENG", "<hh3ff", "indice id x y z rpm_max"),
    2: ("SETUP_GUN", "<h3fff", "indice x y z massa_projetil velocidade"),
    3: ("SETUP_LGEAR", "<hh3f", "indice id x y z"),
    4: ("DROP_BOMB", "<3ffH", "x y z massa tipo"),
    5: ("ROCKET_LAUNCH", "<3fffH", "x y z massa raio tipo"),
    6: ("HIT", "<3f3f", "x y z fx fy fz"),
    7: ("DAMAGE", "<3f3f", "x y z fx fy fz"),
    8: ("EXPLOSION", "<3ff", "x y z raio"),
    GUN_FIRE: ("GUN_FIRE", "<B", "indice"),
    13: ("PLAYER_INFO", None, ""),
}

RECORD_MAGIC = b"IL2REC1\n"
# segundos desde o início (f64), porta (u16), tamanho do pacote (u16)
RECORD_HEADER = struct.Struct("<dHH")


def parse_telemetry(data: bytes):
    """Devolve (tick, {id: valores}, [(id, payload)]). Levanta ValueError se não fechar."""
    try:
        packet_id, size, tick, n_indicators = TELEMETRY_HEADER.unpack_from(data, 0)
        if packet_id != TELEMETRY_PACKET_ID:
            raise ValueError(f"ID de pacote inesperado 0x{packet_id:08X}")
        offset = TELEMETRY_HEADER.size
        indicators = {}
        for _ in range(n_indicators):
            indicator_id, count = ITEM_HEADER.unpack_from(data, offset)
            offset += ITEM_HEADER.size
            indicators[indicator_id] = struct.unpack_from(f"<{count}f", data, offset)
            offset += 4 * count
        events = []
        if offset < len(data):
            n_events = data[offset]
            offset += 1
            for _ in range(n_events):
                event_id, event_size = ITEM_HEADER.unpack_from(data, offset)
                offset += ITEM_HEADER.size
                if offset + event_size > len(data):
                    raise ValueError(f"evento {event_id} passa do fim do pacote")
                events.append((event_id, data[offset:offset + event_size]))
                offset += event_size
    except struct.error as e:
        raise ValueError(f"pacote truncado: {e}") from e
    if offset != len(data):
        raise ValueError(f"sobraram {len(data) - offset} bytes após a decodificação")
    if size != len(data):
        raise ValueError(f"campo de tamanho diz {size}, pacote tem {len(data)} bytes")
    return tick, indicators, events


def parse_motion(data: bytes):
    """Devolve (tick, 9 floats). Levanta ValueError se o pacote não for o esperado."""
    if len(data) != MOTION_PACKET.size:
        raise ValueError(f"pacote de movimento com {len(data)} bytes, esperado {MOTION_PACKET.size}")
    packet_id, tick, *values = MOTION_PACKET.unpack(data)
    if packet_id != MOTION_PACKET_ID:
        raise ValueError(f"ID de pacote inesperado 0x{packet_id:08X}")
    return tick, values


def describe_event(event_id: int, payload: bytes) -> str:
    name, layout, fields = EVENTS.get(event_id, (f"EVENTO_{event_id}", None, ""))
    if event_id == AIRCRAFT_NAME and payload:
        # tamanho (u8) seguido do nome terminado em zero
        return f"{name}: " + payload[1:].split(b"\x00")[0].decode("ascii", "replace")
    if layout is None or struct.calcsize(layout) != len(payload):
        return f"{name} ({len(payload)} bytes, layout desconhecido): {payload.hex(' ')}"
    values = struct.unpack(layout, payload)
    parts = (f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
             for k, v in zip(fields.split(), values))
    return f"{name}: " + " ".join(parts)


class Stats:
    """Acumula o que foi visto para o resumo final."""

    def __init__(self):
        self.packets = Counter()         # porta -> pacotes
        self.first_seen = {}             # porta -> instante do primeiro pacote
        self.last_seen = {}
        self.tick_steps = {}             # porta -> Counter de diferenças de tick
        self.last_tick = {}
        self.errors = Counter()          # mensagem -> ocorrências
        self.indicator_packets = Counter()
        self.indicator_counts = {}       # id -> conjunto de quantidades de valores
        self.indicator_ranges = {}       # id -> [[min, max], ...] por posição
        self.event_counts = Counter()
        self.event_sizes = {}            # id -> conjunto de tamanhos
        self.motion_ranges = [[float("inf"), float("-inf")] for _ in range(9)]

    def packet(self, port: int, now: float, tick: int):
        self.packets[port] += 1
        self.first_seen.setdefault(port, now)
        self.last_seen[port] = now
        if port in self.last_tick:
            self.tick_steps.setdefault(port, Counter())[tick - self.last_tick[port]] += 1
        self.last_tick[port] = tick

    def indicators(self, indicators: dict):
        for indicator_id, values in indicators.items():
            self.indicator_packets[indicator_id] += 1
            self.indicator_counts.setdefault(indicator_id, set()).add(len(values))
            ranges = self.indicator_ranges.setdefault(indicator_id, [])
            for i, value in enumerate(values):
                if i == len(ranges):
                    ranges.append([value, value])
                else:
                    ranges[i][0] = min(ranges[i][0], value)
                    ranges[i][1] = max(ranges[i][1], value)

    def events(self, events: list):
        for event_id, payload in events:
            self.event_counts[event_id] += 1
            self.event_sizes.setdefault(event_id, set()).add(len(payload))

    def motion(self, values: list):
        for r, value in zip(self.motion_ranges, values):
            r[0] = min(r[0], value)
            r[1] = max(r[1], value)

    def summary(self) -> str:
        lines = ["RESUMO DA CAPTURA", ""]
        if not self.packets:
            lines += [
                "Nenhum pacote recebido.",
                "Confira: o jogo foi aberto depois de editar o startup.cfg? Você entrou",
                "em um voo (a telemetria só sai dentro do avião)? As seções",
                "telemetrydevice e motiondevice ainda estão no arquivo?",
            ]
            return "\n".join(lines)

        for port, label in ((TELEMETRY_PORT, "telemetria"), (MOTION_PORT, "movimento")):
            n = self.packets[port]
            if not n:
                lines.append(f"Porta {port} ({label}): nenhum pacote")
                continue
            span = self.last_seen[port] - self.first_seen[port]
            rate = f"{(n - 1) / span:.1f} Hz" if span > 0 else "n/d"
            steps = self.tick_steps.get(port, Counter()).most_common(4)
            steps_text = ", ".join(f"{step} ({count}x)" for step, count in steps) or "n/d"
            lines.append(f"Porta {port} ({label}): {n} pacotes em {span:.1f} s, {rate}")
            lines.append(f"    passo do tick entre pacotes: {steps_text}")
        lines.append("")

        lines.append("Indicadores (id, nome, unidade esperada, valores por pacote, presença, faixa):")
        total = self.packets[TELEMETRY_PORT] or 1
        for indicator_id in sorted(self.indicator_packets):
            name, unit = INDICATORS.get(indicator_id, (f"DESCONHECIDO_{indicator_id}", "?"))
            counts = "/".join(str(c) for c in sorted(self.indicator_counts[indicator_id]))
            presence = 100 * self.indicator_packets[indicator_id] / total
            lines.append(f"  {indicator_id:>2} {name:<14} [{unit}]  n={counts}  em {presence:.0f}% dos pacotes")
            for i, (low, high) in enumerate(self.indicator_ranges[indicator_id]):
                lines.append(f"       [{i}] min {low:.4f}   max {high:.4f}")
        missing = [name for i, (name, _) in INDICATORS.items() if i not in self.indicator_packets]
        if missing:
            lines.append(f"  Esperados e nunca recebidos: {', '.join(missing)}")
        lines.append("")

        lines.append("Eventos (id, nome, ocorrências, tamanhos em bytes):")
        for event_id in sorted(self.event_counts):
            name, layout, _ = EVENTS.get(event_id, (f"DESCONHECIDO_{event_id}", None, ""))
            sizes = sorted(self.event_sizes[event_id])
            note = ""
            if event_id not in EVENTS or (layout and sizes != [struct.calcsize(layout)]):
                note = "   <-- layout a revisar"
            lines.append(f"  {event_id:>2} {name:<14} {self.event_counts[event_id]:>6}x  tamanhos {sizes}{note}")
        if not self.event_counts:
            lines.append("  nenhum")
        lines.append("")

        if self.packets[MOTION_PORT]:
            lines.append("Movimento (faixa de cada campo):")
            names = ("guinada rad", "arfagem rad", "rolagem rad", "rot x rad/s", "rot y rad/s",
                     "rot z rad/s", "acel x m/s²", "acel y m/s²", "acel z m/s²")
            for name, (low, high) in zip(names, self.motion_ranges):
                lines.append(f"  {name:<12} min {low:9.4f}   max {high:9.4f}")
            lines.append("")

        lines.append("Erros de decodificação:")
        for message, count in self.errors.most_common():
            lines.append(f"  {count:>6}x  {message}")
        if not self.errors:
            lines.append("  nenhum")
        return "\n".join(lines)


def fmt(values, scale=1.0, spec=".0f") -> str:
    if not values:
        return "--"
    return "/".join(format(v * scale, spec) for v in values)


def live_line(elapsed: float, indicators: dict, motion, gun_shots: int) -> str:
    accel = indicators.get(ACCEL)
    parts = [
        f"{elapsed:7.1f}s",
        f"IAS {fmt(indicators.get(EAS), 3.6):>4} km/h",
        f"AGL {fmt(indicators.get(AGL)):>5} m",
        f"RPM {fmt(indicators.get(RPM)):>5}",
        f"ATA {fmt(indicators.get(MANIFOLD), 1 / PA_PER_ATA, '.2f'):>5}",
        f"trem {fmt(indicators.get(GEAR), spec='.2f')}",
        f"flaps {fmt(indicators.get(FLAPS), spec='.2f')}",
        f"AoA {fmt(indicators.get(AOA), 57.29578, '.1f'):>5}°",
    ]
    if accel:
        parts.append("acel " + fmt(accel, spec="+.1f"))
    if motion:
        parts.append(f"arf {motion[1] * 57.29578:+4.0f}° rol {motion[2] * 57.29578:+5.0f}°")
    if gun_shots:
        parts.append(f"tiros {gun_shots}")
    return "  ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sniffer da telemetria UDP do IL-2 Great Battles")
    parser.add_argument("--addr", default="127.0.0.1", help="endereço local de escuta")
    parser.add_argument("--telemetry-port", type=int, default=TELEMETRY_PORT)
    parser.add_argument("--motion-port", type=int, default=MOTION_PORT)
    parser.add_argument("--interval", type=float, default=0.5, help="segundos entre linhas ao vivo")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "captures",
                        help="pasta das gravações")
    parser.add_argument("--no-record", action="store_true", help="não grava os pacotes")
    parser.add_argument("--raw", action="store_true", help="despeja cada pacote em hexadecimal")
    args = parser.parse_args()

    selector = selectors.DefaultSelector()
    for port in (args.telemetry_port, args.motion_port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((args.addr, port))
        except OSError as e:
            print(f"Não consegui abrir a porta UDP {port}: {e}\n"
                  "Outro programa (SimHub, SRS, outro sniffer) pode estar usando essa porta.",
                  file=sys.stderr)
            return 1
        sock.setblocking(False)
        selector.register(sock, selectors.EVENT_READ, port)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    record_file = None
    if not args.no_record:
        args.out.mkdir(parents=True, exist_ok=True)
        record_path = args.out / f"voo-{stamp}.il2rec"
        record_file = record_path.open("wb")
        record_file.write(RECORD_MAGIC)
        print(f"Gravando em {record_path}")
    print(f"Escutando {args.addr}:{args.telemetry_port} (telemetria) e "
          f"{args.addr}:{args.motion_port} (movimento). Ctrl+C para encerrar.")

    stats = Stats()
    indicators = {}
    motion = None
    gun_shots = 0
    setup_shown = {}                     # (id, 2 primeiros bytes) -> tamanho já mostrado
    last_tick = None
    start = time.perf_counter()
    last_line = start
    last_packet = None
    waiting_shown = False

    try:
        while True:
            for key, _ in selector.select(timeout=0.2):
                try:
                    data, _ = key.fileobj.recvfrom(65535)
                except OSError:
                    # No Windows, um ICMP "porta inacessível" aparece aqui; não é erro nosso.
                    continue
                port = key.data
                now = time.perf_counter()
                last_packet = now
                if record_file:
                    record_file.write(RECORD_HEADER.pack(now - start, port, len(data)))
                    record_file.write(data)
                if args.raw:
                    print(f"[{port}] {len(data)} bytes: {data.hex(' ')}")
                try:
                    if port == args.telemetry_port:
                        tick, new_indicators, events = parse_telemetry(data)
                        stats.packet(TELEMETRY_PORT, now, tick)
                        stats.indicators(new_indicators)
                        stats.events(events)
                        indicators.update(new_indicators)
                        if last_tick is not None and tick < last_tick:
                            print(f"    >> {now - start:7.1f}s  tick reiniciou: novo voo")
                            setup_shown.clear()
                        last_tick = tick
                        for event_id, payload in events:
                            if event_id == GUN_FIRE:
                                gun_shots += 1
                                continue
                            if event_id in SETUP_EVENTS:
                                key = (event_id, payload[:2])
                                if setup_shown.get(key) == len(payload):
                                    continue
                                setup_shown[key] = len(payload)
                            print(f"    >> {now - start:7.1f}s  {describe_event(event_id, payload)}")
                    else:
                        tick, motion = parse_motion(data)
                        stats.packet(MOTION_PORT, now, tick)
                        stats.motion(motion)
                except ValueError as e:
                    if not stats.errors[str(e)]:
                        print(f"    !! erro de decodificação na porta {port}: {e}\n"
                              f"       {data[:64].hex(' ')}")
                    stats.errors[str(e)] += 1

            now = time.perf_counter()
            if last_packet is None:
                if not waiting_shown and now - start > 3:
                    print("Aguardando pacotes... (a telemetria só sai com você dentro do avião)")
                    waiting_shown = True
            elif now - last_line >= args.interval and now - last_packet < 2:
                print(live_line(now - start, indicators, motion, gun_shots))
                gun_shots = 0
                last_line = now
    except KeyboardInterrupt:
        pass
    finally:
        if record_file:
            record_file.close()

    summary = stats.summary()
    print("\n" + summary)
    if not args.no_record:
        summary_path = args.out / f"voo-{stamp}-resumo.txt"
        summary_path.write_text(summary + "\n", encoding="utf-8")
        print(f"\nResumo salvo em {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
