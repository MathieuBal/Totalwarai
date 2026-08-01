"""Planificateur : posture, groupes, ciblage et deploiement."""

from __future__ import annotations

import math

import pytest

from totalwar_ai.agent.grouping import GroupKind, build_groups
from totalwar_ai.agent.planner import Planner, PlannerSettings, Posture, lateral_of
from totalwar_ai.domain.actions import ActionType
from totalwar_ai.domain.battle_state import BattlePhase
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole


@pytest.fixture
def planner() -> Planner:
    return Planner(settings=PlannerSettings())


def test_lateral_est_perpendiculaire() -> None:
    front = Vector3(0.0, 0.0, 1.0)
    lateral = lateral_of(front)
    assert lateral.x * front.x + lateral.z * front.z == pytest.approx(0.0)


def test_groupes_par_role(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_inf", Side.ALLY, UnitRole.MELEE_INFANTRY),
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY),
            make_unit("a_art", Side.ALLY, UnitRole.ARTILLERY),
            make_unit("a_cav", Side.ALLY, UnitRole.SHOCK_CAVALRY),
            make_unit("a_lord", Side.ALLY, UnitRole.LORD),
        ]
    )
    groups = build_groups(state)
    assert groups.get(GroupKind.FRONT_LINE).unit_ids == ("a_inf",)
    assert groups.get(GroupKind.MISSILE).unit_ids == ("a_arc",)
    assert groups.get(GroupKind.ARTILLERY).unit_ids == ("a_art",)
    assert groups.get(GroupKind.CAVALRY).unit_ids == ("a_cav",)
    assert groups.get(GroupKind.COMMAND).unit_ids == ("a_lord",)


def test_reserve_prelevee_sur_la_ligne(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [make_unit(f"a{i}", Side.ALLY, UnitRole.MELEE_INFANTRY, float(i) * 40) for i in range(4)]
    )
    groups = build_groups(state, reserve_size=1)
    assert len(groups.get(GroupKind.RESERVE).unit_ids) == 1
    assert len(groups.get(GroupKind.FRONT_LINE).unit_ids) == 3


def test_unites_mortes_exclues_des_groupes(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY),
            make_unit("a2", Side.ALLY, UnitRole.MELEE_INFANTRY, entity_ratio=0.0, health_ratio=0.0),
            make_unit("a3", Side.ALLY, UnitRole.MELEE_INFANTRY, is_routing=True),
        ]
    )
    assert build_groups(state).get(GroupKind.FRONT_LINE).unit_ids == ("a1",)


def test_posture_temporise_en_inferiorite(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            *[
                make_unit(f"e{i}", Side.ENEMY, UnitRole.MELEE_INFANTRY, float(i) * 40, 200.0)
                for i in range(4)
            ],
        ]
    )
    assert planner.build_plan(state).posture is Posture.DELAY


def test_posture_enveloppe_avec_cavalerie_et_superiorite(
    planner: Planner, make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            *[
                make_unit(f"a{i}", Side.ALLY, UnitRole.MELEE_INFANTRY, float(i) * 40, 0.0)
                for i in range(4)
            ],
            make_unit("a_cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, -100.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 200.0),
        ]
    )
    assert planner.build_plan(state).posture is Posture.ENVELOP


def test_posture_defend_avec_avantage_de_tir(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_inf", Side.ALLY, UnitRole.SPEAR_INFANTRY, 0.0, 0.0),
            make_unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, -40.0, -40.0),
            make_unit("a_arc2", Side.ALLY, UnitRole.RANGED_INFANTRY, 40.0, -40.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 200.0),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 40.0, 200.0),
        ]
    )
    assert planner.build_plan(state).posture is Posture.DEFEND


def test_ancre_sur_la_ligne_et_non_sur_l_armee(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """L'ancre doit ignorer les tireurs, sinon le dispositif recule sans fin."""
    state = make_battle(
        [
            make_unit("a_inf", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, -200.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 200.0),
        ]
    )
    assert planner.build_plan(state).anchor.z == pytest.approx(0.0)


def test_ciblage_de_tir_prefere_l_artillerie(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, 0.0),
            make_unit("e_inf", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 40.0),
            make_unit("e_art", Side.ENEMY, UnitRole.ARTILLERY, 0.0, 90.0),
        ]
    )
    shooter = state.unit("a_arc")
    assert shooter is not None
    target = planner.select_target(shooter, state, for_missile=True)
    assert target is not None
    assert target.id == "e_art"


def test_ciblage_de_tir_respecte_la_portee(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, 0.0),
            make_unit("e_art", Side.ENEMY, UnitRole.ARTILLERY, 0.0, 400.0),
        ]
    )
    shooter = state.unit("a_arc")
    assert shooter is not None
    assert planner.select_target(shooter, state, for_missile=True) is None


