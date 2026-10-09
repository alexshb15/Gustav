"""Conversa com o Claude em streaming, entregando a resposta frase a frase."""

from __future__ import annotations

import re
from typing import Iterator

import anthropic

from .prompt import SYSTEM_PROMPT

# Fim de frase: pontuação final seguida de espaço. Frases muito curtas ficam
# juntas da seguinte para a fala não sair picotada.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
MIN_SENTENCE_CHARS = 25


class Brain:
    def __init__(self, model: str, max_tokens: int, memory_turns: int):
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.memory_turns = memory_turns
        self.history: list[dict] = []

    def reply(self, user_text: str) -> Iterator[str]:
        """Gera as frases da resposta conforme chegam.

        Se o consumidor parar de iterar (o piloto interrompeu), o que já foi
        gerado fica no histórico como a resposta daquele turno.
        """
        self.history.append({"role": "user", "content": user_text})
        spoken = ""
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=self.history,
            ) as stream:
                pending = ""
                for text in stream.text_stream:
                    pending += text
                    while True:
                        match = next((m for m in SENTENCE_END.finditer(pending)
                                      if m.start() >= MIN_SENTENCE_CHARS), None)
                        if not match:
                            break
                        sentence, pending = pending[:match.start()], pending[match.end():]
                        spoken += sentence + " "
                        yield sentence
                if pending.strip():
                    spoken += pending
                    yield pending.strip()
        finally:
            if spoken.strip():
                self.history.append({"role": "assistant", "content": spoken.strip()})
            else:
                self.history.pop()
            del self.history[:-2 * self.memory_turns]
