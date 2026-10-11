"""Recebe a telemetria UDP do jogo em uma thread e alimenta o estado do voo."""

from __future__ import annotations

import selectors
import socket
import threading

from . import protocol as p
from .state import FlightState


def start_receiver(state: FlightState, addr: str = "127.0.0.1",
                   telemetry_port: int = p.TELEMETRY_PORT, motion_port: int = p.MOTION_PORT):
    """Abre as duas portas e começa a escutar. Levanta OSError se uma porta estiver em uso."""
    selector = selectors.DefaultSelector()
    sockets = []
    try:
        for port in (telemetry_port, motion_port):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(sock)
            sock.bind((addr, port))
            sock.setblocking(False)
            selector.register(sock, selectors.EVENT_READ, port)
    except OSError:
        for sock in sockets:
            sock.close()
        raise

    def run():
        while True:
            for key, _ in selector.select(timeout=0.5):
                try:
                    data, _ = key.fileobj.recvfrom(65535)
                except OSError:
                    # No Windows, um ICMP "porta inacessível" aparece aqui; não é erro nosso.
                    continue
                try:
                    if key.data == telemetry_port:
                        state.on_telemetry(*p.parse_telemetry(data))
                    else:
                        state.on_motion(*p.parse_motion(data))
                except ValueError:
                    continue  # pacote que não fecha: ignora e segue com o próximo

    threading.Thread(target=run, daemon=True, name="telemetria").start()