def test_tir_evite_les_melees_en_cours(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Tirer dans une melee risque de toucher nos propres unites."""
    state = make_battle(
        [
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 40.0, is_engaged=True),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 60.0),
        ]
    )
    shooter = state.unit("a_arc")
    assert shooter is not None
    target = planner.select_target(shooter, state, for_missile=True)
    assert target is not None
    assert target.id == "e2"


def test_ciblage_melee_prefere_le_plus_proche(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Traverser le champ pour une meilleure cible offre du temps a l'ennemi."""
    state = make_battle(
        [
            make_unit("a_inf", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("e_inf", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 20.0),
            make_unit("e_art", Side.ENEMY, UnitRole.ARTILLERY, 0.0, 250.0),
        ]
    )
    attacker = state.unit("a_inf")
    assert attacker is not None
    target = planner.select_target(attacker, state)
    assert target is not None
    assert target.id == "e_inf"


def test_saturation_repartit_les_tireurs(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 40.0),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 5.0, 42.0),
        ]
    )
    shooter = state.unit("a_arc")
    assert shooter is not None
    first = planner.select_target(shooter, state, for_missile=True)
    assert first is not None
    second = planner.select_target(shooter, state, assignments={first.id: 3}, for_missile=True)
    assert second is not None
    assert second.id != first.id


def test_deploiement_place_le_tir_derriere_la_ligne(
    planner: Planner, make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_inf", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
            make_unit("a_arc", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 200.0),
        ],
        phase=BattlePhase.DEPLOYMENT,
    )
    plan = planner.build_plan(state)
    decisions = planner.deployment_decisions(state, plan)
    by_actor = {decision.action.actor_ids: decision.action for decision in decisions}
    line = by_actor[("a_inf",)]
    missile = by_actor[("a_arc",)]
    assert line.type is ActionType.MOVE_GROUP
    destination_line = line.destination
    destination_missile = missile.destination
    assert destination_line is not None and destination_missile is not None
    # L'ennemi est en +z : les tireurs doivent etre plus en arriere que la ligne.
    assert destination_missile.z < destination_line.z


def test_aucune_cible_sans_ennemi(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    attacker = state.unit("a1")
    assert attacker is not None
    assert planner.select_target(attacker, state) is None


def test_defense_fait_face_a_la_masse_ennemie(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Se tourner vers l'ennemi le plus proche offrirait le flanc au gros des forces."""
    state = make_battle(
        [
            make_unit("a_inf", Side.ALLY, UnitRole.SPEAR_INFANTRY, 0.0, 0.0),
            # Une cavalerie isolee tout pres, a l'ouest.
            make_unit("e_cav", Side.ENEMY, UnitRole.SHOCK_CAVALRY, -60.0, 0.0),
            # Le gros de l'infanterie droit devant, un peu plus loin.
            make_unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, -20.0, 90.0),
            make_unit("e_inf2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 20.0, 90.0),
            make_unit("e_inf3", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 100.0),
        ]
    )
    unit = state.unit("a_inf")
    assert unit is not None
    heading = planner._threat_heading(unit, state, state.enemies())
    assert heading is not None
    # Le cap doit pointer vers +z (la masse), pas vers -x (la cavalerie isolee).
    assert math.cos(heading) > 0.5


def test_sans_ennemi_proche_aucun_cap_de_menace(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a_inf", Side.ALLY, UnitRole.SPEAR_INFANTRY, 0.0, 0.0),
            make_unit("e_inf", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 900.0),
        ]
    )
    unit = state.unit("a_inf")
    assert unit is not None
    assert planner._threat_heading(unit, state, state.enemies()) is None


def test_la_doctrine_n_emet_pas_de_reorientation(planner: Planner, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Choix mesure, documente dans decisions/0004 : le pivot explicite degradait l'agent."""
    state = make_battle(
        [
            make_unit("a_inf1", Side.ALLY, UnitRole.SPEAR_INFANTRY, -40.0, 0.0),
            make_unit("a_inf2", Side.ALLY, UnitRole.SPEAR_INFANTRY, 40.0, 0.0),
            make_unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, -40.0),
            make_unit("a_arc2", Side.ALLY, UnitRole.RANGED_INFANTRY, 40.0, -40.0),
            make_unit("e_cav", Side.ENEMY, UnitRole.SHOCK_CAVALRY, -140.0, 5.0),
            make_unit("e_inf", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 250.0),
        ]
    )
    plan = planner.build_plan(state)
    types = {decision.action.type for decision in planner.tactical_decisions(state, plan)}
    assert ActionType.REORIENT_FRONT not in types


def test_les_dix_scenarios_de_reference_sont_jouables() -> None:
    """Chaque scenario du banc doit decrire deux camps non vides."""
    from totalwar_ai.simulation.scenarios import ScenarioCatalog

    for scenario in ScenarioCatalog().all():
        assert scenario.army(Side.ALLY), scenario.name
        assert scenario.army(Side.ENEMY), scenario.name
        assert scenario.fingerprint(), scenario.name
        assert scenario.description, scenario.name


def test_etat_initial_degrade_des_scenarios() -> None:
    """`fragile_lord` et `rout_pursuit` decrivent une bataille deja engagee."""
    from totalwar_ai.simulation.scenarios import get_scenario

    lord = next(spec for spec in get_scenario("fragile_lord").units if spec.role is UnitRole.LORD)
    assert lord.initial_health < 0.5

    routing = [spec for spec in get_scenario("rout_pursuit").units if spec.initial_routing]
    assert len(routing) >= 2
    assert all(spec.side is Side.ENEMY for spec in routing)
