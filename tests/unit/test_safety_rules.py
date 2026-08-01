"""Regles de securite : chaque interdit du README a son test."""

from __future__ import annotations

from totalwar_ai.agent.explainability import Decision
from totalwar_ai.agent.safety_rules import (
    ArtilleryChargeRule,
    ExcessivePursuitRule,
    LordProtectionRule,
    RangedMeleeRule,
    SafetyEngine,
    SafetySettings,
    SuicidalChargeRule,
)
from totalwar_ai.domain.actions import ActionType, AgentAction
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole

REAR = Vector3(0.0, 0.0, -200.0)


def _decision(action: AgentAction) -> Decision:
    return Decision(action=action, cause="test", objective="test")


def test_artillerie_ne_charge_pas(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_art", Side.ALLY, UnitRole.ARTILLERY, 0.0, -50.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 50.0),
        ]
    )
    action = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a_art",), parameters={"target_id": "e1"}
    )
    verdict = ArtilleryChargeRule().check(action, state, REAR)
    assert verdict is not None
    assert verdict.rule == "artillerie_ne_charge_pas"
    assert verdict.replacement is not None
    assert verdict.replacement.type is ActionType.HOLD_POSITION


def test_artillerie_peut_tirer(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_art", Side.ALLY, UnitRole.ARTILLERY, 0.0, -50.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 150.0),
        ]
    )
    action = AgentAction(
        type=ActionType.FOCUS_FIRE, actor_ids=("a_art",), parameters={"target_id": "e1"}
    )
    assert ArtilleryChargeRule().check(action, state, REAR) is None


def test_tireur_menace_est_replie(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, 0.0),
            make_unit("e_cav", Side.ENEMY, UnitRole.SHOCK_CAVALRY, 0.0, 40.0),
        ]
    )
    action = AgentAction(
        type=ActionType.FOCUS_FIRE, actor_ids=("a_arc",), parameters={"target_id": "e_cav"}
    )
    verdict = RangedMeleeRule(threat_radius=70.0).check(action, state, REAR)
    assert verdict is not None
    assert verdict.replacement is not None
    assert verdict.replacement.type is ActionType.RETREAT
    assert verdict.replacement.destination == REAR


def test_tireur_hors_menace_tire_librement(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, 0.0),
            make_unit("e_cav", Side.ENEMY, UnitRole.SHOCK_CAVALRY, 0.0, 200.0),
        ]
    )
    action = AgentAction(
        type=ActionType.FOCUS_FIRE, actor_ids=("a_arc",), parameters={"target_id": "e_cav"}
    )
    assert RangedMeleeRule(threat_radius=70.0).check(action, state, REAR) is None


