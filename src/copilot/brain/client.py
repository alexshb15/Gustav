"""Conversa com o Claude em streaming, entregando a resposta frase a frase."""

from __future__ import annotations

import re
from typing import Iterator

import anthropic

# Fim de frase: pontuação final seguida de espaço. Frases muito curtas ficam
# juntas da seguinte para a fala não sair picotada.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
MIN_SENTENCE_CHARS = 25


class Brain:
    def __init__(self, knowledge, model: str, max_tokens: int, memory_turns: int):
        self.client = anthropic.Anthropic()
        self.knowledge = knowledge
        self.model = model
        self.max_tokens = max_tokens
        self.memory_turns = memory_turns
        self.history: list[dict] = []
        self.last_usage = None

    def reply(self, user_text: str, telemetry: str | None = None,
              aircraft: str | None = None) -> Iterator[str]:
        """Gera as frases da resposta conforme chegam.

        `telemetry` é o retrato dos instrumentos no momento da pergunta. Ele vai
        junto da mensagem do piloto, e não como ferramenta, para a resposta não
        custar uma ida e volta a mais ao modelo.

        Se o consumidor parar de iterar (o piloto interrompeu), o que já foi
        gerado fica no histórico como a resposta daquele turno.
        """
        content = user_text
        if telemetry:
            content = f"<telemetria>\n{telemetry}\n</telemetria>\n\n{user_text}"
        self.history.append({"role": "user", "content": content})
        spoken = ""
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                # 1 h de cache: em voo as perguntas ficam minutos distantes uma da outra
                system=[{"type": "text", "text": self.knowledge.system_prompt(aircraft),
                         "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
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
                self.last_usage = stream.get_final_message().usage
        finally:
            if spoken.strip():
                self.history.append({"role": "assistant", "content": spoken.strip()})
            else:
                self.history.pop()
            del self.history[:-2 * self.memory_turns]
