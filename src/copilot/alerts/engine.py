"""Alertas proativos: regras fixas sobre a telemetria, sem passar pelo Claude.

`step()` recebe uma leitura dos instrumentos e o instante, e devolve as frases a
falar. Não lê relógio nem toca áudio, então roda igual ao vivo e sobre um voo gravado.
"""

from __future__ import annotations

from ..knowledge.base import ATA_TOLERANCE
from ..telemetry import protocol as p
from ..telemetry.state import Reading

# Limites usados quando a ficha do avião (<avião>.toml, tabela [alertas]) não define.
DEFAULT_LIMITS = {
    "aoa_estol": 15.0,     # graus de ângulo de ataque para o aviso de estol
    "carga_g": 8.0,        # carga para o aviso de G
    "flaps_kmh": 250.0,    # velocidade máxima com flaps baixados
    "trem_kmh": 350.0,     # velocidade máxima com trem baixado
}
SPEED_MARGIN_KMH = 20      # folga acima do limite antes de avisar
NEW_FLIGHT_GAP = 5.0       # segundos sem leitura que zeram cronômetros e esperas

GEAR_UP = "Trem recolhido!"
PULL_UP = "Puxa! Solo!"
STALL = "Estol!"
HIGH_G = "Carga alta!"
FLAPS_SPEED = "Flaps baixados, velocidade alta."
GEAR_SPEED = "Trem baixado, velocidade alta."
HIT_TAKEN = "Fomos atingidos!"
DAMAGE_TAKEN = "Dano no avião!"

# id da regra -> (frase, prioridade (0 fala primeiro), segundos mínimos entre repetições)
RULES = {
    "trem_recolhido": (GEAR_UP, 0, 8.0),
    "solo": (PULL_UP, 0, 4.0),
    "estol": (STALL, 1, 6.0),
    "impacto": (HIT_TAKEN, 1, 10.0),
    "dano": (DAMAGE_TAKEN, 1, 10.0),
    "carga_g": (HIGH_G, 2, 8.0),
    "flaps_velocidade": (FLAPS_SPEED, 2, 15.0),
    "trem_velocidade": (GEAR_SPEED, 2, 15.0),
}
TIMER_RULE = "tempo_motor"
TIMER_PRIORITY = 1


def timer_marks(name: str, limit: float) -> list[tuple[float, str]]:
    """Avisos de um regime com limite de `limit` segundos: (segundos usados, frase)."""
    title = name.capitalize()
    if limit <= 120:
        return [(limit / 2, f"{title}: {limit / 2:.0f} segundos."),
                (limit - 10, f"{title} no limite, reduza.")]
    return [(limit - 300, f"{title}: restam cinco minutos."),
            (limit - 60, f"{title}: resta um minuto, reduza.")]


