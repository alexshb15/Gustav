"""Reproduz um voo gravado pelo sniffer, enviando os pacotes UDP como o jogo faria.

Permite desenvolver e testar o copiloto sem abrir o IL-2.

Uso:
    python tools/replay.py captures/voo-AAAAMMDD-HHMMSS.il2rec
    python tools/replay.py voo.il2rec --inicio 120 --velocidade 4
    python tools/replay.py voo.il2rec --inicio 380 --congelar   # fica repetindo aquele instante
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from copilot.telemetry.protocol import RECORD_HEADER, RECORD_MAGIC  # noqa: E402


def read_recording(path: Path):
    data = path.read_bytes()
    if not data.startswith(RECORD_MAGIC):
        sys.exit(f"{path} não é uma gravação do sniffer")
    offset = len(RECORD_MAGIC)
    packets = []
    while offset < len(data):
        t, port, size = RECORD_HEADER.unpack_from(data, offset)
        offset += RECORD_HEADER.size
        packets.append((t, port, data[offset:offset + size]))
        offset += size
    return packets


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduz uma gravação de telemetria do IL-2")
    parser.add_argument("gravacao", type=Path)
    parser.add_argument("--addr", default="127.0.0.1")
    parser.add_argument("--inicio", type=float, default=0.0, help="segundos a pular desde o primeiro pacote")
    parser.add_argument("--velocidade", type=float, default=1.0, help="2 = duas vezes mais rápido")
    parser.add_argument("--congelar", action="store_true",
                        help="repete indefinidamente o segundo que começa em --inicio")
    args = parser.parse_args()

    packets = read_recording(args.gravacao)
    first = packets[0][0]
    packets = [(t - first, port, data) for t, port, data in packets if t - first >= args.inicio]
    if not packets:
        sys.exit("--inicio passa do fim da gravação")
    if args.congelar:
        packets = [pkt for pkt in packets if pkt[0] < args.inicio + 1.0]
    print(f"{len(packets)} pacotes, de {packets[0][0]:.0f} s a {packets[-1][0]:.0f} s da gravação. "
          "Ctrl+C encerra.")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        while True:
            start = time.perf_counter()
            origin = packets[0][0]
            for t, port, data in packets:
                delay = (t - origin) / args.velocidade - (time.perf_counter() - start)
                if delay > 0:
                    time.sleep(delay)
                sock.sendto(data, (args.addr, port))
            if not args.congelar:
                break
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
