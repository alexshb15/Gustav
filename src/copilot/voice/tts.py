"""Síntese de fala com Piper e fila de frases, em uma thread própria.

Duas vias: a conversa (frases enfileiradas, que o piloto pode interromper) e os
alertas, que furam a fila e tocam no meio de uma frase da conversa.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

from .audio import wasapi_settings

# Blocos curtos na escrita para a interrupção ser percebida em até 50 ms.
WRITE_BLOCK_SECONDS = 0.05
PAUSE_AFTER_ALERT_SECONDS = 0.25


class Speaker:
    def __init__(self, model_path: Path, device: int | None, volume: float, speed: float):
        self.voice = PiperVoice.load(model_path)
        self.sample_rate = self.voice.config.sample_rate
        self._config = SynthesisConfig(volume=volume, length_scale=1.0 / speed)
        self._voice_lock = threading.Lock()
        # A primeira síntese é ~0,8 s mais lenta; paga esse custo na carga.
        self._synthesize("Pronto.")
        self._queue: queue.Queue = queue.Queue()
        self._alerts: deque[str] = deque()
        self._prepared: dict[str, np.ndarray] = {}
        self._generation = 0
        self._block = int(self.sample_rate * WRITE_BLOCK_SECONDS)
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16", device=device,
            extra_settings=wasapi_settings(device))
        self._stream.start()
        threading.Thread(target=self._run, daemon=True).start()

    def _synthesize(self, text: str) -> np.ndarray:
        # O Piper não é chamado por duas threads ao mesmo tempo (conversa e preparo de alertas).
        with self._voice_lock:
            return np.concatenate([chunk.audio_int16_array
                                   for chunk in self.voice.synthesize(text, self._config)])

    def say(self, text: str, on_start: Callable[[], None] | None = None):
        """Enfileira uma frase. `on_start` é chamado quando o áudio dela começa a tocar."""
        self._queue.put((self._generation, text, on_start))

    def stop(self):
        """Cala a conversa atual e descarta o que está na fila. Não afeta alertas."""
        self._generation += 1

    def wait(self):
        """Bloqueia até a fila da conversa esvaziar."""
        self._queue.join()

    def prepare(self, texts: list[str]):
        """Sintetiza de antemão as frases de alerta, para saírem sem atraso."""
        for text in texts:
            if text not in self._prepared:
                self._prepared[text] = self._synthesize(text)

    def alert(self, text: str):
        """Fala já, por cima da conversa. Frases iguais ainda pendentes não se acumulam."""
        if text not in self._alerts:
            self._alerts.append(text)

    def _play_alerts(self):
        while self._alerts:
            text = self._alerts.popleft()
            samples = self._prepared.get(text)
            if samples is None:
                samples = self._prepared[text] = self._synthesize(text)
            self._stream.write(samples)
            self._stream.write(np.zeros(int(self.sample_rate * PAUSE_AFTER_ALERT_SECONDS), dtype=np.int16))

    def _run(self):
        while True:
            self._play_alerts()
            try:
                generation, text, on_start = self._queue.get(timeout=WRITE_BLOCK_SECONDS)
            except queue.Empty:
                continue
            try:
                if generation != self._generation:
                    continue
                samples = self._synthesize(text)
                for start in range(0, len(samples), self._block):
                    self._play_alerts()
                    if generation != self._generation:
                        break
                    if on_start:
                        on_start()
                        on_start = None
                    self._stream.write(samples[start:start + self._block])
            finally:
                self._queue.task_done()