class AlertEngine:
    def __init__(self, knowledge, disabled: tuple[str, ...] = ()):
        self.knowledge = knowledge
        self.disabled = set(disabled)
        self.muted = False
        self._last_now: float | None = None
        self._since: dict[str, float] = {}       # regra -> desde quando a condição vale
        self._last_fired: dict[str, float] = {}
        self._used: dict[str, float] = {}        # regime -> segundos usados

    def phrases(self, aircraft: str | None) -> list[str]:
        """Todas as frases que podem sair para este avião, para sintetizar antes."""
        texts = [rule[0] for rule in RULES.values()]
        for regime in self.knowledge.regimes(aircraft):
            if regime["minutos"]:
                texts += [text for _, text in timer_marks(regime["nome"], regime["minutos"] * 60)]
        return texts

    def _held(self, rule: str, condition: bool, now: float, hold: float) -> bool:
        """A condição vale sem interrupção há pelo menos `hold` segundos?"""
        if not condition:
            self._since.pop(rule, None)
            return False
        return now - self._since.setdefault(rule, now) >= hold

    def _timers(self, r: Reading, aircraft: str | None, dt: float) -> list[str]:
        """Conta o tempo em cada regime limitado e devolve os avisos cruzados neste passo.

        Modelo simples, não o do jogo: o tempo de um regime corre enquanto a pressão
        está nele ou acima, e é devolvido no mesmo ritmo com o motor no contínuo.
        """
        regimes = self.knowledge.regimes(aircraft)
        fired = []
        if not regimes or r.rpm < 50:
            return fired
        tolerance = ATA_TOLERANCE
        resting = r.ata <= regimes[0]["ata"] + tolerance
        for lower, regime in zip(regimes, regimes[1:]):
            name, limit = regime["nome"], regime["minutos"] * 60
            if not limit:
                continue
            before = self._used.get(name, 0.0)
            if r.ata > lower["ata"] + tolerance:
                used = before + dt
            elif resting:
                used = max(0.0, before - dt)
            else:
                used = before
            self._used[name] = used
            fired += [text for mark, text in timer_marks(name, limit) if before < mark <= used]
        return fired

    def timers_line(self, aircraft: str | None) -> str | None:
        """Linha de telemetria com o tempo já usado de cada regime limitado."""
        parts = []
        for regime in self.knowledge.regimes(aircraft):
            used = self._used.get(regime["nome"], 0.0)
            if regime["minutos"] and used >= 1:
                if regime["minutos"] > 2:
                    parts.append(f"{regime['nome']} {used / 60:.0f} de {regime['minutos']:g} min")
                else:
                    parts.append(f"{regime['nome']} {used:.0f} de {regime['minutos'] * 60:.0f} s")
        if not parts:
            return None
        return ("Tempo de motor usado (estimativa do copiloto, recupera no regime contínuo): "
                + "; ".join(parts))

    def step(self, r: Reading | None, aircraft: str | None, now: float) -> list[str]:
        """Avalia as regras e devolve as frases a falar agora, da mais urgente para a menos."""
        if r is None:
            self._since.clear()
            return []
        if self._last_now is None or now - self._last_now > NEW_FLIGHT_GAP:
            # primeira leitura, ou voltou depois de uma pausa: é outro voo
            self._since.clear()
            self._used.clear()
            self._last_now = now
            return []
        dt = now - self._last_now
        self._last_now = now

        limits = {**DEFAULT_LIMITS, **self.knowledge.alert_limits(aircraft)}
        flying = not r.on_ground and r.ias > 60
        gear_down = r.gear_down > 0.98
        conditions = {
            # aproximação: baixo, devagar, potência reduzida e descendo, com o trem em cima
            "trem_recolhido": (not gear_down and flying and r.agl < 150 and r.ias < 260
                               and r.ata < 0.9 and r.sink > 1.0, 2.0),
            # menos de 3 s até o chão na razão de descida atual
            "solo": (flying and not gear_down and r.sink > 15 and r.agl < 3.0 * r.sink, 0.3),
            "estol": (flying and r.ias > 120 and r.aoa >= limits["aoa_estol"]
                      and not (gear_down and r.agl < 30), 0.4),
            "carga_g": (r.load >= limits["carga_g"], 0.2),
            "flaps_velocidade": (r.flaps > 0.1 and r.ias > limits["flaps_kmh"] + SPEED_MARGIN_KMH, 1.5),
            "trem_velocidade": (flying and r.gear_down > 0.5
                                and r.ias > limits["trem_kmh"] + SPEED_MARGIN_KMH, 1.5),
            "impacto": (p.HIT in r.events, 0.0),
            "dano": (p.DAMAGE in r.events, 0.0),
        }
        fired = []
        for rule, (condition, hold) in conditions.items():
            text, priority, cooldown = RULES[rule]
            if (self._held(rule, condition, now, hold) and rule not in self.disabled
                    and now - self._last_fired.get(rule, -1e9) >= cooldown):
                self._last_fired[rule] = now
                fired.append((priority, text))
        timer_texts = self._timers(r, aircraft, dt)
        if TIMER_RULE not in self.disabled:
            fired += [(TIMER_PRIORITY, text) for text in timer_texts]
        if self.muted:
            return []
        return [text for _, text in sorted(fired, key=lambda item: item[0])]
