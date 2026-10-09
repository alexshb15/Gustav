"""Push-to-talk por tecla global do Windows.

Lê o estado da tecla com GetAsyncKeyState: funciona com o jogo em foco e não
intercepta a tecla, então ela continua chegando ao jogo normalmente.
"""

from __future__ import annotations

import ctypes

KEYS = {f"numpad{i}": 0x60 + i for i in range(10)}
KEYS.update({f"f{i}": 0x6F + i for i in range(1, 25)})


def key_code(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower()
    if key in KEYS:
        return KEYS[key]
    try:
        return int(key, 0)
    except ValueError:
        raise ValueError(f"Tecla de PTT desconhecida: '{name}'") from None


class PushToTalk:
    def __init__(self, key: str | int):
        self.code = key_code(key)
        self._state = ctypes.windll.user32.GetAsyncKeyState

    def pressed(self) -> bool:
        return bool(self._state(self.code) & 0x8000)
