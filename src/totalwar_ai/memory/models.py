"""Structures persistees : transitions, resume de bataille, episode."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from totalwar_ai.domain.battle_state import BattleOutcomeKind, BattleState
from totalwar_ai.domain.unit_state import Side
from totalwar_ai.telemetry.events import Event


@dataclass(frozen=True, slots=True)
class Transition:
    """Experience elementaire, au format decrit dans le README."""

    battle_id: str
    sequence: int
    game_time: float
    state: dict[str, Any]
    action: dict[str, Any] | None
    reward: float
    next_state: dict[str, Any]
    done: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        previous: BattleState,
        current: BattleState,
        *,
        actions: list[dict[str, Any]],
        reward: float,
        done: bool,
        metadata: dict[str, Any] | None = None,
    ) -> Transition:
        return cls(
            battle_id=current.battle_id,
            sequence=current.sequence,
            game_time=current.game_time,
            state=previous.to_dict(),
            action={"actions": actions} if actions else None,
            reward=reward,
            next_state=current.to_dict(),
            done=done,
            metadata=dict(metadata or {}),
        )

    def compact(self) -> Transition:
        """Version allegee : conserve les mesures, pas le detail des unites.

        Utilisee quand `memory.keep_raw_battles` est desactive.
        """
        return Transition(
            battle_id=self.battle_id,
            sequence=self.sequence,
            game_time=self.game_time,
            state=_digest(self.state),
            action=self.action,
            reward=self.reward,
            next_state=_digest(self.next_state),
            done=self.done,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "battle_id": self.battle_id,
            "sequence": self.sequence,
            "game_time": self.game_time,
            "state": self.state,
            "action": self.action,
            "reward": self.reward,
            "next_state": self.next_state,
            "done": self.done,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Transition:
        return cls(
            battle_id=str(raw["battle_id"]),
            sequence=int(raw.get("sequence", 0)),
            game_time=float(raw.get("game_time", 0.0)),
            state=dict(raw.get("state") or {}),
            action=dict(raw["action"]) if raw.get("action") else None,
            reward=float(raw.get("reward", 0.0)),
            next_state=dict(raw.get("next_state") or {}),
            done=bool(raw.get("done", False)),
            metadata=dict(raw.get("metadata") or {}),
        )


def _digest(state: dict[str, Any]) -> dict[str, Any]:
    units = state.get("units") or []
    return {
        "battle_id": state.get("battle_id"),
        "sequence": state.get("sequence"),
        "game_time": state.get("game_time"),
        "phase": state.get("phase"),
        "unit_count": len(units),
        "ally_count": sum(1 for unit in units if unit.get("side") == Side.ALLY.value),
        "enemy_count": sum(1 for unit in units if unit.get("side") == Side.ENEMY.value),
    }


@dataclass(frozen=True, slots=True)
class BattleSummary:
    """Fiche d'une bataille : ce qui permet de la retrouver et de la comparer."""

    battle_id: str
    scenario: str = ""
    seed: int = 0
    outcome: BattleOutcomeKind = BattleOutcomeKind.UNKNOWN
    duration: float = 0.0
    ally_remaining: float = 0.0
    enemy_remaining: float = 0.0
    total_reward: float = 0.0
    actions_sent: int = 0
    actions_blocked: int = 0
    actions_rejected: int = 0
    agent_mode: str = "deterministic"
    protocol_version: str = ""
    reward_version: str = ""
    army_fingerprint: str = ""
    created_at: float = field(default_factory=time.time)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "battle_id": self.battle_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "outcome": self.outcome.value,
            "duration": self.duration,
            "ally_remaining": self.ally_remaining,
            "enemy_remaining": self.enemy_remaining,
            "total_reward": self.total_reward,
            "actions_sent": self.actions_sent,
            "actions_blocked": self.actions_blocked,
            "actions_rejected": self.actions_rejected,
            "agent_mode": self.agent_mode,
            "protocol_version": self.protocol_version,
            "reward_version": self.reward_version,
            "army_fingerprint": self.army_fingerprint,
            "created_at": self.created_at,
            "metrics": self.metrics,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BattleSummary:
        return cls(
            battle_id=str(raw["battle_id"]),
            scenario=str(raw.get("scenario", "")),
            seed=int(raw.get("seed", 0)),
            outcome=BattleOutcomeKind(raw.get("outcome", BattleOutcomeKind.UNKNOWN.value)),
            duration=float(raw.get("duration", 0.0)),
            ally_remaining=float(raw.get("ally_remaining", 0.0)),
            enemy_remaining=float(raw.get("enemy_remaining", 0.0)),
            total_reward=float(raw.get("total_reward", 0.0)),
            actions_sent=int(raw.get("actions_sent", 0)),
            actions_blocked=int(raw.get("actions_blocked", 0)),
            actions_rejected=int(raw.get("actions_rejected", 0)),
            agent_mode=str(raw.get("agent_mode", "deterministic")),
            protocol_version=str(raw.get("protocol_version", "")),
            reward_version=str(raw.get("reward_version", "")),
            army_fingerprint=str(raw.get("army_fingerprint", "")),
            created_at=float(raw.get("created_at", 0.0)),
            metrics=dict(raw.get("metrics") or {}),
        )


@dataclass(slots=True)
class Episode:
    """Bataille complete : resume, transitions et evenements."""

    summary: BattleSummary
    transitions: list[Transition] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    @property
    def battle_id(self) -> str:
        return self.summary.battle_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "transitions": [transition.to_dict() for transition in self.transitions],
            "events": [event.to_dict() for event in self.events],
        }
