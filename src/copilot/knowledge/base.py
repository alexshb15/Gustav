"""Monta o prompt de sistema: personagem, ficha do avião atual e teclas do jogo."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from ..brain.prompt import SYSTEM_PROMPT
from .keybinds import load_keybinds

FAMILY = "bf109"
# Folga na pressão ao classificar o regime: o ponteiro oscila em torno do batente.
ATA_TOLERANCE = 0.03


def slug(aircraft: str) -> str:
    """'Bf 109 F-4' -> 'bf109f4', o nome do arquivo em data/aircraft/."""
    return re.sub(r"[^a-z0-9]", "", aircraft.lower())


def _pt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _limit(minutes: float) -> str:
    return "sem limite de tempo" if not minutes else f"no máximo {minutes:g} min"


class Knowledge:
    def __init__(self, data_dir: Path, game_dir: Path | None):
        self.aircraft_dir = data_dir / "aircraft"
        self.keybinds = load_keybinds(game_dir) if game_dir else ""
        self._prompts: dict[str, str] = {}
        self._engines: dict[str, tuple] = {}

    def _read(self, name: str) -> str | None:
        path = self.aircraft_dir / f"{name}.md"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def has_sheet(self, aircraft: str) -> bool:
        return (self.aircraft_dir / f"{slug(aircraft)}.md").exists()

    def _engine(self, aircraft: str | None) -> tuple[list[dict], list[tuple[float, float]]]:
        """(regimes em ordem crescente de pressão, pontos de calibração (% do manete, ata))."""
        key = slug(aircraft) if aircraft else ""
        if key not in self._engines:
            regimes, points = [], []
            path = self.aircraft_dir / f"{key}.toml"
            if key and path.exists():
                with path.open("rb") as f:
                    regimes = sorted(tomllib.load(f).get("regime", []), key=lambda r: r["ata"])
            path = self.aircraft_dir / f"{key}.manete.json"
            if key and path.exists():
                points = sorted((p["manete"], p["ata"])
                                for p in json.loads(path.read_text(encoding="utf-8"))["pontos"])
            self._engines[key] = (regimes, points)
        return self._engines[key]

    def throttle_for(self, aircraft: str | None, ata: float) -> float | None:
        """Porcentagem do manete que dá `ata`, pela calibração; None se não houver."""
        points = self._engine(aircraft)[1]
        if len(points) < 2 or ata > points[-1][1] + ATA_TOLERANCE:
            return None
        if ata <= points[0][1]:
            return points[0][0]
        for (t0, a0), (t1, a1) in zip(points, points[1:]):
            if ata <= a1:
                return t1 if a1 <= a0 else t0 + (t1 - t0) * (ata - a0) / (a1 - a0)
        return points[-1][0]

    def engine_line(self, aircraft: str | None, ata: float | None) -> str | None:
        """Linha de telemetria com o regime atual e o manete estimado, já calculados."""
        regimes = self._engine(aircraft)[0]
        if ata is None or not regimes:
            return None
        current = next((r for r in regimes if ata <= r["ata"] + ATA_TOLERANCE), None)
        if current is None:
            text = f"acima de {regimes[-1]['nome']}, fora dos limites do motor"
        elif current is regimes[0]:
            text = f"{current['nome']} ou abaixo, {_limit(current['minutos'])}"
        else:
            text = f"{current['nome']}, {_limit(current['minutos'])}"
        throttle = self.throttle_for(aircraft, ata)
        if throttle is not None:
            text += f"; manete estimado em {throttle:.0f}%"
        return f"Regime do motor: {text}"

    def _regimes_text(self, aircraft: str) -> str | None:
        regimes, points = self._engine(aircraft)
        if not regimes:
            return None
        lines = []
        for r in regimes:
            line = f"- {r['nome'].capitalize()}: "
            throttle = self.throttle_for(aircraft, r["ata"])
            if throttle is not None:
                line += f"manete em {throttle:.0f}%, "
            lines.append(line + f"{_pt(r['ata'])} ata, {r['rpm']} RPM, {_limit(r['minutos'])}")
        if points:
            note = ("O piloto lê a posição do manete em porcentagem no HUD e não olha o manômetro. "
                    "Ao falar de potência, diga a porcentagem do manete e o nome do regime; só cite "
                    "ata se ele perguntar por ela. As porcentagens vêm de uma calibração feita em voo "
                    "e valem em baixa altitude.")
        else:
            note = ("Ainda não há calibração do manete para este avião: você não sabe converter ata "
                    "em porcentagem do manete. Se o piloto pedir a porcentagem, diga isso e dê o valor "
                    "em ata e RPM.")
        return "Regimes do motor:\n" + "\n".join(lines) + "\n\n" + note

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
                regimes = self._regimes_text(aircraft)
                parts.append(f"<ficha_do_aviao nome=\"{aircraft}\">\n{sheet}"
                             + (f"\n{regimes}\n" if regimes else "") + "</ficha_do_aviao>")
            elif aircraft:
                parts.append(f"Você não tem a ficha do {aircraft}. Para limites de motor e "
                             "velocidades dessa variante, diga que ainda não tem os números dela.")
            if self.keybinds:
                parts.append("<teclas_do_jogo>\nConfiguração atual do piloto, uma ação por linha, com a "
                             "descrição em inglês do jogo. Em pares separados por barra, a primeira "
                             "diminui e a segunda aumenta.\n" + self.keybinds + "\n</teclas_do_jogo>")
            self._prompts[key] = "\n\n".join(parts)
        return self._prompts[key]
