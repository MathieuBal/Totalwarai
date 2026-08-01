"""Calcul des recompenses.

Le README insiste sur un point : recompenser uniquement la victoire produirait un
agent pret a sacrifier son armee. Le bareme combine donc une composante terminale,
des evenements ponctuels et une composante continue (usure, isolement).

Aucune valeur n'est codee en dur : tout vient de `config/rewards.yaml`, dont la
version est enregistree avec chaque episode.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from totalwar_ai.config import load_named_config
from totalwar_ai.domain.battle_state import BattleOutcomeKind, BattleState
from totalwar_ai.domain.unit_state import RANGED_ROLES, Side, UnitRole
from totalwar_ai.telemetry.events import Event, EventType


@dataclass(frozen=True, slots=True)
class RewardConfig:
    """Bareme versionne."""

    version: str = "0.1.0"
    terminal: dict[str, float] = field(default_factory=dict)
    events: dict[str, float] = field(default_factory=dict)
    continuous: dict[str, float] = field(default_factory=dict)

    @classmethod
    def default(cls) -> RewardConfig:
        """Bareme de secours, utilise si le YAML est absent."""
        return cls(
            version="0.1.0-defaults",
            terminal={"victory": 1000.0, "defeat": -1000.0, "draw": 0.0},
            events={
                "enemy_unit_destroyed": 100.0,
                "enemy_unit_routed": 30.0,
                "allied_unit_destroyed": -120.0,
                "allied_unit_routed": -40.0,
                "lord_killed": -250.0,
                "successful_flank": 25.0,
                "ranged_unit_caught_in_melee": -35.0,
                "artillery_caught_in_melee": -60.0,
                "unnecessary_order_change": -2.0,
                "action_blocked_by_safety": -5.0,
            },
            continuous={
                "isolated_unit_per_second": -0.5,
                "enemy_strength_lost": 60.0,
                "allied_strength_lost": -70.0,
            },
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> RewardConfig:
        defaults = cls.default()
        return cls(
            version=str(raw.get("version", defaults.version)),
            terminal={**defaults.terminal, **_floats(raw.get("terminal"))},
            events={**defaults.events, **_floats(raw.get("events"))},
            continuous={**defaults.continuous, **_floats(raw.get("continuous"))},
        )

    @classmethod
    def load(cls) -> RewardConfig:
        raw = load_named_config("rewards")
        return cls.from_mapping(raw) if raw else cls.default()


def _floats(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): float(value) for key, value in raw.items()}


@dataclass(frozen=True, slots=True)
class RewardBreakdown:
    """Recompense detaillee : le total seul n'est pas diagnosticable."""

    total: float = 0.0
    components: dict[str, float] = field(default_factory=dict)

    def merged(self, other: RewardBreakdown) -> RewardBreakdown:
        components = dict(self.components)
        for key, value in other.components.items():
            components[key] = components.get(key, 0.0) + value
        return RewardBreakdown(total=self.total + other.total, components=components)

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "components": dict(self.components)}


class RewardCalculator:
    """Traduit evenements et evolution d'etat en recompense."""

    def __init__(self, config: RewardConfig | None = None) -> None:
        self.config = config or RewardConfig.load()

    @property
    def version(self) -> str:
        return self.config.version

    # --- recompense d'etape --------------------------------------------------

    def step_reward(
        self,
        previous: BattleState,
        current: BattleState,
        events: Sequence[Event] = (),
    ) -> RewardBreakdown:
        """Recompense entre deux instantanes consecutifs."""
        components: dict[str, float] = {}
        for event in events:
            key = self.reward_key(event)
            if key is None:
                continue
            value = self.config.events.get(key)
            if value is None:
                continue
            components[key] = components.get(key, 0.0) + value

        delta_time = max(0.0, current.game_time - previous.game_time)
        self._add_continuous(previous, current, delta_time, components)
        total = sum(components.values())
        return RewardBreakdown(total=total, components=components)

    def reward_key(self, event: Event) -> str | None:
        """Cle de bareme correspondant a un evenement, ou `None` s'il n'en a pas."""
        side = event.side
        role = event.role
        if event.type is EventType.UNIT_DESTROYED:
            if side is Side.ENEMY:
                return "enemy_unit_destroyed"
            if role is UnitRole.LORD:
                return "lord_killed"
            return "allied_unit_destroyed"
        if event.type is EventType.UNIT_ROUTED:
            return "enemy_unit_routed" if side is Side.ENEMY else "allied_unit_routed"
        if event.type is EventType.UNIT_ENGAGED and side is Side.ALLY and role in RANGED_ROLES:
            return (
                "artillery_caught_in_melee"
                if role is UnitRole.ARTILLERY
                else "ranged_unit_caught_in_melee"
            )
        if event.type is EventType.FLANK_ATTACK and side is Side.ALLY:
            return "successful_flank"
        if event.type is EventType.ACTION_BLOCKED_BY_SAFETY:
            return "action_blocked_by_safety"
        if event.type is EventType.ORDER_CHANGED and event.payload.get("unnecessary"):
            return "unnecessary_order_change"
        return None

    def _add_continuous(
        self,
        previous: BattleState,
        current: BattleState,
        delta_time: float,
        components: dict[str, float],
    ) -> None:
        """Composantes proportionnelles au temps et a l'usure des deux camps."""
        if delta_time <= 0.0:
            return

        isolated = sum(1 for unit in current.allies() if current.is_isolated(unit))
        if isolated:
            value = self.config.continuous.get("isolated_unit_per_second", 0.0)
            components["isolated_unit_per_second"] = (
                components.get("isolated_unit_per_second", 0.0) + value * isolated * delta_time
            )

        enemy_lost = max(0.0, previous.strength(Side.ENEMY) - current.strength(Side.ENEMY))
        ally_lost = max(0.0, previous.strength(Side.ALLY) - current.strength(Side.ALLY))
        if enemy_lost > 0.0:
            value = self.config.continuous.get("enemy_strength_lost", 0.0)
            components["enemy_strength_lost"] = (
                components.get("enemy_strength_lost", 0.0) + value * enemy_lost
            )
        if ally_lost > 0.0:
            value = self.config.continuous.get("allied_strength_lost", 0.0)
            components["allied_strength_lost"] = (
                components.get("allied_strength_lost", 0.0) + value * ally_lost
            )

    # --- recompense terminale ------------------------------------------------

    def terminal_reward(self, outcome: BattleOutcomeKind) -> RewardBreakdown:
        """Recompense de fin de bataille."""
        key = outcome.value if outcome is not BattleOutcomeKind.UNKNOWN else "draw"
        value = self.config.terminal.get(key, 0.0)
        return RewardBreakdown(total=value, components={f"outcome_{key}": value})
