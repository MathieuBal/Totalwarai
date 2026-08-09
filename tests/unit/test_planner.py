"""Planificateur : posture, groupes, ciblage et deploiement."""

from __future__ import annotations

import math

import pytest

from totalwar_ai.agent.grouping import GroupKind, build_groups
from totalwar_ai.agent.planner import (
    Planner,
    PlannerSettings,
    Posture,
    can_be_attacked,
    finishing_value,
    lateral_of,
)
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


def test_les_tireurs_concentrent_puis_se_repartissent(
    planner: Planner, make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    """Le tir concentre comme la melee, jusqu'a l'entassement.

    Cette regle disait l'inverse : trois tireurs sur une cible faisaient fuir le
    quatrieme ailleurs. C'est la dispersion que la mesure condamne — 9,77
    unites-equivalent de degats en jeu, zero regiment abattu. Le banc a tranche :
    en concentrant aussi le tir, 113 unites adverses detruites sur huit graines
    contre 105 en le dispersant, a taux de victoire egal.

    L'entassement reste puni, sinon toute l'armee viserait un seul regiment.
    """
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

    # Deux tireurs deja sur la cible : on renforce.
    renfort = planner.select_target(shooter, state, assignments={first.id: 2}, for_missile=True)
    assert renfort is not None and renfort.id == first.id

    # Sept : ce n'est plus concentrer, c'est s'entasser.
    ailleurs = planner.select_target(shooter, state, assignments={first.id: 7}, for_missile=True)
    assert ailleurs is not None and ailleurs.id != first.id


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


# --- concentration : ne pas ouvrir une melee perdue d'avance --------------------


def test_le_rapport_local_compte_l_attaquant_dans_les_notres(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Un contre un une fois arrive, c'est la parite — donc zero."""
    attaquant = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 200.0)
    cible = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0)
    etat = make_battle([attaquant, cible])

    assert Planner().local_balance(attaquant, cible, etat) == pytest.approx(0.0)


def test_le_rapport_local_est_negatif_quand_on_arrive_en_inferiorite(  # type: ignore[no-untyped-def]
    make_unit, make_battle
) -> None:
    """Deux ennemis groupes, nous seuls : c'est le 1 contre 2 mesure en jeu."""
    attaquant = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 200.0)
    cible = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0)
    etat = make_battle(
        [attaquant, cible, make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 20.0)]
    )

    assert Planner().local_balance(attaquant, cible, etat) < 0.0


def test_les_allies_trop_loin_ne_soutiennent_pas(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Le soutien est local : au-dela du rayon, un allie ne pese pas."""
    attaquant = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 200.0)
    cible = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0)
    loin = make_unit("a2", Side.ALLY, UnitRole.MELEE_INFANTRY, 400.0)
    pres = make_unit("a3", Side.ALLY, UnitRole.MELEE_INFANTRY, 20.0)

    planificateur = Planner()
    assert planificateur.local_balance(attaquant, cible, make_battle([attaquant, cible, loin])) == (
        pytest.approx(0.0)
    )
    assert (
        planificateur.local_balance(attaquant, cible, make_battle([attaquant, cible, pres])) > 0.0
    )


def test_les_fuyards_ne_comptent_dans_aucun_camp(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Une unite qui rompt ne se bat plus : ni menace, ni renfort."""
    attaquant = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 200.0)
    cible = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0)
    fuyard = make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 20.0, is_routing=True)
    etat = make_battle([attaquant, cible, fuyard])

    assert Planner().local_balance(attaquant, cible, etat) == pytest.approx(0.0)


def test_a_distance_egale_on_prefere_la_cible_ou_l_on_est_soutenu(  # type: ignore[no-untyped-def]
    make_unit, make_battle
) -> None:
    """**Le defaut mesure en jeu.** 65 % des melees livrees en inferiorite
    locale : a valeur et distance egales, il faut choisir le cote ou la ligne
    tient, pas celui ou l'on sera deux contre trois."""
    attaquant = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, z=0.0)
    soutenue = make_unit("e_soutenue", Side.ENEMY, UnitRole.MELEE_INFANTRY, 100.0)
    isolee = make_unit("e_isolee", Side.ENEMY, UnitRole.MELEE_INFANTRY, -100.0)
    etat = make_battle(
        [
            attaquant,
            soutenue,
            isolee,
            # deux allies deja au contact du cote de `soutenue`
            make_unit("a2", Side.ALLY, UnitRole.MELEE_INFANTRY, 110.0),
            make_unit("a3", Side.ALLY, UnitRole.MELEE_INFANTRY, 120.0),
            # deux ennemis groupes autour de `isolee`
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, -110.0),
            make_unit("e3", Side.ENEMY, UnitRole.MELEE_INFANTRY, -120.0),
        ]
    )

    planificateur = Planner()
    assert planificateur.select_target(attaquant, etat) is soutenue


