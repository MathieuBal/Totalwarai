"""Le cliquet de la reserve, et le repli tirant qui le remplace.

Chacun de ces tests **echoue sans le correctif qu'il accompagne**. Ils pincent
une boucle de retroaction, pas un reglage : le detail des seuils peut bouger,
la boucle ne doit pas revenir.
"""

from __future__ import annotations

import pytest

from totalwar_ai.agent.grouping import GroupKind, build_groups
from totalwar_ai.agent.planner import Planner, PlannerSettings, Posture
from totalwar_ai.domain.actions import ActionType
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole


@pytest.fixture
def planner() -> Planner:
    return Planner(settings=PlannerSettings())


def _armee(make_unit, **fatigues: float):  # type: ignore[no-untyped-def]
    """Quatre unites de ligne, deux tireurs, un ennemi qui approche."""
    unites = [
        make_unit(
            f"a_inf{index}", Side.ALLY, UnitRole.MELEE_INFANTRY, x=-40.0 + 30.0 * index, z=0.0
        )
        for index in range(4)
    ]
    unites = [
        make_unit(
            unite.id,
            Side.ALLY,
            UnitRole.MELEE_INFANTRY,
            x=unite.position.x,
            z=0.0,
            fatigue=fatigues.get(unite.id, 0.0),
        )
        for unite in unites
    ]
    unites += [
        make_unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, x=-20.0, z=-30.0),
        make_unit("a_arc2", Side.ALLY, UnitRole.RANGED_INFANTRY, x=20.0, z=-30.0),
        make_unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, x=0.0, z=200.0),
        make_unit("e_inf2", Side.ENEMY, UnitRole.MELEE_INFANTRY, x=40.0, z=200.0),
    ]
    return unites


def test_la_reserve_ne_tourne_pas_quand_elle_se_fatigue(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """La fatigue du repli ne doit pas evincer celui qu'on vient de replier.

    `effective_strength` inclut la fatigue, et se replier en coute : sans
    appartenance collante, l'unite mise en reserve repassait derriere une unite
    restee immobile et se faisait remplacer au plan suivant — 119 changements de
    composition en 1201 plans sur `skirmish_standoff`.
    """
    state = make_battle(_armee(make_unit))
    premier = build_groups(state, reserve_size=1)
    tenue = premier.get(GroupKind.RESERVE).unit_ids
    assert len(tenue) == 1

    # L'unite repliee s'est fatiguee ; toutes les autres sont restees fraiches.
    fatiguee = make_battle(_armee(make_unit, **{tenue[0]: 0.9}))
    second = build_groups(fatiguee, reserve_size=1, reserve_ids=tenue)
    assert second.get(GroupKind.RESERVE).unit_ids == tenue


def test_la_reserve_ne_tire_pas_l_ancre_vers_l_arriere(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """L'ancre suit la ligne qui combat, pas celui qu'on a envoye derriere.

    La reserve est un groupe de doctrine et non un role : ses unites gardaient
    leur role de melee et comptaient dans l'ancre. On les envoie soixante metres
    en arriere, l'ancre suivait, le point de ralliement reculait encore — 198 m
    de recul mesures face a un ennemi parfaitement immobile.
    """
    planner_ = Planner(settings=PlannerSettings())
    unites = _armee(make_unit)
    plan = planner_.build_plan(make_battle(unites))
    reserve = plan.groups.get(GroupKind.RESERVE).unit_ids
    assert reserve, "le scenario doit produire une reserve"

    # La reserve a rejoint son point de ralliement, loin derriere la ligne.
    reculee = [
        make_unit(
            unite.id,
            unite.side,
            unite.role,
            x=unite.position.x,
            z=unite.position.z - (120.0 if unite.id in reserve else 0.0),
        )
        for unite in unites
    ]
    apres = planner_.build_plan(make_battle(reculee))
    assert apres.anchor.z == pytest.approx(plan.anchor.z, abs=1.0)


def test_le_repli_tirant_s_arrete_sans_munitions(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Reculer sans munitions ne fait que ceder du terrain.

    C'est la condition d'arret qui separe ce repli de l'« avance imposee apres un
    delai » que l'ADR 0005 a mesuree et rejetee.
    """
    planner_ = Planner(settings=PlannerSettings())
    proches = [
        unite
        if unite.side is Side.ALLY
        else make_unit(unite.id, unite.side, unite.role, x=unite.position.x, z=40.0)
        for unite in _armee(make_unit)
    ]
    avec = planner_.build_plan(make_battle(proches))

    planner_.reset()
    sans = [
        make_unit(
            unite.id,
            unite.side,
            unite.role,
            x=unite.position.x,
            z=unite.position.z,
            ammo_ratio=0.0,
        )
        for unite in proches
    ]
    sec = planner_.build_plan(make_battle(sans))
    assert avec.anchor.z < sec.anchor.z, "sans munitions, aucun recul ne doit etre demande"


def test_les_tireurs_suivent_la_ligne_qui_avance(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Un tireur sans cible a portee recevait **aucun ordre** et restait plante.

    Tant que l'ennemi venait a nous, cela ne se voyait pas. Des que la ligne
    avance, l'appui reste en arriere et la ligne arrive seule — « trois unites
    d'archers qui n'ont pas suivi le pack », constate en jeu par l'operateur.
    """
    planner_ = Planner(settings=PlannerSettings(), forced_posture=Posture.ADVANCE)
    unites = [
        make_unit("a_inf1", Side.ALLY, UnitRole.MELEE_INFANTRY, x=0.0, z=300.0),
        make_unit("a_inf2", Side.ALLY, UnitRole.MELEE_INFANTRY, x=40.0, z=300.0),
        # Le tireur est reste tres loin derriere, hors de portee de tout ennemi.
        make_unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, x=0.0, z=0.0),
        make_unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, x=0.0, z=420.0),
    ]
    state = make_battle(unites)
    plan = planner_.build_plan(state)
    decisions = planner_.tactical_decisions(state, plan)

    ordres = [
        decision.action
        for decision in decisions
        if "a_arc1" in decision.action.actor_ids and decision.action.type is ActionType.MOVE_GROUP
    ]
    assert ordres, "le tireur distance doit recevoir un ordre de ralliement"
    destination = ordres[0].parameters["destination"]
    assert isinstance(destination, Vector3)
    assert destination.z > 200.0, "il doit rejoindre la ligne, pas rester en arriere"


def test_reset_oublie_la_bataille_precedente(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Deux batailles identiques doivent se derouler identiquement.

    Le planificateur porte de la memoire — engagements de cible, composition de
    la reserve. `DeterministicTacticalAgent.reset` ne la vidait pas.
    """
    planner_ = Planner(settings=PlannerSettings())
    state = make_battle(_armee(make_unit))
    planner_.build_plan(state)
    assert planner_._reserve_ids

    planner_.reset()
    assert not planner_._reserve_ids
    assert not planner_._commitments
