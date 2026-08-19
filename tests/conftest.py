"""Fixtures partagees par toute la suite de tests."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.config import AppConfig, load_config
from totalwar_ai.domain.battle_state import BattlePhase, BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import RANGED_ROLES, Side, UnitRole, UnitState


def unit(
    unit_id: str,
    side: Side,
    role: UnitRole,
    x: float = 0.0,
    z: float = 0.0,
    **overrides: Any,
) -> UnitState:
    """Unite de test : les unites de tir ont des munitions et une portee.

    **`missile_range` vaut 0 sur qui ne tire pas**, comme en jeu — verifie en
    bataille sur un prince demon. Le donner a tout le monde faisait passer une
    infanterie de melee pour une plateforme de tir, ce qu'aucun corps de bataille
    reel ne produit.
    """
    defaults: dict[str, Any] = {
        "ammo_ratio": 1.0 if role in RANGED_ROLES else 0.0,
        "metadata": {"missile_range": 120.0 if role in RANGED_ROLES else 0.0},
    }
    defaults.update(overrides)
    return UnitState(
        id=unit_id,
        side=side,
        role=role,
        position=Vector3(x, 0.0, z),
        **defaults,
    )


def battle(
    units: Sequence[UnitState],
    *,
    phase: BattlePhase = BattlePhase.ENGAGEMENT,
    game_time: float = 30.0,
    sequence: int = 10,
    battle_id: str = "test-battle",
) -> BattleState:
    """Etat de bataille synthetique."""
    return BattleState(
        battle_id=battle_id,
        sequence=sequence,
        game_time=game_time,
        phase=phase,
        units=tuple(units),
    )


@pytest.fixture
def make_unit():  # type: ignore[no-untyped-def]
    return unit


@pytest.fixture
def make_battle():  # type: ignore[no-untyped-def]
    return battle


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    """Configuration standard, mais dont toutes les donnees vont dans `tmp_path`."""
    return load_config(data_dir=tmp_path)


@pytest.fixture
def agent(config: AppConfig) -> DeterministicTacticalAgent:
    return DeterministicTacticalAgent.from_config(config)
