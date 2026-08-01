"""Evenements structures du systeme.

Les journaux ne doivent pas etre du texte libre : chaque fait notable devient un
:class:`Event` serialisable, exploitable ensuite par le rapport, les recompenses
et la memoire.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from totalwar_ai.domain.unit_state import Side, UnitRole


class EventType(str, Enum):
    """Types d'evenements journalisables.

    Les quinze premiers sont ceux listes dans le README ; les suivants decrivent
    les faits de combat necessaires au calcul des recompenses.
    """

    # Cycle de vie (README)
    BATTLE_STARTED = "battle_started"
    UNIT_DISCOVERED = "unit_discovered"
    STATE_RECEIVED = "state_received"
    PLAN_SELECTED = "plan_selected"
    ACTION_PROPOSED = "action_proposed"
    ACTION_BLOCKED_BY_SAFETY = "action_blocked_by_safety"
    ACTION_SENT = "action_sent"
    ACTION_REJECTED = "action_rejected"
    REWARD_ASSIGNED = "reward_assigned"
    BATTLE_FINISHED = "battle_finished"
    EPISODE_SAVED = "episode_saved"
    TRAINING_STARTED = "training_started"
    CANDIDATE_EVALUATED = "candidate_evaluated"
    MODEL_PROMOTED = "model_promoted"
    MODEL_REJECTED = "model_rejected"

    # Faits de combat
    UNIT_DESTROYED = "unit_destroyed"
    UNIT_ROUTED = "unit_routed"
    UNIT_ENGAGED = "unit_engaged"
    FLANK_ATTACK = "flank_attack"
    ORDER_CHANGED = "order_changed"
    EMERGENCY_STOP = "emergency_stop"
    DATA_QUALITY_ISSUE = "data_quality_issue"


@dataclass(frozen=True, slots=True)
class Event:
    """Fait date, rattache a une bataille."""

    type: EventType
    battle_id: str
    game_time: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    wall_clock: float = field(default_factory=time.time)

    @property
    def side(self) -> Side | None:
        raw = self.payload.get("side")
        if isinstance(raw, Side):
            return raw
        if isinstance(raw, str):
            try:
                return Side(raw)
            except ValueError:
                return None
        return None

    @property
    def role(self) -> UnitRole | None:
        raw = self.payload.get("role")
        if isinstance(raw, UnitRole):
            return raw
        if isinstance(raw, str):
            try:
                return UnitRole(raw)
            except ValueError:
                return None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "battle_id": self.battle_id,
            "game_time": self.game_time,
            "wall_clock": self.wall_clock,
            "payload": _jsonable(self.payload),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Event:
        return cls(
            type=EventType(raw["type"]),
            battle_id=str(raw["battle_id"]),
            game_time=float(raw.get("game_time", 0.0)),
            payload=dict(raw.get("payload") or {}),
            wall_clock=float(raw.get("wall_clock", 0.0)),
        )


def unit_event(
    event_type: EventType,
    battle_id: str,
    game_time: float,
    *,
    unit_id: str,
    side: Side,
    role: UnitRole,
    **extra: Any,
) -> Event:
    """Raccourci pour les evenements portant sur une unite."""
    payload: dict[str, Any] = {
        "unit_id": unit_id,
        "side": side.value,
        "role": role.value,
        **extra,
    }
    return Event(type=event_type, battle_id=battle_id, game_time=game_time, payload=payload)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value
