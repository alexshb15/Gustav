"""Gustav: conversa por voz com push-to-talk, com a telemetria do jogo a cada pergunta.

Uso:
    python -m copilot.main                  # segure a tecla de PTT para falar
    python -m copilot.main --texto "..."    # pergunta digitada, resposta falada
    python -m copilot.main --dispositivos   # lista microfones e saídas de áudio
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
import tomllib
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
MIN_RECORDING_SECONDS = 0.3
BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def load_config() -> dict:
    with (ROOT / "config.toml").open("rb") as f:
        return tomllib.load(f)


def voice_path(name: str) -> Path:
    path = ROOT / "models" / f"{name}.onnx"
    if not path.exists():
        sys.exit(f"Voz não encontrada em {path}. Baixe com:\n"
                 f"    python -m piper.download_voices {name} --download-dir models")
    return path


def answer(brain, speaker, user_text: str, t0: float, telemetry: str | None = None,
           aircraft: str | None = None, interrupted=lambda: False):
    """Envia a pergunta, fala a resposta frase a frase e mostra a latência."""
    timing = {}

    def audio_started():
        timing["audio"] = time.perf_counter()

    print("Gustav: ", end="", flush=True)
    try:
        for sentence in brain.reply(user_text, telemetry, aircraft):
            timing.setdefault("llm", time.perf_counter())
            print(sentence, end=" ", flush=True)
            speaker.say(sentence, audio_started if "audio" not in timing else None)
            if interrupted():
                break
    except anthropic.AuthenticationError:
        print("\n  !! chave da API recusada; confira ANTHROPIC_API_KEY no .env")
        return
    except anthropic.RateLimitError:
        print("\n  !! limite de requisições atingido; tente de novo em instantes")
        return
    except anthropic.APIStatusError as e:
        if "credit balance" in e.message:
            print("\n  !! conta da API sem créditos; adicione em console.anthropic.com, Plans & Billing")
        else:
            print(f"\n  !! erro da API ({e.status_code}): {e.message}")
        return
    except anthropic.APIConnectionError:
        print("\n  !! sem conexão com a API")
        return
    print()
    while "audio" not in timing and "llm" in timing and not interrupted():
        time.sleep(0.01)
    if "audio" in timing:
        print(f"  [1ª frase do Claude em {timing['llm'] - t0:.2f} s, "
              f"início da fala em {timing['audio'] - t0:.2f} s]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gustav: copiloto de voz para o IL-2")
    parser.add_argument("--texto", help="pergunta digitada em vez de falada; responde e sai")
    parser.add_argument("--dispositivos", action="store_true", help="lista os dispositivos de áudio")
    args = parser.parse_args()

    from .voice import audio
    if args.dispositivos:
        print(audio.list_devices())
        return 0

    config = load_config()
    load_dotenv(ROOT / ".env")
    if config["sistema"]["prioridade_baixa"]:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)

    from .brain.client import Brain
    from .knowledge.base import Knowledge
    from .telemetry.receiver import start_receiver
    from .telemetry.state import FlightState
    from .voice.tts import Speaker

    game_dir = Path(config["jogo"]["pasta"])
    knowledge = Knowledge(ROOT / "data", game_dir if game_dir.exists() else None)
    if not knowledge.keybinds:
        print(f"Aviso: não li as teclas do jogo em {game_dir}; confira [jogo] no config.toml.")
    state = FlightState()
    try:
        start_receiver(state)
    except OSError as e:
        print(f"Aviso: sem telemetria, não consegui abrir as portas UDP ({e}). "
              "O sniffer ou outro programa está usando as portas?")

    print("Carregando voz...")
    speaker = Speaker(voice_path(config["tts"]["voz"]),
                      audio.find_device(config["audio"]["saida"], "output"),
                      config["tts"]["volume"], config["tts"]["velocidade"])
    brain = Brain(knowledge, config["llm"]["modelo"], config["llm"]["max_tokens"],
                  config["llm"]["turnos_de_memoria"])

    if args.texto:
        time.sleep(1.5)  # tempo para a telemetria chegar, se houver
        print(state.describe())
        answer(brain, speaker, args.texto, time.perf_counter(), state.describe(), state.aircraft)
        speaker.wait()
        return 0

    from .voice.ptt import PushToTalk
    from .voice.stt import Transcriber

    print("Carregando transcrição...")
    transcriber = Transcriber(config["stt"]["modelo"], config["stt"]["threads"], config["stt"]["dica"])
    recorder = audio.Recorder(audio.find_device(config["audio"]["microfone"], "input"))
    ptt = PushToTalk(config["ptt"]["tecla"])
    print(f"Pronto. Segure {config['ptt']['tecla']} para falar; Ctrl+C encerra.")

    announced = None
    try:
        while True:
            while not ptt.pressed():
                time.sleep(0.01)
            speaker.stop()
            recorder.start()
            while ptt.pressed():
                time.sleep(0.01)
            recording = recorder.stop()
            t0 = time.perf_counter()
            telemetry, aircraft = state.describe(), state.aircraft
            if aircraft and aircraft != announced:
                announced = aircraft
                print(f"  Avião: {aircraft}" + ("" if knowledge.has_sheet(aircraft) else " (sem ficha na base)"))
            seconds = recording.size / audio.STT_SAMPLE_RATE
            if seconds < MIN_RECORDING_SECONDS:
                continue
            text = transcriber.transcribe(recording)
            if not text:
                print(f"  (não entendi nada em {seconds:.1f} s de gravação)")
                continue
            print(f"Você: {text}\n  [{seconds:.1f} s de fala, transcrita em {time.perf_counter() - t0:.2f} s]")
            answer(brain, speaker, text, t0, telemetry, aircraft, interrupted=ptt.pressed)
    except KeyboardInterrupt:
        pass
    finally:
        recorder.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
