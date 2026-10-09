"""Transcrição local com faster-whisper, em CPU para deixar a GPU com o VR."""

from __future__ import annotations

import numpy as np
from faster_whisper import WhisperModel

# Abaixo disso a gravação é só ruído de fundo; o Whisper inventaria texto.
SILENCE_RMS = 0.003


class Transcriber:
    def __init__(self, model: str, threads: int, hint: str):
        self.model = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
        self.hint = hint

    def transcribe(self, audio: np.ndarray) -> str:
        """Texto falado em `audio` (float32, 16 kHz mono), ou '' se não houver fala."""
        if audio.size == 0 or float(np.sqrt(np.mean(audio ** 2))) < SILENCE_RMS:
            return ""
        segments, _ = self.model.transcribe(
            audio, language="pt", initial_prompt=self.hint or None,
            beam_size=1, condition_on_previous_text=False)  # beam 1: ~0,3 s a menos, mesmo texto
        return " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