# --- achever plutot qu'egratigner ----------------------------------------------


def test_une_cible_intacte_ne_vaut_aucune_prime_d_achevement(make_unit) -> None:  # type: ignore[no-untyped-def]
    intacte = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY)

    assert finishing_value(intacte) == 0.0


def test_la_prime_d_achevement_croit_quand_la_cible_agonise(make_unit) -> None:  # type: ignore[no-untyped-def]
    """Ce qui compte, ce sont les hommes tombes, pas les blessures moyennes.

    Une unite meurt dans Total War quand il ne lui reste plus personne : c'est le
    compte d'hommes qui approche de zero, pas la sante moyenne des survivants.
    """
    entamee = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, entity_ratio=0.35)
    agonisante = make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, entity_ratio=0.10)

    assert 0.0 < finishing_value(entamee) < finishing_value(agonisante) <= 1.0


def test_une_unite_en_deroute_est_a_achever(make_unit) -> None:  # type: ignore[no-untyped-def]
    """**La penalite sur les fuyards etait a l'envers.** Une unite qui rompt est
    celle qu'un dernier choc detruit pour de bon ; la laisser partir, c'est la
    revoir ralliee."""
    debout = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY)
    en_fuite = make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, is_routing=True)

    assert finishing_value(debout) == 0.0
    assert finishing_value(en_fuite) > 0.5


def test_a_distance_egale_on_acheve_l_unite_entamee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """**Le defaut mesure en jeu** : 9,77 unites-equivalent de degats etales sur
    dix-neuf regiments, aucun abattu. Des degats etales ne rendent rien."""
    attaquant = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0)
    entamee = make_unit("e_entamee", Side.ENEMY, UnitRole.MELEE_INFANTRY, 60.0, health_ratio=0.15)
    intacte = make_unit("e_intacte", Side.ENEMY, UnitRole.MELEE_INFANTRY, -60.0)
    etat = make_battle([attaquant, entamee, intacte])

    assert Planner().select_target(attaquant, etat) is entamee


def test_le_soutien_est_encourage_puis_l_entassement_penalise() -> None:
    """L'ancienne version retirait 0,20 par allie, sans palier : une pression de
    dispersion permanente, et des degats etales sur toute la ligne adverse."""
    planificateur = Planner()

    assert planificateur.focus_bonus(0) == 0.0
    # Concentrer paye...
    assert planificateur.focus_bonus(1) > 0.0
    assert planificateur.focus_bonus(2) > planificateur.focus_bonus(1)
    # ... jusqu'a ce que ce ne soit plus de la concentration mais un embouteillage.
    assert planificateur.focus_bonus(6) < planificateur.focus_bonus(2)


def test_deux_allies_deja_engages_attirent_le_troisieme(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """A distance et valeur egales, renforcer un regiment qu'on est en train de
    faire tomber vaut mieux qu'en entamer un neuf."""
    attaquant = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0)
    prise = make_unit("e_prise", Side.ENEMY, UnitRole.MELEE_INFANTRY, 60.0)
    neuve = make_unit("e_neuve", Side.ENEMY, UnitRole.MELEE_INFANTRY, -60.0)
    etat = make_battle([attaquant, prise, neuve])

    choix = Planner().select_target(attaquant, etat, assignments={"e_prise": 2})
    assert choix is prise


def test_une_cible_que_le_jeu_refusera_n_est_pas_choisie(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """**Sinon l'unite reste plantee toute la bataille.** Le Lua refuse
    `uc:attack_unit` quand `is_valid_target()` est faux, et ce drapeau reste faux
    durablement : trois ennemis l'ont ete 665, 644 et 644 fois sur deux batailles.
    Une unite lancee sur l'une d'elles recoit un refus par seconde."""
    attaquant = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0)
    interdite = make_unit(
        "e_interdite", Side.ENEMY, UnitRole.ARTILLERY, 20.0, metadata={"targetable": False}
    )
    atteignable = make_unit("e_ok", Side.ENEMY, UnitRole.MELEE_INFANTRY, 150.0)
    etat = make_battle([attaquant, interdite, atteignable])

    # L'artillerie toute proche serait le choix evident sans ce garde-fou.
    assert Planner().select_target(attaquant, etat) is atteignable


