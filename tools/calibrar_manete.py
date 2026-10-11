"""Calibra a relação entre a porcentagem do manete (HUD do jogo) e a pressão de admissão.

A telemetria não informa a posição do manete, só a pressão em ata. Este script
pede por voz cada porcentagem, espera a pressão estabilizar e anota. Com isso o
copiloto passa a falar de potência em porcentagem de manete.

Como usar: em voo nivelado, abaixo de 2000 m, com o copiloto fechado:
    .venv\\Scripts\\python tools\\calibrar_manete.py

Ajuste o manete até o HUD mostrar a porcentagem pedida e segure. Leva uns 3 minutos.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

from copilot.knowledge.base import slug
from copilot.main import ROOT, load_config, voice_path
from copilot.telemetry.receiver import start_receiver
from copilot.telemetry.state import FlightState
from copilot.voice import audio
from copilot.voice.tts import Speaker

# 100% primeiro e por pouco tempo: pode ser um regime limitado a 1 minuto.
STEPS = (100, 90, 80, 70, 60, 50, 40, 30)
SETTLE_SECONDS = 4.0       # tempo para o piloto mexer no manete depois do pedido
STABLE_SECONDS = 2.5       # janela em que a pressão precisa ficar parada
STABLE_RANGE_ATA = 0.012
TIMEOUT_SECONDS = 30.0


def measure(state: FlightState) -> tuple[float, float] | None:
    """Espera a pressão estabilizar e devolve (ata, RPM) médios; None se não estabilizar."""
    start = time.monotonic()
    window: list[tuple[float, float, float]] = []
    while time.monotonic() - start < TIMEOUT_SECONDS:
        time.sleep(0.1)
        engine = state.engine()
        now = time.monotonic()
        if engine is None or now - start < SETTLE_SECONDS:
            continue
        window.append((now, *engine))
        window = [s for s in window if now - s[0] <= STABLE_SECONDS]
        values = [s[1] for s in window]
        if window[-1][0] - window[0][0] >= STABLE_SECONDS * 0.9 and max(values) - min(values) <= STABLE_RANGE_ATA:
            return statistics.fmean(values), statistics.fmean(s[2] for s in window)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibra porcentagem do manete x pressão de admissão")
    parser.add_argument("--saida", type=Path, help="arquivo de saída (padrão: data/aircraft/<avião>.manete.json)")
    args = parser.parse_args()

    config = load_config()
    state = FlightState()
    try:
        start_receiver(state)
    except OSError as e:
        sys.exit(f"Não consegui abrir as portas UDP ({e}). Feche o copiloto e o sniffer.")
    speaker = Speaker(voice_path(config["tts"]["voz"]), audio.find_device(config["audio"]["saida"], "output"),
                      config["tts"]["volume"], config["tts"]["velocidade"])

    print("Aguardando telemetria e o nome do avião (até 15 s)...")
    deadline = time.monotonic() + 15
    while (state.aircraft is None or state.engine() is None) and time.monotonic() < deadline:
        time.sleep(0.2)
    if state.aircraft is None or state.engine() is None:
        sys.exit("Sem telemetria. Você está dentro do avião, em voo?")
    print(f"Avião: {state.aircraft}")
    speaker.say("Calibração do manete. Mantenha o voo nivelado e ajuste o manete pelo HUD quando eu pedir.")
    speaker.wait()

    points = []
    for step in STEPS:
        speaker.say(f"Manete em {step} por cento.")
        result = measure(state)
        if result is None:
            print(f"  {step:>3}%: a pressão não estabilizou, ponto ignorado")
            speaker.say("Não estabilizou, pulando.")
            continue
        ata, rpm = result
        points.append({"manete": step, "ata": round(ata, 3), "rpm": round(rpm)})
        print(f"  {step:>3}%: {ata:.2f} ata, {rpm:.0f} RPM")
        speaker.say("Anotado.")
        speaker.wait()

    if len(points) < 3:
        speaker.say("Calibração incompleta. Nada foi salvo.")
        speaker.wait()
        sys.exit("Menos de 3 pontos medidos; nada foi salvo.")
    # A pressão tem que cair a cada passo; se não caiu, o manete não foi mexido.
    if any(b["ata"] > a["ata"] - 0.01 for a, b in zip(points, points[1:])):
        speaker.say("A pressão não caiu a cada passo. Calibração descartada, tente de novo.")
        speaker.wait()
        sys.exit("A pressão não caiu a cada passo (o manete foi mexido a tempo?); nada foi salvo.")
    path = args.saida or ROOT / "data" / "aircraft" / f"{slug(state.aircraft)}.manete.json"
    path.write_text(json.dumps({"aviao": state.aircraft, "data": date.today().isoformat(), "pontos": points},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Salvo em {path}")
    speaker.say("Calibração concluída. Pode voltar ao voo normal.")
    speaker.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
