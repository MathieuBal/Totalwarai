"""Apprentissage.

Pour l'instant, seul le systeme de recompense est implemente : il alimente la
memoire d'experience. L'entraineur et l'evaluateur (Phases 5 et 6 du README)
viendront lorsqu'il y aura assez de batailles enregistrees pour en tirer
quelque chose.
"""

from totalwar_ai.learning.rewards import RewardBreakdown, RewardCalculator, RewardConfig

__all__ = ["RewardBreakdown", "RewardCalculator", "RewardConfig"]
