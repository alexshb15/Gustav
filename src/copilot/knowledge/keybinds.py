"""Lê as teclas configuradas no jogo (data/input/global.actions) e descreve em português."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

# global.actions é o que o jogo regrava ao sair; current.actions fica defasado.
ACTIONS_FILE = "global.actions"
LINE = re.compile(r"^(\w+),\s*([^,|]*?),\s*\d\|\s*(?://\s*(.*))?$")

MODIFIERS = {
    "lcontrol": "Ctrl esquerdo", "rcontrol": "Ctrl direito",
    "lshift": "Shift esquerdo", "rshift": "Shift direito",
    "lmenu": "Alt esquerdo", "rmenu": "Alt direito",
    "lwin": "Windows esquerdo", "rwin": "Windows direito",
}
# Ações que não interessam a quem pilota um caça em VR.
SKIP_PREFIXES = ("cam_", "gui_", "console", "tc_", "bc_head", "bc_snap", "turret", "gunner",
                 "chat", "record", "screenshot", "ipd", "resethmd")


def _devices(input_dir: Path) -> dict[str, str]:
    """configId -> modelo, de devices.txt."""
    devices = {}
    path = input_dir / "devices.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().rstrip("|").split(",")
            if len(parts) == 3 and parts[0].isdigit():
                devices[parts[0]] = unquote(parts[2])
    return devices


def _key(token: str, devices: dict[str, str]) -> str:
    token = token.strip()
    joy = re.fullmatch(r"joy(\d+)_(b|axis_|pov)(\w+)", token)
    if joy:
        device = devices.get(joy[1], f"joystick {joy[1]}")
        kind = {"b": "botão", "axis_": "eixo", "pov": "chapéu"}[joy[2]]
        return f"{kind} {joy[3].upper()} do {device}"
    name = token.removeprefix("key_")
    if name in MODIFIERS:
        return MODIFIERS[name]
    numpad = re.fullmatch(r"numpad(\w+)", name)
    if numpad:
        return f"{numpad[1]} do teclado numérico"
    return name.upper() if len(name) == 1 else name


def _command(command: str, devices: dict[str, str]) -> str:
    # "a/b" é um par diminuir/aumentar de um eixo no teclado; "a+b" é uma combinação
    return " / ".join(" + ".join(_key(k, devices) for k in half.split("+"))
                      for half in command.split("/"))


def load_keybinds(game_dir: Path) -> str:
    """Uma linha por ação com tecla atribuída: 'descrição do jogo: tecla ou tecla'."""
    input_dir = game_dir / "data" / "input"
    path = input_dir / ACTIONS_FILE
    if not path.exists():
        return ""
    devices = _devices(input_dir)
    actions: dict[str, dict] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = LINE.match(raw.strip())
        if not match:
            continue
        action, command, comment = match.groups()
        if action.startswith(SKIP_PREFIXES):
            continue
        entry = actions.setdefault(action, {"comment": None, "keys": []})
        entry["comment"] = entry["comment"] or comment
        if command:
            entry["keys"].append(_command(command, devices))
    return "\n".join(f"{e['comment'] or action}: {' ou '.join(e['keys'])}"
                     for action, e in actions.items() if e["keys"])
