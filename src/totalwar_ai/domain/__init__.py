"""Contrats de donnees partages entre l'agent, le pont et le simulateur."""

from totalwar_ai.domain.actions import (
    CHARGE_ACTIONS,
    ActionResult,
    ActionStatus,
    ActionType,
    AgentAction,
    Formation,
    new_action_id,
)
from totalwar_ai.domain.battle_state import BattleOutcomeKind, BattlePhase, BattleState
from totalwar_ai.domain.geometry import Vector3, centroid, is_in_rear_arc, spread_positions
from totalwar_ai.domain.serialization import SchemaError, clamp
from totalwar_ai.domain.unit_state import (
    LINE_ROLES,
    MOBILE_ROLES,
    PRECIOUS_ROLES,
    RANGED_ROLES,
    Side,
    UnitRole,
    UnitState,
)

__all__ = [
    "CHARGE_ACTIONS",
    "LINE_ROLES",
    "MOBILE_ROLES",
    "PRECIOUS_ROLES",
    "RANGED_ROLES",
    "ActionResult",
    "ActionStatus",
    "ActionType",
    "AgentAction",
    "BattleOutcomeKind",
    "BattlePhase",
    "BattleState",
    "Formation",
    "SchemaError",
    "Side",
    "UnitRole",
    "UnitState",
    "Vector3",
    "centroid",
    "clamp",
    "is_in_rear_arc",
    "new_action_id",
    "spread_positions",
]
