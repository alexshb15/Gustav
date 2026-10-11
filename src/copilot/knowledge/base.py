"""Monta o prompt de sistema: personagem, ficha do avião atual e teclas do jogo."""

from __future__ import annotations

import re
from pathlib import Path

from ..brain.prompt import SYSTEM_PROMPT
from .keybinds import load_keybinds

FAMILY = "bf109"


def slug(aircraft: str) -> str:
    """'Bf 109 F-4' -> 'bf109f4', o nome do arquivo em data/aircraft/."""
    return re.sub(r"[^a-z0-9]", "", aircraft.lower())


class Knowledge:
    def __init__(self, data_dir: Path, game_dir: Path | None):
        self.aircraft_dir = data_dir / "aircraft"
        self.keybinds = load_keybinds(game_dir) if game_dir else ""
        self._prompts: dict[str, str] = {}

    def _read(self, name: str) -> str | None:
        path = self.aircraft_dir / f"{name}.md"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def has_sheet(self, aircraft: str) -> bool:
        return (self.aircraft_dir / f"{slug(aircraft)}.md").exists()

    def system_prompt(self, aircraft: str | None) -> str:
        """Texto estável por avião, para o cache de prompt valer entre perguntas."""
        key = slug(aircraft) if aircraft else ""
        if key not in self._prompts:
            parts = [SYSTEM_PROMPT]
            family = self._read(FAMILY) if not key or key.startswith(FAMILY) else None
            sheet = self._read(key) if key else None
            if family:
                parts.append("<familia_bf109>\n" + family + "</familia_bf109>")
            if sheet:
                parts.append(f"<ficha_do_aviao nome=\"{aircraft}\">\n{sheet}</ficha_do_aviao>")
            elif aircraft:
                parts.append(f"Você não tem a ficha do {aircraft}. Para limites de motor e "
                             "velocidades dessa variante, diga que ainda não tem os números dela.")
            if self.keybinds:
                parts.append("<teclas_do_jogo>\nConfiguração atual do piloto, uma ação por linha, com a "
                             "descrição em inglês do jogo. Em pares separados por barra, a primeira "
                             "diminui e a segunda aumenta.\n" + self.keybinds + "\n</teclas_do_jogo>")
            self._prompts[key] = "\n\n".join(parts)
        return self._prompts[key]
