"""Agent tactique : classification, groupes, plan, ordres et securite."""

from totalwar_ai.agent.explainability import Decision, describe_action
from totalwar_ai.agent.grouping import GroupKind, GroupSet, TacticalGroup, build_groups
from totalwar_ai.agent.planner import BattlePlan, Planner, PlannerSettings, Posture
from totalwar_ai.agent.safety_rules import (
    SafetyEngine,
    SafetyOutcome,
    SafetyRule,
    SafetySettings,
    SafetyVerdict,
)
from totalwar_ai.agent.tactical_agent import AgentTurn, DeterministicTacticalAgent
from totalwar_ai.agent.unit_classifier import ClassificationRule, UnitClassifier

__all__ = [
    "AgentTurn",
    "BattlePlan",
    "ClassificationRule",
    "Decision",
    "DeterministicTacticalAgent",
    "GroupKind",
    "GroupSet",
    "Planner",
    "PlannerSettings",
    "Posture",
    "SafetyEngine",
    "SafetyOutcome",
    "SafetyRule",
    "SafetySettings",
    "SafetyVerdict",
    "TacticalGroup",
    "UnitClassifier",
    "build_groups",
    "describe_action",
]
