"""Pont entre le jeu et l'agent : protocole versionne et adaptateurs."""

from totalwar_ai.bridge.base import Bridge, BridgeError
from totalwar_ai.bridge.mock_bridge import MockBridge, always_accept, always_reject
from totalwar_ai.bridge.protocol import (
    PROTOCOL_VERSION,
    ActionResultMessage,
    AgentActionsMessage,
    BattleStateMessage,
    IncompatibleProtocolVersionError,
    MessageType,
    check_version,
    decode_message,
    encode_message,
    is_compatible,
)

__all__ = [
    "PROTOCOL_VERSION",
    "ActionResultMessage",
    "AgentActionsMessage",
    "BattleStateMessage",
    "Bridge",
    "BridgeError",
    "IncompatibleProtocolVersionError",
    "MessageType",
    "MockBridge",
    "always_accept",
    "always_reject",
    "check_version",
    "decode_message",
    "encode_message",
    "is_compatible",
]
