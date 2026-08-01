"""Espace d'actions tactiques haut niveau.

L'agent ne clique jamais sur des coordonnees d'ecran : il emet des intentions
que l'adaptateur (simulateur aujourd'hui, mod Lua demain) traduit en ordres
reellement disponibles.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any

from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.serialization import (
    SchemaError,
    as_enum,
    as_mapping,
    as_optional_str,
    as_ratio,
    as_str,
    as_str_list,
    require_mapping,
)


class ActionType(StrEnum):
    """Actions haut niveau reconnues par le protocole (voir README)."""

    HOLD_POSITION = "HOLD_POSITION"
    MOVE_GROUP = "MOVE_GROUP"
    ATTACK_TARGET = "ATTACK_TARGET"
    FOCUS_FIRE = "FOCUS_FIRE"
    PROTECT = "PROTECT"
    FLANK = "FLANK"
    RETREAT = "RETREAT"
    DISENGAGE = "DISENGAGE"
    CHASE_ROUTING = "CHASE_ROUTING"
    FORM_RESERVE = "FORM_RESERVE"
    REORIENT_FRONT = "REORIENT_FRONT"


class Formation(StrEnum):
    """Formations demandables lors d'un deplacement de groupe."""

    LINE = "line"
    LOOSE = "loose"
    COLUMN = "column"
    WEDGE = "wedge"


class ActionStatus(StrEnum):
    """Sort d'une action une fois soumise."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    FAILED = "failed"


#: Actions qui envoient physiquement des unites au contact.
CHARGE_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.ATTACK_TARGET, ActionType.FLANK, ActionType.CHASE_ROUTING}
)


def new_action_id() -> str:
    """Identifiant d'action unique (UUID4 textuel)."""
    return str(uuid.uuid4())


@dataclass(frozen=True)
class AgentAction:
    """Intention tactique produite par l'agent.

    `reason` et `confidence` ne sont pas decoratifs : ils alimentent
    l'explicabilite, les rapports et le seuil de confiance de la configuration.
    """

    type: ActionType
    actor_ids: tuple[str, ...]
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.5
    action_id: str = field(default_factory=new_action_id)

    def __post_init__(self) -> None:
        if not self.actor_ids:
            raise SchemaError(f"L'action {self.type.value} doit designer au moins une unite")
        if not 0.0 <= self.confidence <= 1.0:
            raise SchemaError(f"Confiance hors bornes : {self.confidence}")

    @property
    def is_charge(self) -> bool:
        return self.type in CHARGE_ACTIONS

    @property
    def target_id(self) -> str | None:
        value = self.parameters.get("target_id")
        return value if isinstance(value, str) else None

    @property
    def destination(self) -> Vector3 | None:
        raw = self.parameters.get("destination")
        return Vector3.from_dict(raw) if raw is not None else None

    def signature(self) -> tuple[Any, ...]:
        """Empreinte stable d'un ordre.

        Deux actions de meme signature representent le meme ordre : l'agent s'en
        sert pour ne pas re-emettre en boucle un ordre deja donne (penalite
        `unnecessary_order_change`).
        """
        destination = self.destination
        rounded = (
            (round(destination.x, 0), round(destination.z, 0)) if destination is not None else None
        )
        # Le cap ne compte que pour une reorientation, dont il est tout l'objet :
        # sans lui, deux pivots opposes auraient la meme empreinte et le second
        # serait avale comme un doublon. Pour les autres ordres, c'est du bruit —
        # l'inclure ferait re-emettre l'ordre au moindre frémissement de la cible.
        heading = self.parameters.get("heading")
        heading_bucket = (
            round(float(heading) / 0.25)
            if self.type is ActionType.REORIENT_FRONT and isinstance(heading, int | float)
            else None
        )
        return (
            self.type.value,
            tuple(sorted(self.actor_ids)),
            self.target_id,
            rounded,
            heading_bucket,
        )

    def with_reason(self, reason: str) -> AgentAction:
        return AgentAction(
            type=self.type,
            actor_ids=self.actor_ids,
            parameters=dict(self.parameters),
            reason=reason,
            confidence=self.confidence,
            action_id=self.action_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "type": self.type.value,
            "actor_ids": list(self.actor_ids),
            "parameters": _serialise_parameters(self.parameters),
            "reason": self.reason,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> AgentAction:
        data = require_mapping(raw, "AgentAction")
        return cls(
            type=as_enum(data, "type", ActionType),
            actor_ids=tuple(as_str_list(data, "actor_ids")),
            parameters=as_mapping(data, "parameters"),
            reason=as_str(data, "reason", default=""),
            confidence=as_ratio(data, "confidence", default=0.5),
            action_id=as_str(data, "action_id", default=new_action_id()),
        )


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Accuse d'execution renvoye par l'adaptateur."""

    action_id: str
    status: ActionStatus
    error: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status is ActionStatus.ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": self.status.value,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ActionResult:
        data = require_mapping(raw, "ActionResult")
        return cls(
            action_id=as_str(data, "action_id"),
            status=as_enum(data, "status", ActionStatus),
            error=as_optional_str(data, "error"),
        )


def _serialise_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Convertit les valeurs non JSON (vecteurs, enums) des parametres."""
    result: dict[str, Any] = {}
    for key, value in parameters.items():
        if isinstance(value, Vector3):
            result[key] = value.to_dict()
        elif isinstance(value, Enum):
            result[key] = value.value
        else:
            result[key] = value
    return result
