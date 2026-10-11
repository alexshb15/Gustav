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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from copilot.telemetry.protocol import (  # noqa: E402
    AIRCRAFT_NAME, ACCEL, AGL, AOA, EAS, EVENTS, FLAPS, GEAR, GUN_FIRE, INDICATORS,
    MANIFOLD, MOTION_PORT, PA_PER_ATA, RECORD_HEADER, RECORD_MAGIC, RPM, SETUP_EVENTS,
    TELEMETRY_PORT, describe_event, parse_motion, parse_telemetry)


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
