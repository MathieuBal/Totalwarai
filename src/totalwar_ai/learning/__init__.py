"""Apprentissage.

Deux briques aujourd'hui : le systeme de recompense, et l'adaptation de la
doctrine a partir de l'historique (Phase 4 du README). L'entraineur et
l'evaluateur de modeles (Phases 5 et 6) viendront lorsqu'il y aura assez de
batailles enregistrees pour en tirer quelque chose.
"""

from totalwar_ai.learning.adaptation import (
    ADJUSTABLES,
    DoctrineProfile,
    HistoryStats,
    derive_profile,
)
from totalwar_ai.learning.checkpoints import CheckpointStore
from totalwar_ai.learning.rewards import RewardBreakdown, RewardCalculator, RewardConfig

__all__ = [
    "ADJUSTABLES",
    "CheckpointStore",
    "DoctrineProfile",
    "HistoryStats",
    "RewardBreakdown",
    "RewardCalculator",
    "RewardConfig",
    "derive_profile",
]
