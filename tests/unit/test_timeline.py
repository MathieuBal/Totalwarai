"""Ou une bataille a bascule, et ce qui s'y passait.

Un compte de fin de bataille dit qu'on a perdu, jamais pourquoi. Ces tests
verifient que le deroule designe le bon moment, et qu'il ne pretend jamais
donner une cause.
"""

from __future__ import annotations

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState
from totalwar_ai.learning.timeline import summarise


def _unite(
    unit_id: str, side: Side, role: UnitRole = UnitRole.MELEE_INFANTRY, *, melee: bool = False
) -> UnitState:
    return UnitState(
        id=unit_id,
        side=side,
        role=role,
        position=Vector3(0.0, 0.0, 0.0 if side is Side.ALLY else 200.0),
        is_engaged=melee,
    )


def _bataille(pertes: list[int], *, tours: int = 60) -> list[BattleState]:
    """Dix unites de chaque cote ; `pertes[i]` allies morts au tour i."""
    etats = []
    for index in range(tours):
        perdues = pertes[min(index, len(pertes) - 1)]
        unites = [_unite(f"a{n}", Side.ALLY) for n in range(10 - perdues)]
        unites += [_unite(f"e{n}", Side.ENEMY) for n in range(10)]
        etats.append(BattleState(battle_id="t", game_time=index * 5.0, units=unites))
    return etats


def test_la_bascule_designe_le_moment_ou_l_ecart_se_creuse() -> None:
    """Ce n'est pas « le moment ou l'on a perdu » : c'est le plus rapide."""
    # Rien pendant la premiere moitie, effondrement d'un coup au tour 30.
    pertes = [0] * 30 + [7] * 30
    deroule = summarise(_bataille(pertes))

    bascule = deroule.turning_point
    assert bascule is not None
    assert 140.0 <= bascule.start <= 160.0, f"bascule annoncee a {bascule.start:.0f} s"


def test_une_bataille_sans_perte_n_a_pas_de_bascule() -> None:
    deroule = summarise(_bataille([0]))
    assert deroule.turning_point is None
    assert "Aucune bascule" in deroule.render()


def test_le_deroule_signale_les_tireurs_au_contact() -> None:
    """La ou ils ne servent plus a rien et meurent vite."""
    etats = []
    for index in range(60):
        au_contact = index >= 30
        unites = [
            _unite("arc", Side.ALLY, UnitRole.RANGED_INFANTRY, melee=au_contact),
            *[_unite(f"a{n}", Side.ALLY) for n in range(9 if index < 30 else 3)],
            *[_unite(f"e{n}", Side.ENEMY) for n in range(10)],
        ]
        etats.append(BattleState(battle_id="t", game_time=index * 5.0, units=unites))

    rendu = summarise(etats).render()
    assert "tireur(s) au contact" in rendu


def test_le_deroule_ne_pretend_jamais_donner_une_cause() -> None:
    """Une correlation nommee vaut mieux qu'une cause inventee."""
    rendu = summarise(_bataille([0] * 30 + [7] * 30)).render()
    assert "Ce qui coincide (et non ce qui cause)" in rendu


def test_une_bataille_trop_courte_ne_produit_pas_de_deroule() -> None:
    assert summarise(_bataille([0], tours=5)).slices == []
    assert "trop courte" in summarise(_bataille([0], tours=5)).render()


def test_la_chute_du_seigneur_est_signalee() -> None:
    etats = []
    for index in range(60):
        unites = [_unite(f"a{n}", Side.ALLY) for n in range(10 if index < 30 else 4)]
        if index < 30:
            unites.append(_unite("lord", Side.ALLY, UnitRole.LORD))
        unites += [_unite(f"e{n}", Side.ENEMY) for n in range(10)]
        etats.append(BattleState(battle_id="t", game_time=index * 5.0, units=unites))

    rendu = summarise(etats).render()
    assert "SEIGNEUR TOMBE" in rendu
