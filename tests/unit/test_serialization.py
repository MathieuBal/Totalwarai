"""Serialisation du domaine : aller-retour JSON et rejet des donnees invalides."""

from __future__ import annotations

import json

import pytest

from totalwar_ai.domain.actions import (
    ActionResult,
    ActionStatus,
    ActionType,
    AgentAction,
    Formation,
)
from totalwar_ai.domain.battle_state import BattlePhase, BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.serialization import SchemaError
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState


def test_unit_state_aller_retour() -> None:
    original = UnitState(
        id="a1",
        side=Side.ALLY,
        role=UnitRole.RANGED_INFANTRY,
        position=Vector3(12.5, 0.0, -8.0),
        heading=1.2,
        health_ratio=0.84,
        entity_ratio=0.77,
        morale=61.0,
        fatigue=0.32,
        ammo_ratio=0.48,
        tags=("missile", "armour_piercing"),
    )
    restored = UnitState.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original


def test_battle_state_aller_retour(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.ARTILLERY, 0.0, 200.0),
        ],
        phase=BattlePhase.APPROACH,
    )
    restored = BattleState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored.battle_id == state.battle_id
    assert restored.phase is BattlePhase.APPROACH
    assert [unit.id for unit in restored.units] == ["a1", "e1"]


def test_agent_action_aller_retour_avec_vecteur_et_enum() -> None:
    action = AgentAction(
        type=ActionType.MOVE_GROUP,
        actor_ids=("a1", "a2"),
        parameters={"destination": Vector3(1.0, 0.0, 2.0), "formation": Formation.LINE},
        reason="tenir la ligne",
        confidence=0.91,
    )
    payload = json.loads(json.dumps(action.to_dict()))
    restored = AgentAction.from_dict(payload)
    assert restored.type is ActionType.MOVE_GROUP
    assert restored.destination == Vector3(1.0, 0.0, 2.0)
    assert restored.parameters["formation"] == "line"


def test_action_result_aller_retour() -> None:
    result = ActionResult(action_id="x", status=ActionStatus.REJECTED, error="cible inconnue")
    assert ActionResult.from_dict(result.to_dict()) == result


def test_ratio_hors_bornes_rejete() -> None:
    with pytest.raises(SchemaError, match="entre 0 et 1"):
        UnitState.from_dict({"id": "a", "side": "ally", "health_ratio": 1.5})


def test_champ_obligatoire_manquant() -> None:
    with pytest.raises(SchemaError, match="obligatoire"):
        UnitState.from_dict({"side": "ally"})


def test_camp_inconnu_rejete() -> None:
    with pytest.raises(SchemaError, match="invalide"):
        UnitState.from_dict({"id": "a", "side": "neutre"})


def test_role_inconnu_retombe_sur_unknown() -> None:
    """Une version plus recente du mod ne doit pas casser tout le message."""
    unit = UnitState.from_dict({"id": "a", "side": "ally", "role": "necro_dragon"})
    assert unit.role is UnitRole.UNKNOWN


def test_identifiants_dupliques_rejetes() -> None:
    with pytest.raises(SchemaError, match="duplique"):
        BattleState.from_dict(
            {
                "battle_id": "b",
                "units": [{"id": "a", "side": "ally"}, {"id": "a", "side": "enemy"}],
            }
        )


def test_action_sans_acteur_rejetee() -> None:
    with pytest.raises(SchemaError, match="au moins une unite"):
        AgentAction(type=ActionType.HOLD_POSITION, actor_ids=())


def test_signature_stable_par_arrondi() -> None:
    first = AgentAction(
        type=ActionType.MOVE_GROUP,
        actor_ids=("a2", "a1"),
        parameters={"destination": Vector3(10.2, 0.0, 5.1)},
    )
    second = AgentAction(
        type=ActionType.MOVE_GROUP,
        actor_ids=("a1", "a2"),
        parameters={"destination": Vector3(10.4, 0.0, 4.9)},
    )
    assert first.signature() == second.signature()
