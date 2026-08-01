"""Protocole d'echange entre le jeu (mod Lua) et l'agent Python.

Le format est celui documente dans le README, section « Contrats d'interface ».
Regle de compatibilite : deux versions sont compatibles si leurs numeros majeur
et mineur sont identiques. Un correctif (patch) ne doit jamais casser le format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from totalwar_ai.domain.actions import ActionResult, AgentAction
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.serialization import (
    SchemaError,
    as_int,
    as_str,
    require_mapping,
)

PROTOCOL_VERSION = "0.1.0"


class MessageType(StrEnum):
    """Types de messages transportes par le pont."""

    BATTLE_STATE = "battle_state"
    AGENT_ACTIONS = "agent_actions"
    ACTION_RESULT = "action_result"


class IncompatibleProtocolVersionError(SchemaError):
    """Le message provient d'une version de protocole non supportee."""


def parse_version(version: str) -> tuple[int, int, int]:
    """Decoupe une version semantique `major.minor.patch`."""
    parts = version.split(".")
    if len(parts) != 3:
        raise SchemaError(f"Version de protocole malformee : {version!r}")
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError:
        raise SchemaError(f"Version de protocole malformee : {version!r}") from None
    return major, minor, patch


def is_compatible(version: str, reference: str = PROTOCOL_VERSION) -> bool:
    """Compatibilite majeure+mineure."""
    return parse_version(version)[:2] == parse_version(reference)[:2]


def check_version(version: str, reference: str = PROTOCOL_VERSION) -> None:
    """Leve :class:`IncompatibleProtocolVersionError` si les versions divergent."""
    if not is_compatible(version, reference):
        raise IncompatibleProtocolVersionError(
            f"Version de protocole {version} incompatible avec {reference}"
        )


@dataclass(frozen=True, slots=True)
class BattleStateMessage:
    """Etat de bataille pousse par le jeu vers l'agent."""

    state: BattleState
    protocol_version: str = PROTOCOL_VERSION

    @property
    def message_type(self) -> MessageType:
        return MessageType.BATTLE_STATE

    def to_dict(self) -> dict[str, Any]:
        payload = self.state.to_dict()
        return {
            "protocol_version": self.protocol_version,
            "message_type": MessageType.BATTLE_STATE.value,
            "battle_id": self.state.battle_id,
            "sequence": self.state.sequence,
            "game_time": self.state.game_time,
            "payload": {
                "phase": payload["phase"],
                "units": payload["units"],
                "objectives": payload["objectives"],
                "metadata": payload["metadata"],
            },
        }

    @classmethod
    def from_dict(cls, raw: Any) -> BattleStateMessage:
        data = require_mapping(raw, "BattleStateMessage")
        version = as_str(data, "protocol_version")
        check_version(version)
        payload = require_mapping(data.get("payload", {}), "payload")
        state = BattleState.from_dict(
            {
                "battle_id": as_str(data, "battle_id"),
                "sequence": as_int(data, "sequence", default=0),
                "game_time": data.get("game_time", 0.0),
                "phase": payload.get("phase"),
                "units": payload.get("units", []),
                "objectives": payload.get("objectives", []),
                "metadata": payload.get("metadata", {}),
            }
        )
        return cls(state=state, protocol_version=version)


@dataclass(frozen=True, slots=True)
class AgentActionsMessage:
    """Lot d'actions emis par l'agent vers le jeu."""

    battle_id: str
    sequence: int
    actions: tuple[AgentAction, ...] = ()
    protocol_version: str = PROTOCOL_VERSION

    @property
    def message_type(self) -> MessageType:
        return MessageType.AGENT_ACTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": MessageType.AGENT_ACTIONS.value,
            "battle_id": self.battle_id,
            "sequence": self.sequence,
            "actions": [action.to_dict() for action in self.actions],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> AgentActionsMessage:
        data = require_mapping(raw, "AgentActionsMessage")
        version = as_str(data, "protocol_version")
        check_version(version)
        raw_actions = data.get("actions") or []
        if not isinstance(raw_actions, list):
            raise SchemaError("Le champ 'actions' doit etre une liste")
        return cls(
            battle_id=as_str(data, "battle_id"),
            sequence=as_int(data, "sequence", default=0),
            actions=tuple(AgentAction.from_dict(item) for item in raw_actions),
            protocol_version=version,
        )


@dataclass(frozen=True, slots=True)
class ActionResultMessage:
    """Accuse d'execution renvoye par le jeu."""

    battle_id: str
    result: ActionResult
    protocol_version: str = PROTOCOL_VERSION

    @property
    def message_type(self) -> MessageType:
        return MessageType.ACTION_RESULT

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_type": MessageType.ACTION_RESULT.value,
            "battle_id": self.battle_id,
            "action_id": self.result.action_id,
            "status": self.result.status.value,
            "error": self.result.error,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ActionResultMessage:
        data = require_mapping(raw, "ActionResultMessage")
        version = as_str(data, "protocol_version")
        check_version(version)
        return cls(
            battle_id=as_str(data, "battle_id"),
            result=ActionResult.from_dict(
                {
                    "action_id": as_str(data, "action_id"),
                    "status": data.get("status"),
                    "error": data.get("error"),
                }
            ),
            protocol_version=version,
        )


Message = BattleStateMessage | AgentActionsMessage | ActionResultMessage

_DECODERS: dict[str, Any] = {
    MessageType.BATTLE_STATE.value: BattleStateMessage.from_dict,
    MessageType.AGENT_ACTIONS.value: AgentActionsMessage.from_dict,
    MessageType.ACTION_RESULT.value: ActionResultMessage.from_dict,
}


def decode_message(raw: Any) -> Message:
    """Decode un message quelconque en s'appuyant sur `message_type`."""
    data = require_mapping(raw, "message")
    message_type = as_str(data, "message_type")
    decoder = _DECODERS.get(message_type)
    if decoder is None:
        known = ", ".join(sorted(_DECODERS))
        raise SchemaError(f"Type de message inconnu : {message_type!r} (attendu : {known})")
    decoded: Message = decoder(data)
    return decoded


def encode_message(message: Message) -> dict[str, Any]:
    """Encode un message en dictionnaire JSON-compatible."""
    return message.to_dict()


@dataclass(frozen=True, slots=True)
class ProtocolInfo:
    """Descripteur de version, joint a chaque episode enregistre."""

    protocol_version: str = PROTOCOL_VERSION
    supported_message_types: tuple[str, ...] = field(
        default=tuple(member.value for member in MessageType)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "supported_message_types": list(self.supported_message_types),
        }