def test_l_absence_du_drapeau_vaut_attaquable(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Hors du pont — au banc, en test — la question ne se pose pas."""
    cible = make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY)

    assert can_be_attacked(cible)


def test_une_cible_interdite_compte_toujours_dans_le_rapport_de_forces(  # type: ignore[no-untyped-def]
    make_unit, make_battle
) -> None:
    """Elle est sur le champ de bataille et elle menace : on ne peut simplement
    pas lui donner de coup."""
    etat = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0),
            make_unit(
                "e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 20.0, metadata={"targetable": False}
            ),
        ]
    )

    assert len(etat.enemies()) == 1


# --- tenir sa cible : une manoeuvre dure plus qu'un tour de boucle -------------


def test_la_cavalerie_garde_sa_cible_quand_celle_ci_entre_en_melee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """**Le defaut qui a perdu la bataille `a1274d62`.**

    Le vivier du contournement — tireurs adverses ni en deroute ni au contact —
    change sans arret. La cavalerie a recu dix cibles differentes en cent trente
    secondes, parcouru 1 944 m contre 500 a 800 m pour le reste de l'armee,
    n'a acheve aucun contournement, et a rompu la premiere.
    """
    planificateur = Planner()
    cavalier = make_unit("a_cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0)
    premiere = make_unit("e_arc1", Side.ENEMY, UnitRole.RANGED_INFANTRY, 200.0)
    seconde = make_unit("e_arc2", Side.ENEMY, UnitRole.RANGED_INFANTRY, 30.0)

    libre = make_battle([cavalier, premiere, seconde])
    choisie = planificateur.committed_target(cavalier, libre, candidates=[premiere])
    assert choisie is premiere

    # La cible entre en melee : elle quitte le vivier, mais la course continue.
    au_contact = make_unit("e_arc1", Side.ENEMY, UnitRole.RANGED_INFANTRY, 200.0, is_engaged=True)
    etat = make_battle([cavalier, au_contact, seconde])
    tenue = planificateur.committed_target(cavalier, etat, candidates=[seconde])

    assert tenue is not None and tenue.id == "e_arc1", "la cavalerie a change de cible en route"


def test_une_cible_qui_rompt_libere_l_engagement(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """On n'abandonne que pour une raison qui ne se retourne pas."""
    planificateur = Planner()
    cavalier = make_unit("a_cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0)
    premiere = make_unit("e_arc1", Side.ENEMY, UnitRole.RANGED_INFANTRY, 200.0)
    seconde = make_unit("e_arc2", Side.ENEMY, UnitRole.RANGED_INFANTRY, 30.0)

    planificateur.committed_target(
        cavalier, make_battle([cavalier, premiere, seconde]), candidates=[premiere]
    )

    en_fuite = make_unit("e_arc1", Side.ENEMY, UnitRole.RANGED_INFANTRY, 200.0, is_routing=True)
    etat = make_battle([cavalier, en_fuite, seconde])
    suivante = planificateur.committed_target(cavalier, etat, candidates=[seconde])

    assert suivante is seconde


def test_une_cible_devenue_inattaquable_libere_l_engagement(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Sinon l'unite reste engagee sur une cible que le jeu refusera toujours."""
    planificateur = Planner()
    cavalier = make_unit("a_cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0)
    premiere = make_unit("e_arc1", Side.ENEMY, UnitRole.RANGED_INFANTRY, 200.0)
    seconde = make_unit("e_arc2", Side.ENEMY, UnitRole.RANGED_INFANTRY, 30.0)

    planificateur.committed_target(
        cavalier, make_battle([cavalier, premiere, seconde]), candidates=[premiere]
    )

    interdite = make_unit(
        "e_arc1", Side.ENEMY, UnitRole.RANGED_INFANTRY, 200.0, metadata={"targetable": False}
    )
    etat = make_battle([cavalier, interdite, seconde])

    assert planificateur.committed_target(cavalier, etat, candidates=[seconde]) is seconde
