"""Escolha de dispositivos de áudio e gravação do microfone."""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

STT_SAMPLE_RATE = 16000  # o Whisper trabalha em 16 kHz mono


def find_device(name: str, kind: str) -> int | None:
    """Índice do dispositivo WASAPI cujo nome contém `name`. kind: 'input' ou 'output'.

    None = dispositivo padrão do sistema. WASAPI é a API de menor latência no
    Windows e, com auto_convert, aceita qualquer taxa de amostragem.
    """
    if not name:
        return None
    apis = sd.query_hostapis()
    channels = f"max_{kind}_channels"
    matches = [i for i, d in enumerate(sd.query_devices())
               if d[channels] > 0 and name.lower() in d["name"].lower()]
    for i in matches:
        if "WASAPI" in apis[sd.query_devices(i)["hostapi"]]["name"]:
            return i
    if matches:
        return matches[0]
    raise ValueError(f"Nenhum dispositivo de {kind} com '{name}' no nome. "
                     "Rode com --dispositivos para ver a lista.")


def wasapi_settings(device: int | None):
    """Conversão automática de taxa e canais, só aplicável a dispositivos WASAPI."""
    index = device if device is not None else sd.default.device[0]
    api = sd.query_hostapis(sd.query_devices(index)["hostapi"])["name"]
    return sd.WasapiSettings(auto_convert=True) if "WASAPI" in api else None


def list_devices() -> str:
    apis = sd.query_hostapis()
    lines = []
    for d in sd.query_devices():
        if "WASAPI" not in apis[d["hostapi"]]["name"]:
            continue
        kind = "microfone" if d["max_input_channels"] else "saída    "
        lines.append(f"  {kind}  {d['name']}")
    return "\n".join(sorted(lines))


class Recorder:
    """Mantém o microfone aberto e guarda o áudio só enquanto o PTT está apertado."""

    def __init__(self, device: int | None):
        self._blocks: list[np.ndarray] = []
        self._active = False
        self._lock = threading.Lock()
        self._stream = sd.InputStream(
            samplerate=STT_SAMPLE_RATE, channels=1, dtype="float32", device=device,
            callback=self._callback, extra_settings=wasapi_settings(device))
        self._stream.start()

    def _callback(self, indata, frames, time, status):
        with self._lock:
            if self._active:
                self._blocks.append(indata[:, 0].copy())

    def start(self):
        with self._lock:
            self._blocks = []
            self._active = True

    def stop(self) -> np.ndarray:
        with self._lock:
            self._active = False
            blocks, self._blocks = self._blocks, []
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)

    def close(self):
        self._stream.close()
