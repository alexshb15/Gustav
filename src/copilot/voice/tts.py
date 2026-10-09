"""Síntese de fala com Piper e fila de frases, em uma thread própria."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable

import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

from .audio import wasapi_settings

# Blocos curtos na escrita para a interrupção ser percebida em até 50 ms.
WRITE_BLOCK_SECONDS = 0.05


class Speaker:
    def __init__(self, model_path: Path, device: int | None, volume: float, speed: float):
        self.voice = PiperVoice.load(model_path)
        self.sample_rate = self.voice.config.sample_rate
        self._config = SynthesisConfig(volume=volume, length_scale=1.0 / speed)
        # A primeira síntese é ~0,8 s mais lenta; paga esse custo na carga.
        for _ in self.voice.synthesize("Pronto.", self._config):
            pass
        self._queue: queue.Queue = queue.Queue()
        self._generation = 0
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16", device=device,
            extra_settings=wasapi_settings(device))
        self._stream.start()
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text: str, on_start: Callable[[], None] | None = None):
        """Enfileira uma frase. `on_start` é chamado quando o áudio dela começa a tocar."""
        self._queue.put((self._generation, text, on_start))

    def stop(self):
        """Cala a fala atual e descarta o que está na fila."""
        self._generation += 1

    def wait(self):
        """Bloqueia até a fila esvaziar."""
        self._queue.join()

    def _run(self):
        block = int(self.sample_rate * WRITE_BLOCK_SECONDS)
        while True:
            generation, text, on_start = self._queue.get()
            try:
                if generation != self._generation:
                    continue
                for chunk in self.voice.synthesize(text, self._config):
                    samples = chunk.audio_int16_array
                    for start in range(0, len(samples), block):
                        if generation != self._generation:
                            break
                        if on_start:
                            on_start()
                            on_start = None
                        self._stream.write(samples[start:start + block])
            finally:
                self._queue.task_done()
