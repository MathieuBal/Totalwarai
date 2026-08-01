"""Systeme de recompense : evenements, composantes continues et issue."""

from __future__ import annotations

import pytest

from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.learning.rewards import RewardBreakdown, RewardCalculator, RewardConfig
from totalwar_ai.telemetry.events import Event, EventType, unit_event


@pytest.fixture
def calculator() -> RewardCalculator:
    return RewardCalculator(RewardConfig.default())


def _event(event_type: EventType, side: Side, role: UnitRole, unit_id: str = "u") -> Event:
    return unit_event(event_type, "b", 10.0, unit_id=unit_id, side=side, role=role)


def test_bareme_charge_depuis_le_depot() -> None:
    config = RewardConfig.load()
    assert config.terminal["victory"] > 0
    assert config.events["allied_unit_destroyed"] < 0
    assert config.version


def test_unite_ennemie_detruite_recompensee(
    calculator: RewardCalculator, make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    events = [_event(EventType.UNIT_DESTROYED, Side.ENEMY, UnitRole.MELEE_INFANTRY)]
    reward = calculator.step_reward(state, state, events)
    assert reward.components["enemy_unit_destroyed"] == pytest.approx(100.0)


def test_mort_du_seigneur_penalisee(calculator: RewardCalculator, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    events = [_event(EventType.UNIT_DESTROYED, Side.ALLY, UnitRole.LORD)]
    reward = calculator.step_reward(state, state, events)
    assert reward.components["lord_killed"] == pytest.approx(-250.0)
    assert "allied_unit_destroyed" not in reward.components


def test_tireur_pris_en_melee_penalise(
    calculator: RewardCalculator, make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    archers = calculator.step_reward(
        state, state, [_event(EventType.UNIT_ENGAGED, Side.ALLY, UnitRole.RANGED_INFANTRY)]
    )
    artillery = calculator.step_reward(
        state, state, [_event(EventType.UNIT_ENGAGED, Side.ALLY, UnitRole.ARTILLERY)]
    )
    assert archers.components["ranged_unit_caught_in_melee"] < 0
    assert artillery.components["artillery_caught_in_melee"] < archers.total


def test_infanterie_en_melee_neutre(calculator: RewardCalculator, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Engager l'infanterie est le but du jeu : aucune penalite."""
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    reward = calculator.step_reward(
        state, state, [_event(EventType.UNIT_ENGAGED, Side.ALLY, UnitRole.MELEE_INFANTRY)]
    )
    assert reward.total == pytest.approx(0.0)


def test_flanquement_allie_recompense(calculator: RewardCalculator, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    reward = calculator.step_reward(
        state, state, [_event(EventType.FLANK_ATTACK, Side.ALLY, UnitRole.SHOCK_CAVALRY)]
    )
    assert reward.components["successful_flank"] > 0


def test_blocage_de_securite_penalise(calculator: RewardCalculator, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    event = Event(type=EventType.ACTION_BLOCKED_BY_SAFETY, battle_id="b", payload={"rule": "x"})
    assert calculator.step_reward(state, state, [event]).total < 0


def test_usure_des_deux_camps(calculator: RewardCalculator, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    before = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 30.0),
        ],
        game_time=10.0,
    )
    after = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit(
                "e1",
                Side.ENEMY,
                UnitRole.MELEE_INFANTRY,
                0.0,
                30.0,
                entity_ratio=0.5,
                health_ratio=0.5,
            ),
        ],
        game_time=11.0,
    )
    reward = calculator.step_reward(before, after, [])
    assert reward.components["enemy_strength_lost"] > 0
    assert "allied_strength_lost" not in reward.components


def test_unite_isolee_penalisee_par_seconde(
    calculator: RewardCalculator, make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    before = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 50.0),
        ],
        game_time=10.0,
    )
    after = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 50.0),
        ],
        game_time=20.0,
    )
    reward = calculator.step_reward(before, after, [])
    assert reward.components["isolated_unit_per_second"] == pytest.approx(-5.0)


def test_recompense_terminale(calculator: RewardCalculator) -> None:
    assert calculator.terminal_reward(BattleOutcomeKind.VICTORY).total == pytest.approx(1000.0)
    assert calculator.terminal_reward(BattleOutcomeKind.DEFEAT).total == pytest.approx(-1000.0)
    assert calculator.terminal_reward(BattleOutcomeKind.UNKNOWN).total == pytest.approx(0.0)


def test_fusion_de_recompenses() -> None:
    first = RewardBreakdown(total=10.0, components={"a": 10.0})
    second = RewardBreakdown(total=5.0, components={"a": 2.0, "b": 3.0})
    merged = first.merged(second)
    assert merged.total == pytest.approx(15.0)
    assert merged.components == {"a": 12.0, "b": 3.0}
