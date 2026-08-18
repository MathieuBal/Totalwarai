"""La vitesse observee, et l'assaut qui arrive groupe.

L'agent n'avait aucune notion de vitesse : il composait ses assauts par distance
pure, et le simulateur va de 1,6 m/s pour l'artillerie a 8,5 pour la cavalerie de
choc. Ces tests pincent la mesure et la composition, pas un reglage.
"""

from __future__ import annotations

import pytest

from totalwar_ai.agent.mobility import (
    DEFAULT_SPEED,
    MINIMUM_SPEED,
    ROLE_SPEED_PRIOR,
    MobilityTracker,
)
from totalwar_ai.agent.sectors import ASSAULT_WINDOW, commit, split_sectors
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole

FRONT = Vector3(0.0, 0.0, 1.0)


def test_une_unite_jamais_vue_porte_la_vitesse_par_defaut() -> None:
    """Demarrage a froid : au premier plan, rien n'a encore bouge.

    Il faut rendre un nombre plutot que refuser de composer un assaut — la
    degradation est douce, et le tri par temps redevient un tri par distance.
    """
    suivi = MobilityTracker()
    assert suivi.speed("inconnue") == DEFAULT_SPEED
    assert not suivi.observed("inconnue")


def test_la_vitesse_se_deduit_du_deplacement(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Vingt metres en deux secondes font dix metres par seconde."""
    suivi = MobilityTracker()
    suivi.observe(
        make_battle([make_unit("a", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0, 0.0)], game_time=0.0)
    )
    suivi.observe(
        make_battle([make_unit("a", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0, 20.0)], game_time=2.0)
    )
    assert suivi.observed("a")
    assert suivi.speed("a") == pytest.approx(10.0)


def test_un_tassement_de_formation_n_est_pas_une_marche(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Sous le seuil d'immobilite, le deplacement ne renseigne rien.

    Une unite au contact pietine : la compter ferait tomber sa vitesse, puis son
    ETA a l'infini, et elle serait exclue de tout assaut — y compris quand c'est
    precisement elle qu'il faudrait envoyer.
    """
    suivi = MobilityTracker()
    suivi.observe(
        make_battle([make_unit("a", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0)], game_time=0.0)
    )
    suivi.observe(
        make_battle([make_unit("a", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 1.0)], game_time=1.0)
    )
    assert not suivi.observed("a"), "un metre n'est pas une marche"
    assert suivi.speed("a") == DEFAULT_SPEED


def test_la_vitesse_ne_tombe_jamais_a_zero(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Sans plancher, un ETA infini exclurait l'unite de tout assaut a jamais."""
    suivi = MobilityTracker()
    suivi.observe(
        make_battle([make_unit("a", Side.ALLY, UnitRole.ARTILLERY, 0.0, 0.0)], game_time=0.0)
    )
    suivi.observe(
        make_battle([make_unit("a", Side.ALLY, UnitRole.ARTILLERY, 0.0, 4.0)], game_time=100.0)
    )
    assert suivi.speed("a") >= MINIMUM_SPEED


def test_une_unite_lente_et_une_rapide_ont_des_eta_tres_differents(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Le defaut que ce module corrige, dans sa forme la plus nue.

    A distance egale du secteur, l'artillerie et la cavalerie comptaient pour la
    meme chose dans le numerateur du rapport local. L'une arrive en vingt-quatre
    secondes, l'autre en deux minutes, et le rapport annonce supposait les deux
    presentes.
    """
    suivi = MobilityTracker()
    lent = make_unit("art", Side.ALLY, UnitRole.ARTILLERY, 0.0, 0.0)
    rapide = make_unit("cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0, 0.0)
    suivi.observe(make_battle([lent, rapide], game_time=0.0))
    suivi.observe(
        make_battle(
            [
                make_unit("art", Side.ALLY, UnitRole.ARTILLERY, 0.0, 4.0),
                make_unit("cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0, 20.0),
            ],
            game_time=2.0,
        )
    )
    cible = Vector3(0.0, 0.0, 200.0)
    assert suivi.eta(lent, cible) > 3 * suivi.eta(rapide, cible)


def test_reset_oublie_la_bataille_precedente(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    suivi = MobilityTracker()
    suivi.observe(
        make_battle([make_unit("a", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0)], game_time=0.0)
    )
    suivi.observe(
        make_battle([make_unit("a", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 20.0)], game_time=2.0)
    )
    assert suivi.observed("a")
    suivi.reset()
    assert not suivi.observed("a")


def test_une_unite_qui_arriverait_trop_tard_ne_compte_pas(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Un assaut n'est fort que si ses unites arrivent groupees.

    Trois unites separees de vingt secondes livrent trois combats a un contre un,
    pas un combat a trois contre un : c'est la defaite en detail, appliquee a
    nous-memes et de notre propre initiative.
    """
    ennemis = [make_unit("e0", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 150.0)]
    # Trois unites au meme endroit, donc a ETA egal si la vitesse est ignoree.
    proches = [
        make_unit(f"a{index}", Side.ALLY, UnitRole.MELEE_INFANTRY, float(index * 5), 100.0)
        for index in range(3)
    ]
    trainarde = make_unit("lente", Side.ALLY, UnitRole.ARTILLERY, 0.0, 100.0)
    allies = [*proches, trainarde]

    suivi = MobilityTracker()
    suivi.observe(make_battle([*allies, *ennemis], game_time=0.0))
    # Les trois marchent, la trainarde se traine.
    bouge = [
        make_unit(f"a{index}", Side.ALLY, UnitRole.MELEE_INFANTRY, float(index * 5), 120.0)
        for index in range(3)
    ]
    bouge.append(make_unit("lente", Side.ALLY, UnitRole.ARTILLERY, 0.0, 104.0))
    suivi.observe(make_battle([*bouge, *ennemis], game_time=4.0))

    carte = split_sectors(make_battle([*allies, *ennemis]), FRONT, allies, mobility=suivi)
    secteur = carte.sectors[0]
    assaut = commit(
        secteur, make_battle([*allies, *ennemis]), allies, game_time=0.0, mobility=suivi
    )
    # **Pas de `if assaut is not None`.** Un test qui ne verifie rien quand la
    # manoeuvre n'a pas lieu passerait aussi bien avec la primitive desactivee :
    # c'est un instrument incapable d'echouer, et il y en a eu assez cette
    # session.
    assert assaut is not None, "le scenario doit produire un assaut"
    etas = [suivi.eta(u, secteur.centre) for u in allies if u.id in set(assaut.attackers)]
    assert max(etas) - min(etas) <= ASSAULT_WINDOW + 1e-6, (
        "les assaillants retenus doivent arriver dans la meme fenetre"
    )
    # L'artillerie arrive a 50 s quand l'infanterie y est en 10 : elle ne doit
    # pas compter dans un rapport local qu'elle ne renforcera pas a temps.
    assert "lente" not in assaut.attackers


def test_un_a_priori_de_role_donne_son_avantage_a_la_cavalerie(make_unit) -> None:
    """Sans lui, la branche de charge de cavalerie etait du code mort.

    L'assaut se compose au **premier** plan de la bataille, quand rien n'a encore
    ete observe. Tout le monde portait alors la meme vitesse, la cavalerie postee
    sur l'aile paraissait plus lointaine que l'infanterie, et n'etait jamais
    retenue : mesure, zero charge de cavalerie sur tout le banc alors que le code
    existait.
    """
    suivi = MobilityTracker()
    cavalerie = make_unit("cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0, 0.0)
    infanterie = make_unit("inf", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0)
    artillerie = make_unit("art", Side.ALLY, UnitRole.ARTILLERY, 0.0, 0.0)
    cible = Vector3(0.0, 0.0, 200.0)

    # L'ordre est ce qui compte ; les grandeurs se corrigent a l'observation.
    assert suivi.eta(cavalerie, cible) < suivi.eta(infanterie, cible)
    assert suivi.eta(infanterie, cible) < suivi.eta(artillerie, cible)


def test_l_observation_prime_sur_l_a_priori(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Une cavalerie qu'on a vue trainer n'est plus presumee rapide."""
    suivi = MobilityTracker()
    suivi.observe(
        make_battle([make_unit("cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0, 0.0)], game_time=0.0)
    )
    suivi.observe(
        make_battle([make_unit("cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0, 4.0)], game_time=4.0)
    )
    presume = ROLE_SPEED_PRIOR[UnitRole.SHOCK_CAVALRY]
    assert suivi.speed("cav", UnitRole.SHOCK_CAVALRY) < presume
