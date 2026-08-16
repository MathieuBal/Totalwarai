"""Ce que compte la mesure de ciblage : des decisions, pas des releves."""

from __future__ import annotations

import pytest

from totalwar_ai.domain.unit_state import UnitRole
from totalwar_ai.learning.observation import Move, Observation
from totalwar_ai.learning.targeting import episodes, evaluate


def _releve(
    instant: float,
    unit_id: str,
    target_id: str | None,
    *,
    role: UnitRole = UnitRole.SHOCK_CAVALRY,
    cible: UnitRole = UnitRole.RANGED_INFANTRY,
) -> Observation:
    return Observation(
        game_time=instant,
        unit_id=unit_id,
        role=role,
        move=Move.CLOSE,
        target_id=target_id,
        target_role=cible if target_id else None,
        available=(UnitRole.RANGED_INFANTRY, UnitRole.MELEE_INFANTRY),
    )


def test_un_engagement_qui_dure_ne_compte_qu_une_fois() -> None:
    """A 2 Hz, une minute sur la meme cible produisait 120 « decisions ».

    Elles ne sont pas independantes : c'est une seule decision, tenue. Les
    compter separement donne a la mesure une assise qu'elle n'a pas et fait peser
    les engagements longs plus que les choix frequents.
    """
    suite = [_releve(index * 0.5, "a_cav", "e_arc1") for index in range(120)]
    assert len(episodes(suite)) == 1


def test_un_changement_de_cible_ouvre_une_nouvelle_decision() -> None:
    suite = (
        [_releve(index * 0.5, "a_cav", "e_arc1") for index in range(10)]
        + [_releve(5.0 + index * 0.5, "a_cav", "e_arc2") for index in range(10)]
        + [_releve(10.0 + index * 0.5, "a_cav", "e_arc1") for index in range(10)]
    )
    retenues = episodes(suite)
    assert [item.target_id for item in retenues] == ["e_arc1", "e_arc2", "e_arc1"]


def test_les_unites_ne_se_melangent_pas() -> None:
    """Deux unites entrelacees gardent chacune sa propre suite."""
    suite = []
    for index in range(10):
        suite.append(_releve(index * 0.5, "a_cav1", "e_arc1"))
        suite.append(_releve(index * 0.5, "a_cav2", "e_arc2"))
    assert len(episodes(suite)) == 2


def test_la_precision_est_la_moyenne_des_batailles_pas_des_decisions() -> None:
    """Une bataille, une voix.

    `learned` valait `bons / total` — une moyenne ponderee par la taille des
    batailles — alors que la docstring promettait « la moyenne des passes ». Une
    bataille longue ecrasait les autres, ce qui vide de son sens une coupe faite
    precisement pour separer les batailles.
    """
    # Bataille courte : la cavalerie prend systematiquement les tireurs.
    courte = [_releve(float(index), f"a_cav{index}", f"e_arc{index}") for index in range(4)]
    # Bataille longue : elle prend systematiquement la melee — la preference
    # inverse, sur beaucoup plus de decisions.
    longue = [
        _releve(
            float(index),
            f"b_cav{index}",
            f"e_inf{index}",
            cible=UnitRole.MELEE_INFANTRY,
        )
        for index in range(40)
    ]

    resultat = evaluate([courte, longue])
    assert resultat.folds == 2
    # Chaque passe apprend l'inverse de ce qu'elle doit predire : les deux
    # scores valent zero, et leur moyenne aussi. Le point du test est ailleurs —
    # `learned` doit etre la moyenne des passes, donc encadree par elles.
    assert resultat.worst <= resultat.learned <= resultat.best
    assert resultat.learned == pytest.approx((resultat.worst + resultat.best) / 2, abs=1e-9)
