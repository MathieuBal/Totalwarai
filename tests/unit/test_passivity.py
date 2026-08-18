"""La permission de passivite : ce qui la refuse, et ce qui l'accorde.

Ces tests pincent **deux faits independants** — l'adversaire ne saigne pas, et
il ne se rapproche pas — parce qu'un simple compteur de secondes etait
precisement la partie fausse de la regle rejetee a l'ADR 0015.
"""

from __future__ import annotations

from totalwar_ai.agent.passivity import PASSIVITY_SECONDS, PassivityWatch
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole

ANCRE = Vector3(0.0, 0.0, 0.0)


def _veille(make_unit, make_battle, releves):  # type: ignore[no-untyped-def]
    """Joue une suite de (instant, distance, force) et rend la veille."""
    veille = PassivityWatch()
    for instant, distance, force in releves:
        ennemi = make_unit(
            "e0", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, distance, entity_ratio=force
        )
        veille.observe(make_battle([ennemi], game_time=instant), ANCRE)
    return veille


def test_un_ennemi_qui_approche_n_est_jamais_passif(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Le fait qui separe « il attend » de « il arrive et je ne l'ai pas touche ».

    Sans lui, la permission serait accordee pendant toute phase d'approche
    silencieuse — exactement le cas ou attendre est la bonne reponse.
    """
    releves = [(float(i) * 10.0, 200.0 - float(i) * 20.0, 1.0) for i in range(12)]
    veille = _veille(make_unit, make_battle, releves)
    assert not veille.passive(releves[-1][0]), "un ennemi qui avance n'est pas inerte"


def test_un_ennemi_qui_saigne_n_est_jamais_passif(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Nos tirs mordent : attendre rapporte encore, et le compteur repart."""
    releves = [(float(i) * 10.0, 200.0, 1.0 - float(i) * 0.05) for i in range(12)]
    veille = _veille(make_unit, make_battle, releves)
    assert not veille.passive(releves[-1][0]), "un ennemi qui perd des hommes n'est pas inerte"


def test_un_ennemi_immobile_et_intact_finit_par_etre_passif(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Les deux faits reunis, et seulement alors."""
    releves = [(float(i) * 10.0, 200.0, 1.0) for i in range(12)]
    veille = _veille(make_unit, make_battle, releves)
    dernier = releves[-1][0]
    assert veille.passive_since(dernier) >= PASSIVITY_SECONDS
    assert veille.passive(dernier)


def test_la_permission_n_est_pas_accordee_avant_le_delai(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Un adversaire qui vient de s'arreter n'est pas encore un adversaire inerte."""
    releves = [(0.0, 200.0, 1.0), (10.0, 200.0, 1.0)]
    veille = _veille(make_unit, make_battle, releves)
    assert not veille.passive(10.0)


def test_un_rapprochement_reste_visible_apres_un_recul(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Le defaut du cliquet, pince ici.

    La veille gardait la **plus courte distance jamais atteinte**. Comparer a
    celle-la rend tout rapprochement ulterieur indetectable des qu'un des deux
    camps a recule une fois — sur `balanced_clash`, la distance passait de 35,6 m
    a 38,4 puis a 200, et l'approche restait fausse pour le reste de la bataille.
    Un ennemi qui se ressaisit et revient doit redevenir un fait nouveau.
    """
    releves = [
        (0.0, 100.0, 1.0),
        (10.0, 40.0, 1.0),  # il approche
        (20.0, 300.0, 1.0),  # nous reculons : il est loin
        (30.0, 300.0, 1.0),
        (40.0, 300.0, 1.0),
        (50.0, 300.0, 1.0),
        (60.0, 300.0, 1.0),
        (70.0, 300.0, 1.0),  # passif : rien depuis t=10
    ]
    veille = _veille(make_unit, make_battle, releves)
    assert veille.passive(70.0), "immobile et intact depuis soixante secondes"

    # Il revient. Le compteur doit repartir, alors que 200 m restent bien
    # au-dela des 40 m qu'il avait un jour atteints.
    ennemi = make_unit("e0", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 200.0, entity_ratio=1.0)
    veille.observe(make_battle([ennemi], game_time=80.0), ANCRE)
    assert not veille.passive(80.0), "un retour offensif est un fait nouveau"


def test_reset_oublie_la_bataille_precedente(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    releves = [(float(i) * 10.0, 200.0, 1.0) for i in range(12)]
    veille = _veille(make_unit, make_battle, releves)
    assert veille.passive(releves[-1][0])
    veille.reset()
    assert not veille.passive(releves[-1][0])
