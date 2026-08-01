"""Tests de comportement.

Ce sont les garde-fous listes dans le README : ils verifient que l'agent
protege ses archers, n'envoie pas l'artillerie charger, refuse une poursuite
dangereuse, concentre ses tireurs et conserve une reserve. Ils reposent sur des
etats synthetiques reproductibles, pas sur le hasard d'une bataille.
"""

from __future__ import annotations

import pytest

from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.config import AppConfig
from totalwar_ai.domain.actions import CHARGE_ACTIONS, ActionType
from totalwar_ai.domain.battle_state import BattlePhase
from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.simulation.runner import run_battle
from totalwar_ai.simulation.scenarios import get_scenario


def _actions_by_type(turn, action_type: ActionType):  # type: ignore[no-untyped-def]
    return [decision.action for decision in turn.decisions if decision.action.type is action_type]


def test_les_archers_menaces_sont_replies(
    agent: DeterministicTacticalAgent,
    make_unit,
    make_battle,  # type: ignore[no-untyped-def]
) -> None:
    state = make_battle(
        [
            make_unit("a_inf1", Side.ALLY, UnitRole.MELEE_INFANTRY, -40.0, 0.0),
            make_unit("a_inf2", Side.ALLY, UnitRole.MELEE_INFANTRY, 40.0, 0.0),
            make_unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, -40.0),
            make_unit("e_cav1", Side.ENEMY, UnitRole.SHOCK_CAVALRY, 10.0, -10.0),
            make_unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 120.0),
        ]
    )
    turn = agent.decide(state)
    retreats = _actions_by_type(turn, ActionType.RETREAT)
    assert any("a_arc1" in action.actor_ids for action in retreats)


def test_l_artillerie_ne_charge_jamais(
    agent: DeterministicTacticalAgent,
    make_unit,
    make_battle,  # type: ignore[no-untyped-def]
) -> None:
    state = make_battle(
        [
            make_unit("a_art1", Side.ALLY, UnitRole.ARTILLERY, 0.0, -30.0),
            make_unit("a_inf1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 30.0),
        ]
    )
    turn = agent.decide(state)
    for decision in turn.decisions:
        if decision.action.type in CHARGE_ACTIONS:
            assert "a_art1" not in decision.action.actor_ids


def test_l_artillerie_ne_charge_pas_de_toute_la_bataille(config: AppConfig) -> None:
    result = run_battle(
        get_scenario("artillery_assault"), config=config, seed=41, generate_report=False
    )
    artillery = {"a_art1", "a_art2"}
    offending = [
        decision
        for decision in result.decisions
        if decision.action.type in CHARGE_ACTIONS and artillery & set(decision.action.actor_ids)
    ]
    assert offending == []


def test_poursuite_dangereuse_refusee(
    agent: DeterministicTacticalAgent,
    make_unit,
    make_battle,  # type: ignore[no-untyped-def]
) -> None:
    state = make_battle(
        [
            make_unit("a_inf1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("a_inf2", Side.ALLY, UnitRole.MELEE_INFANTRY, 40.0, 0.0),
            make_unit("a_inf3", Side.ALLY, UnitRole.MELEE_INFANTRY, -40.0, 0.0),
            make_unit("a_cav1", Side.ALLY, UnitRole.LIGHT_CAVALRY, 0.0, -20.0),
            make_unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 320.0, is_routing=True),
        ],
        phase=BattlePhase.PURSUIT,
    )
    turn = agent.decide(state)
    chases = _actions_by_type(turn, ActionType.CHASE_ROUTING)
    assert chases == []
    assert any(decision.blocked_by == "poursuite_mesuree" for decision in turn.blocked) or all(
        decision.action.type is not ActionType.CHASE_ROUTING for decision in turn.decisions
    )


def test_les_tireurs_concentrent_sur_la_cible_prioritaire(
    agent: DeterministicTacticalAgent,
    make_unit,
    make_battle,  # type: ignore[no-untyped-def]
) -> None:
    state = make_battle(
        [
            make_unit("a_inf1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, -30.0, -40.0),
            make_unit("a_arc2", Side.ALLY, UnitRole.RANGED_INFANTRY, 30.0, -40.0),
            make_unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 60.0),
            make_unit("e_art1", Side.ENEMY, UnitRole.ARTILLERY, 0.0, 70.0),
        ]
    )
    turn = agent.decide(state)
    volleys = _actions_by_type(turn, ActionType.FOCUS_FIRE)
    assert len(volleys) == 2
    assert all(action.target_id == "e_art1" for action in volleys)


def test_la_reserve_est_conservee_en_defense(
    agent: DeterministicTacticalAgent,
    make_unit,
    make_battle,  # type: ignore[no-untyped-def]
) -> None:
    state = make_battle(
        [
            *[
                make_unit(f"a_inf{index}", Side.ALLY, UnitRole.MELEE_INFANTRY, index * 40.0, 0.0)
                for index in range(4)
            ],
            make_unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, -40.0),
            make_unit("a_arc2", Side.ALLY, UnitRole.RANGED_INFANTRY, 40.0, -40.0),
            *[
                make_unit(f"e_inf{index}", Side.ENEMY, UnitRole.MELEE_INFANTRY, index * 40.0, 160.0)
                for index in range(5)
            ],
        ]
    )
    plan = agent.planner.build_plan(state)
    assert plan.groups.to_dict().get("reserve")
    turn = agent.decide(state)
    assert _actions_by_type(turn, ActionType.FORM_RESERVE)


def test_le_seigneur_reste_en_soutien(
    agent: DeterministicTacticalAgent,
    make_unit,
    make_battle,  # type: ignore[no-untyped-def]
) -> None:
    state = make_battle(
        [
            make_unit("a_inf1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("a_lord", Side.ALLY, UnitRole.LORD, 0.0, 0.0, health_ratio=0.2),
            make_unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 25.0),
        ]
    )
    turn = agent.decide(state)
    for decision in turn.decisions:
        if "a_lord" in decision.action.actor_ids:
            assert decision.action.type not in CHARGE_ACTIONS


def test_meme_graine_meme_bataille(config: AppConfig) -> None:
    first = run_battle(
        get_scenario("balanced_clash"),
        config=config,
        seed=99,
        battle_id="det-1",
        generate_report=False,
    )
    second = run_battle(
        get_scenario("balanced_clash"),
        config=config,
        seed=99,
        battle_id="det-2",
        generate_report=False,
    )
    assert first.outcome is second.outcome
    assert first.summary.duration == pytest.approx(second.summary.duration)
    assert first.summary.total_reward == pytest.approx(second.summary.total_reward)
    assert len(first.episode.transitions) == len(second.episode.transitions)


def test_graines_differentes_donnent_des_batailles_differentes(config: AppConfig) -> None:
    first = run_battle(get_scenario("balanced_clash"), config=config, seed=1, generate_report=False)
    second = run_battle(
        get_scenario("balanced_clash"), config=config, seed=12345, generate_report=False
    )
    assert first.summary.total_reward != second.summary.total_reward


@pytest.mark.parametrize(
    ("scenario_name", "minimum_share"),
    [("ranged_defense", 0.4), ("cavalry_flank_threat", 0.4), ("artillery_assault", 0.4)],
)
def test_scenarios_favorables_sont_gagnes(
    config: AppConfig, scenario_name: str, minimum_share: float
) -> None:
    """Non-regression : ces situations doivent rester des victoires nettes."""
    result = run_battle(get_scenario(scenario_name), config=config, generate_report=False)
    assert result.outcome.value == "victory"
    assert result.summary.ally_remaining >= minimum_share
