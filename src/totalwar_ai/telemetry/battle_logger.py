"""Journalisation structuree d'une bataille.

Deux sorties complementaires :

* un fichier JSONL par bataille, exploitable par les outils d'analyse ;
* le `logging` standard, pour suivre la partie en direct.

Le journal detecte aussi les etats incomplets : un pont Lua bavard mais mal
renseigne doit se voir, pas se deviner.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from totalwar_ai.agent.explainability import Decision
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.telemetry.events import Event, EventType

LOGGER = logging.getLogger("totalwar_ai.battle")


class BattleLogger:
    """Ecrit les evenements d'une bataille et garde le fil en memoire."""

    def __init__(
        self,
        battle_id: str,
        *,
        directory: str | Path | None = None,
        write_jsonl: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self.battle_id = battle_id
        self.events: list[Event] = []
        self.logger = logger or LOGGER
        self.path: Path | None = None
        self._handle: TextIO | None = None
        if write_jsonl and directory is not None:
            base = Path(directory)
            base.mkdir(parents=True, exist_ok=True)
            self.path = base / f"{battle_id}.jsonl"
            self._handle = self.path.open("w", encoding="utf-8")

    # --- cycle de vie --------------------------------------------------------

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> BattleLogger:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # --- ecriture ------------------------------------------------------------

    def emit(self, event: Event) -> Event:
        """Journalise un evenement deja construit."""
        self.events.append(event)
        if self._handle is not None:
            self._handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            self._handle.flush()
        self.logger.debug("%s %s", event.type.value, event.payload)
        return event

    def emit_many(self, events: Iterable[Event]) -> None:
        for event in events:
            self.emit(event)

    def log(self, event_type: EventType, game_time: float = 0.0, **payload: Any) -> Event:
        """Construit puis journalise un evenement."""
        return self.emit(
            Event(
                type=event_type,
                battle_id=self.battle_id,
                game_time=game_time,
                payload=payload,
            )
        )

    # --- raccourcis metier ---------------------------------------------------

    def battle_started(self, state: BattleState, **payload: Any) -> None:
        self.log(EventType.BATTLE_STARTED, state.game_time, **payload)
        for unit in state.units:
            self.log(
                EventType.UNIT_DISCOVERED,
                state.game_time,
                unit_id=unit.id,
                side=unit.side.value,
                role=unit.role.value,
            )

    def state_received(self, state: BattleState) -> None:
        self.log(
            EventType.STATE_RECEIVED,
            state.game_time,
            sequence=state.sequence,
            phase=state.phase.value,
            units=len(state.units),
        )
        for issue in detect_data_issues(state):
            self.log(EventType.DATA_QUALITY_ISSUE, state.game_time, **issue)

    def plan_selected(self, game_time: float, plan: dict[str, Any]) -> None:
        self.log(EventType.PLAN_SELECTED, game_time, **plan)

    def decisions(self, game_time: float, allowed: Sequence[Decision], blocked: Sequence[Decision]) -> None:
        """Journalise les ordres proposes, envoyes et refuses."""
        for decision in allowed:
            payload = {
                "action_id": decision.action.action_id,
                "type": decision.action.type.value,
                "actors": list(decision.action.actor_ids),
                "cause": decision.cause,
                "objective": decision.objective,
                "confidence": decision.action.confidence,
            }
            self.log(EventType.ACTION_PROPOSED, game_time, **payload)
            self.log(EventType.ACTION_SENT, game_time, **payload)
        for decision in blocked:
            self.log(
                EventType.ACTION_BLOCKED_BY_SAFETY,
                game_time,
                action_id=decision.action.action_id,
                type=decision.action.type.value,
                actors=list(decision.action.actor_ids),
                rule=decision.blocked_by,
                cause=decision.cause,
                replacement=decision.replacement.type.value if decision.replacement else None,
            )

    def action_rejected(self, game_time: float, action_id: str, error: str | None) -> None:
        self.log(EventType.ACTION_REJECTED, game_time, action_id=action_id, error=error)

    def reward_assigned(self, game_time: float, total: float, components: dict[str, float]) -> None:
        self.log(EventType.REWARD_ASSIGNED, game_time, total=total, components=components)

    def episode_saved(self, game_time: float, **payload: Any) -> None:
        self.log(EventType.EPISODE_SAVED, game_time, **payload)


def detect_data_issues(state: BattleState) -> list[dict[str, Any]]:
    """Reperage des donnees manifestement incompletes.

    Ne leve jamais : un etat degrade doit rester exploitable, mais tracable.
    """
    issues: list[dict[str, Any]] = []
    if not state.units:
        issues.append({"issue": "etat sans aucune unite", "sequence": state.sequence})
    for unit in state.units:
        if unit.role.value == "unknown":
            issues.append({"issue": "role inconnu", "unit_id": unit.id})
        if unit.is_ranged and unit.ammo_ratio == 0.0 and unit.health_ratio == 1.0:
            issues.append({"issue": "unite de tir sans munitions declarees", "unit_id": unit.id})
    return issues


def configure_logging(level: str = "INFO") -> None:
    """Configuration minimale, appelee par le CLI."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s : %(message)s",
    )
