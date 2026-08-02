"""Messages du prototype d'integration au jeu.

Ce protocole est **volontairement plus pauvre** que celui de
:mod:`totalwar_ai.bridge.protocol` : il ne transporte qu'une unite et un ordre
de deplacement. C'est une sonde de faisabilite, pas l'adaptateur final — son
seul but est de prouver l'aller-retour jeu <-> Python sur le plus petit
perimetre possible.

Les deux protocoles cohabitent tant que la sonde n'a pas repondu a la question
« que peut-on reellement observer et commander ? ». `ProbeUnitState.to_unit_state`
montre le raccord vers le domaine complet, une fois cette reponse obtenue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from totalwar_ai.bridge.protocol import PROTOCOL_VERSION, check_version
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.serialization import (
    SchemaError,
    as_bool,
    as_enum,
    as_int,
    as_optional_str,
    as_str,
    require_mapping,
)
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState

#: Precision des coordonnees ecrites vers le Lua, en decimales.
#:
#: L'analyseur du script Lua lit les nombres avec le motif `(-?%d+%.?%d*)`, qui
#: ne comprend pas la notation scientifique : `1e-05` y serait lu comme `1`,
#: silencieusement et faussement. Arrondir avant d'ecrire elimine le cas —
#: le millimetre est de toute facon sans objet sur un champ de bataille.
COORDINATE_PRECISION = 3


def jsonable_vector(vector: Vector3) -> dict[str, float]:
    """Vecteur serialisable sans risque de notation scientifique."""
    return {
        "x": round(vector.x, COORDINATE_PRECISION),
        "y": round(vector.y, COORDINATE_PRECISION),
        "z": round(vector.z, COORDINATE_PRECISION),
    }


class ProbeMessageType(StrEnum):
    """Types de messages echanges par la sonde."""

    UNIT_STATE = "unit_state"
    MOVE_UNIT = "move_unit"
    ACTION_RESULT = "action_result"
    #: Ordre d'arret : le Lua libere toutes ses unites et cesse de lire.
    ABORT = "abort"


class ProbeStatus(StrEnum):
    """Sort d'une commande, du point de vue du Lua."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class ProbeUnitState:
    """Etat d'une unite, tel que le Lua l'observe."""

    unit_id: str
    position: Vector3
    unit_type: str = ""
    controllable: bool = False
    sequence: int = 0
    game_time_ms: int = 0
    #: Phase de bataille annoncee par le jeu au moment de l'observation.
    #: Vide si la sonde ne l'a pas encore transmise (protocole anterieur).
    phase: str = ""
    protocol_version: str = PROTOCOL_VERSION

    @property
    def orders_take_effect(self) -> bool:
        """Un ordre emis maintenant peut-il produire un deplacement ?

        Avant `Deployed`, le moteur accepte l'ordre et l'acquitte, mais l'unite
        ne bouge pas — constate en jeu, immobile 33 s durant apres un ordre
        accepte. Une phase inconnue est traitee comme jouable : c'est le cas
        d'une sonde plus ancienne, ou l'on ne veut pas bloquer a tort.
        """
        return self.phase in ("", "unknown", "Deployed", "Complete")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.UNIT_STATE.value,
            "sequence": self.sequence,
            "game_time_ms": self.game_time_ms,
            "phase": self.phase,
            "unit": {
                "id": self.unit_id,
                "type": self.unit_type,
                "position": jsonable_vector(self.position),
                "controllable": self.controllable,
            },
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeUnitState:
        data = require_mapping(raw, "ProbeUnitState")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.UNIT_STATE)
        unit = require_mapping(data.get("unit"), "unit")
        return cls(
            unit_id=as_str(unit, "id"),
            position=Vector3.from_dict(unit.get("position", {})),
            unit_type=as_str(unit, "type", default=""),
            controllable=as_bool(unit, "controllable", default=False),
            sequence=as_int(data, "sequence", default=0),
            game_time_ms=as_int(data, "game_time_ms", default=0),
            phase=as_str(data, "phase", default=""),
            protocol_version=version,
        )

    def to_unit_state(self) -> UnitState:
        """Traduction vers le domaine complet.

        La sonde n'observe qu'une fraction de `UnitState` ; le reste garde ses
        valeurs par defaut. C'est ce raccord qui permettra a l'agent existant de
        consommer un jour les donnees du vrai jeu sans etre modifie.
        """
        return UnitState(
            id=self.unit_id,
            side=Side.ALLY,
            role=UnitRole.UNKNOWN,
            position=self.position,
            unit_key=self.unit_type,
            metadata={"controllable": self.controllable, "source": "probe"},
        )