def test_ennemi_deja_fixe_ne_compte_pas_comme_menace(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Un ennemi en melee avec notre ligne ne peut pas fondre sur nos tireurs."""
    state = make_battle(
        [
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, 0.0),
            make_unit("a_inf", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 45.0, is_engaged=True),
            make_unit(
                "e_inf",
                Side.ENEMY,
                UnitRole.MELEE_INFANTRY,
                0.0,
                50.0,
                is_engaged=True,
                current_target_id="a_inf",
            ),
        ]
    )
    action = AgentAction(
        type=ActionType.FOCUS_FIRE, actor_ids=("a_arc",), parameters={"target_id": "e_inf"}
    )
    assert RangedMeleeRule(threat_radius=70.0).check(action, state, REAR) is None


def test_charge_suicidaire_bloquee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 40.0),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 10.0, 45.0),
            make_unit("e3", Side.ENEMY, UnitRole.MELEE_INFANTRY, -10.0, 45.0),
            make_unit("e4", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 55.0),
        ]
    )
    action = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a1",), parameters={"target_id": "e1"}
    )
    verdict = SuicidalChargeRule(min_ratio=0.6, radius=60.0).check(action, state, REAR)
    assert verdict is not None
    assert verdict.replacement is not None
    assert verdict.replacement.type is ActionType.HOLD_POSITION


def test_charge_soutenue_autorisee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("a2", Side.ALLY, UnitRole.MELEE_INFANTRY, 20.0, 0.0),
            make_unit("a3", Side.ALLY, UnitRole.MELEE_INFANTRY, -20.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 40.0),
        ]
    )
    action = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a1",), parameters={"target_id": "e1"}
    )
    assert SuicidalChargeRule(min_ratio=0.6, radius=60.0).check(action, state, REAR) is None


def test_unite_deja_engagee_ne_rompt_pas_le_combat(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0, is_engaged=True),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 8.0),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 10.0, 10.0),
            make_unit("e3", Side.ENEMY, UnitRole.MELEE_INFANTRY, -10.0, 10.0),
        ]
    )
    action = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a1",), parameters={"target_id": "e1"}
    )
    assert SuicidalChargeRule(min_ratio=0.6, radius=60.0).check(action, state, REAR) is None


def test_seigneur_entame_est_protege(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_lord", Side.ALLY, UnitRole.LORD, 0.0, 0.0, health_ratio=0.2),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 30.0),
        ]
    )
    action = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a_lord",), parameters={"target_id": "e1"}
    )
    verdict = LordProtectionRule(health_threshold=0.35).check(action, state, REAR)
    assert verdict is not None
    assert verdict.replacement is not None
    assert verdict.replacement.type is ActionType.RETREAT


def test_seigneur_en_forme_peut_combattre(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_lord", Side.ALLY, UnitRole.LORD, 0.0, 0.0, health_ratio=0.9),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 30.0),
        ]
    )
    action = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a_lord",), parameters={"target_id": "e1"}
    )
    assert LordProtectionRule(health_threshold=0.35).check(action, state, REAR) is None


def test_poursuite_lointaine_refusee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_cav", Side.ALLY, UnitRole.LIGHT_CAVALRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 300.0, is_routing=True),
        ]
    )
    action = AgentAction(
        type=ActionType.CHASE_ROUTING, actor_ids=("a_cav",), parameters={"target_id": "e1"}
    )
    verdict = ExcessivePursuitRule(max_distance=150.0).check(action, state, REAR)
    assert verdict is not None
    assert verdict.rule == "poursuite_mesuree"


def test_poursuite_proche_autorisee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_cav", Side.ALLY, UnitRole.LIGHT_CAVALRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 60.0, is_routing=True),
        ]
    )
    action = AgentAction(
        type=ActionType.CHASE_ROUTING, actor_ids=("a_cav",), parameters={"target_id": "e1"}
    )
    assert ExcessivePursuitRule(max_distance=150.0).check(action, state, REAR) is None


def test_arret_d_urgence_bloque_tout(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    engine = SafetyEngine.from_settings(SafetySettings())
    engine.trigger_emergency_stop()
    outcome = engine.filter(
        [_decision(AgentAction(type=ActionType.HOLD_POSITION, actor_ids=("a1",)))], state
    )
    assert outcome.allowed == ()
    assert outcome.blocked[0].blocked_by == "arret_d_urgence"

    engine.release_emergency_stop()
    assert (
        len(
            engine.filter(
                [_decision(AgentAction(type=ActionType.HOLD_POSITION, actor_ids=("a1",)))], state
            ).allowed
        )
        == 1
    )


def test_limite_d_ordres_par_minute(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    engine = SafetyEngine.from_settings(SafetySettings(max_orders_per_minute=3))
    decisions = [
        _decision(AgentAction(type=ActionType.HOLD_POSITION, actor_ids=(f"a{index}",)))
        for index in range(5)
    ]
    outcome = engine.throttle(decisions, game_time=10.0)
    assert len(outcome.allowed) == 3
    assert len(outcome.blocked) == 2
    assert outcome.blocked[0].blocked_by == "limite_d_ordres"

    # Une minute plus tard, le budget est de nouveau disponible.
    assert len(engine.throttle(decisions, game_time=75.0).allowed) == 3


def test_action_de_substitution_est_emise(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_art", Side.ALLY, UnitRole.ARTILLERY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 40.0),
        ]
    )
    engine = SafetyEngine.from_settings(SafetySettings())
    outcome = engine.filter(
        [
            _decision(
                AgentAction(
                    type=ActionType.ATTACK_TARGET,
                    actor_ids=("a_art",),
                    parameters={"target_id": "e1"},
                )
            )
        ],
        state,
    )
    assert len(outcome.blocked) == 1
    assert len(outcome.allowed) == 1
    assert outcome.allowed[0].action.type is ActionType.HOLD_POSITION
