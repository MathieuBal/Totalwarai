"""Effectifs initiaux deduits de l'observation.

Le jeu ne donne que le nombre de survivants, jamais l'effectif nominal. Sans
denominateur, quarante hommes debout peuvent etre une unite intacte comme une
unite a moitie detruite — et `effective_strength`, qui multiplie effectifs et
sante, s'en trouve fausse dans un rapport du simple au double.
"""

from __future__ import annotations

import pytest

from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.roster import RosterMemory
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side


def _unite(unit_id: str, men: int | None, hitpoints: float | None = None) -> ProbeUnitObservation:
    return ProbeUnitObservation(
        unit_id=unit_id,
        position=Vector3(0.0, 0.0, 0.0),
        men_alive=men,
        hitpoints=hitpoints,
    )


def _etat(*unites: ProbeUnitObservation) -> ProbeBattleState:
    return ProbeBattleState(allies=unites)


def test_le_maximum_observe_sert_d_effectif_initial() -> None:
    """Au deploiement l'unite est au complet : le maximum vu est l'effectif."""
    memoire = RosterMemory()
    memoire.observe(_etat(_unite("1002", 80)))
    memoire.observe(_etat(_unite("1002", 64)))
    memoire.observe(_etat(_unite("1002", 20)))

    assert memoire.initial_men("1002") == 80
    assert memoire.entity_ratio(_unite("1002", 20)) == pytest.approx(0.25)


def test_une_unite_ne_regagne_pas_d_hommes() -> None:
    """Le maximum ne redescend jamais, meme si un etat arrive dans le desordre."""
    memoire = RosterMemory()
    memoire.observe(_etat(_unite("1002", 30)))
    memoire.observe(_etat(_unite("1002", 80)))
    memoire.observe(_etat(_unite("1002", 40)))

    assert memoire.initial_men("1002") == 80


def test_une_unite_jamais_vue_ne_donne_aucun_rapport() -> None:
    """Mieux vaut `None` qu'un rapport calcule sur un denominateur invente."""
    assert RosterMemory().entity_ratio(_unite("9999", 40)) is None


def test_un_effectif_absent_ne_donne_aucun_rapport() -> None:
    memoire = RosterMemory()
    memoire.observe(_etat(_unite("1001", None)))
    assert memoire.entity_ratio(_unite("1001", None)) is None
    assert memoire.initial_men("1001") is None


def test_les_ennemis_sont_suivis_aussi() -> None:
    """Le rapport de force depend autant de leurs pertes que des notres."""
    memoire = RosterMemory()
    memoire.observe(ProbeBattleState(allies=(_unite("1002", 80),), enemies=(_unite("2001", 120),)))
    assert memoire.initial_men("2001") == 120


# --- effet sur la puissance de combat ----------------------------------------


def test_le_compte_d_hommes_prime_sur_la_sante() -> None:
    """Une unite reduite au quart doit valoir le quart, quelle que soit sa sante.

    `unary_hitpoints` peut valoir la fraction d'unite restante ou la sante
    moyenne des survivants — les deux divergent completement. Le compte
    d'hommes, lui, ne souffre d'aucune ambiguite.
    """
    memoire = RosterMemory()
    memoire.observe(_etat(_unite("1002", 80, hitpoints=1.0)))

    survivants = _unite("1002", 20, hitpoints=1.0)  # vingt hommes en pleine forme
    ratio = memoire.entity_ratio(survivants)
    assert ratio is not None

    domaine = survivants.to_unit_state(Side.ALLY, entity_ratio=ratio)
    assert domaine.entity_ratio == pytest.approx(0.25)
    assert domaine.effective_strength == pytest.approx(0.25)


def test_sans_compte_d_hommes_la_sante_prend_le_relais() -> None:
    """Un seigneur n'a qu'une entite : la sante est alors le seul signal."""
    seigneur = _unite("1001", None, hitpoints=0.3)
    domaine = seigneur.to_unit_state(Side.ALLY)
    assert domaine.entity_ratio == pytest.approx(0.3)


def test_l_etat_de_bataille_applique_les_rapports() -> None:
    memoire = RosterMemory()
    depart = _etat(_unite("1002", 80, hitpoints=1.0))
    memoire.observe(depart)

    plus_tard = _etat(_unite("1002", 8, hitpoints=1.0))
    memoire.observe(plus_tard)
    ratios = {
        unite.unit_id: ratio
        for unite in plus_tard.allies
        if (ratio := memoire.entity_ratio(unite)) is not None
    }

    domaine = plus_tard.to_battle_state(entity_ratios=ratios)
    unite = domaine.unit("1002")
    assert unite is not None
    assert unite.effective_strength == pytest.approx(0.1)