@dataclass(frozen=True, slots=True)
class ProbeMoveCommand:
    """Ordre de deplacement envoye par Python."""

    unit_id: str
    destination: Vector3
    sequence: int = 1
    #: Duree au bout de laquelle le Lua rend la main, meme si l'unite marche encore.
    release_after_ms: int = 5000
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not self.unit_id:
            raise SchemaError("Une commande de deplacement doit designer une unite")
        if self.sequence < 1:
            raise SchemaError("Le numero de sequence doit etre superieur ou egal a 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.MOVE_UNIT.value,
            "sequence": self.sequence,
            "unit_id": self.unit_id,
            "destination": jsonable_vector(self.destination),
            "release_after_ms": self.release_after_ms,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeMoveCommand:
        data = require_mapping(raw, "ProbeMoveCommand")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.MOVE_UNIT)
        return cls(
            unit_id=as_str(data, "unit_id"),
            destination=Vector3.from_dict(require_mapping(data.get("destination"), "destination")),
            sequence=as_int(data, "sequence", default=1),
            release_after_ms=as_int(data, "release_after_ms", default=5000),
            protocol_version=version,
        )


@dataclass(frozen=True, slots=True)
class ProbeAbortCommand:
    """Arret d'urgence : le Lua libere tout et cesse de lire les commandes."""

    sequence: int = 1
    reason: str = ""
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.ABORT.value,
            "sequence": self.sequence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeAbortCommand:
        data = require_mapping(raw, "ProbeAbortCommand")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.ABORT)
        return cls(
            sequence=as_int(data, "sequence", default=1),
            reason=as_str(data, "reason", default=""),
            protocol_version=version,
        )


@dataclass(frozen=True, slots=True)
class ProbeAck:
    """Accuse d'execution renvoye par le Lua."""

    sequence: int
    status: ProbeStatus
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    protocol_version: str = PROTOCOL_VERSION

    @property
    def accepted(self) -> bool:
        return self.status in (ProbeStatus.ACCEPTED, ProbeStatus.COMPLETED, ProbeStatus.RELEASED)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.ACTION_RESULT.value,
            "sequence": self.sequence,
            "status": self.status.value,
            "error": self.error,
        }
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeAck:
        data = require_mapping(raw, "ProbeAck")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.ACTION_RESULT)
        return cls(
            sequence=as_int(data, "sequence"),
            status=as_enum(data, "status", ProbeStatus),
            error=as_optional_str(data, "error"),
            detail=dict(data.get("detail") or {}),
            protocol_version=version,
        )


ProbeCommand = ProbeMoveCommand | ProbeAbortCommand


def decode_command(raw: Any) -> ProbeCommand:
    """Decode une commande quelconque en s'appuyant sur son champ `type`."""
    data = require_mapping(raw, "commande")
    message_type = as_str(data, "type")
    if message_type == ProbeMessageType.MOVE_UNIT.value:
        return ProbeMoveCommand.from_dict(data)
    if message_type == ProbeMessageType.ABORT.value:
        return ProbeAbortCommand.from_dict(data)
    raise SchemaError(f"Type de commande inconnu : {message_type!r}")


def _require_type(data: Any, expected: ProbeMessageType) -> None:
    actual = as_str(data, "type")
    if actual != expected.value:
        raise SchemaError(f"Message de type {actual!r} la ou {expected.value!r} etait attendu")
