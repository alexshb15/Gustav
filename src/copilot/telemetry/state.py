"""Estado do voo: último valor de cada instrumento e um histórico curto para tendências."""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from . import protocol as p

STALE_SECONDS = 2.0        # sem pacotes por esse tempo = fora do avião
SAMPLE_SECONDS = 0.5       # intervalo entre amostras do histórico
HISTORY_SECONDS = 120
TREND_SECONDS = 30
AOA_MIN_IAS_KMH = 100      # abaixo disso o ângulo de ataque do jogo não é confiável
G = 9.80665


def _pt(value: float, digits: int = 0) -> str:
    """Número com vírgula decimal, como o piloto vai ouvir."""
    return f"{value:.{digits}f}".replace(".", ",")


def _round(value: float, step: int) -> str:
    """Arredonda para o que um piloto leria no instrumento (RPM de 10 em 10, etc.)."""
    return str(int(round(value / step) * step))


def _height(agl: float) -> str:
    return _round(agl, 10) if agl >= 100 else _round(agl, 1)


class FlightState:
    """Atualizado pela thread do receptor; lido pela conversa e, depois, pelos alertas."""

    def __init__(self):
        self._lock = threading.Lock()
        self._indicators: dict[int, tuple] = {}
        self._motion: list[float] | None = None
        self._last_packet = 0.0
        self._last_tick: int | None = None
        self._last_sample = 0.0
        # (instante, km/h, m AGL, rpm, ata, carga G)
        self._history: deque = deque(maxlen=int(HISTORY_SECONDS / SAMPLE_SECONDS))
        self.aircraft: str | None = None

    def on_telemetry(self, tick: int, indicators: dict, events: list, now: float | None = None):
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._last_tick is not None and (tick < self._last_tick
                                                or now - self._last_packet > STALE_SECONDS):
                # o tick volta a zero quando a missão recomeça, e os pacotes param
                # quando o piloto sai do avião: nos dois casos é outro voo
                self._history.clear()
                self._indicators = {}
            self._last_tick = tick
            self._last_packet = now
            self._indicators.update(indicators)
            for event_id, payload in events:
                if event_id == p.AIRCRAFT_NAME and payload:
                    self.aircraft = payload[1:].split(b"\x00")[0].decode("ascii", "replace")
            if now - self._last_sample >= SAMPLE_SECONDS and p.EAS in self._indicators:
                self._last_sample = now
                self._history.append((now, *self._sample()))

    def on_motion(self, tick: int, values: list[float], now: float | None = None):
        with self._lock:
            self._motion = values
            self._last_packet = time.monotonic() if now is None else now

    def connected(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return bool(self._indicators) and now - self._last_packet < STALE_SECONDS

    def engine(self) -> tuple[float, float] | None:
        """(ata, RPM) do primeiro motor, ou None sem telemetria."""
        with self._lock:
            if not self.connected() or p.MANIFOLD not in self._indicators:
                return None
            return self._first(p.MANIFOLD) / p.PA_PER_ATA, self._first(p.RPM)

    def _first(self, indicator: int, default: float = 0.0) -> float:
        values = self._indicators.get(indicator)
        return values[0] if values else default

    def _sample(self) -> tuple:
        accel = self._indicators.get(p.ACCEL, (0.0, G, 0.0))
        return (self._first(p.EAS) * 3.6, self._first(p.AGL), self._first(p.RPM),
                self._first(p.MANIFOLD) / p.PA_PER_ATA, accel[1] / G)

    def describe(self, now: float | None = None) -> str:
        """Retrato dos instrumentos em texto, para acompanhar a pergunta do piloto."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self.connected(now):
                return ("Sem telemetria neste momento: o piloto não está dentro do avião "
                        "ou o jogo não está enviando dados.")
            ias, agl, rpm, ata, load = self._sample()
            gear = self._indicators.get(p.GEAR, ())
            press = self._indicators.get(p.GEAR_PRESS, ())
            flaps = self._first(p.FLAPS)
            past = next((s for s in self._history if now - s[0] <= TREND_SECONDS), None)
            recent = [s for s in self._history if now - s[0] <= TREND_SECONDS]
            motion = self._motion
            aoa = self._first(p.AOA)

        on_ground = any(v > 0.05 for v in press[:2])
        if not gear:
            gear_text = "sem dado"
        elif all(v > 0.98 for v in gear[:2]):
            gear_text = "baixado e travado"
        elif all(v < 0.02 for v in gear[:2]):
            gear_text = "recolhido"
        else:
            gear_text = "em movimento ou com uma perna fora de posição (" + \
                ", ".join(f"{name} {_pt(v * 100)}%" for name, v in zip(("esquerda", "direita"), gear)) + ")"

        lines = [
            f"Avião: {self.aircraft or 'ainda não identificado'}",
            f"Situação: {'no chão' if on_ground else 'em voo'}",
            f"Velocidade indicada: {_round(ias, 5)} km/h",
            f"Altura sobre o solo: {_height(agl)} m (é altura sobre o terreno, não altitude do altímetro)",
            "Motor: parado" if rpm < 50 else f"Motor: funcionando, {_round(rpm, 10)} RPM, {_pt(ata, 2)} ata",
            f"Trem de pouso: {gear_text}",
            f"Flaps: {_pt(flaps * 100)}% do curso (0 recolhidos, 100 totalmente baixados)",
            f"Carga: {_pt(load, 1)} G",
        ]
        if motion:
            # no canal de movimento a arfagem vem negativa com o nariz para cima
            lines.append(f"Atitude: nariz {_pt(-math.degrees(motion[1]))}° em relação ao horizonte "
                         f"(positivo = para cima), inclinação lateral {_pt(abs(math.degrees(motion[2])))}°")
        if ias >= AOA_MIN_IAS_KMH and not on_ground:
            lines.append(f"Ângulo de ataque: {_pt(math.degrees(aoa), 1)}°")
        if past and now - past[0] >= 5:
            seconds = now - past[0]
            lines.append(
                f"Há {_pt(seconds)} s: {_round(past[1], 5)} km/h, {_height(past[2])} m sobre o solo, "
                f"{_round(past[3], 10)} RPM, {_pt(past[4], 2)} ata")
            lines.append(f"Nos últimos {_pt(seconds)} s: carga entre {_pt(min(s[5] for s in recent), 1)} "
                         f"e {_pt(max(s[5] for s in recent), 1)} G, "
                         f"potência máxima {_pt(max(s[4] for s in recent), 2)} ata")
        return "\n".join(lines)
