"""Formato dos pacotes UDP do IL-2 Great Battles, confirmado em voo (Bf 109 F-4).

Só usa a biblioteca padrão: o sniffer e o replay importam daqui sem instalar nada.
"""

from __future__ import annotations

import struct

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
HIT = 6
DAMAGE = 7
GUN_FIRE = 9
# Impacto e dano: IDs presumidos, nunca vistos em voo.
ALERT_EVENTS = {HIT, DAMAGE}
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
