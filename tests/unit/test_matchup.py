"""A qui l'on avait affaire, et en quel nombre.

Huit batailles reelles, huit defaites, l'adversaire finissant a 92 ou 100 % de
ses unites debout. Lu comme un verdict sur le pilotage, cela dirait que l'IA du
jeu joue tres mal. Il manque a ce raisonnement la seule chose qui permette de le
tenir : savoir si la bataille etait gagnable.
"""

from __future__ import annotations

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState
from totalwar_ai.learning.matchup import summarise


def _unite(unit_id: str, side: Side, role: UnitRole = UnitRole.MELEE_INFANTRY) -> UnitState:
    return UnitState(id=unit_id, side=side, role=role, position=Vector3(0.0, 0.0, 0.0))


def _etats(*tours: tuple[UnitState, ...]) -> list[BattleState]:
    return [
        BattleState(battle_id="r", game_time=index * 1.0, units=unites)
        for index, unites in enumerate(tours)
    ]


def test_les_renforts_adverses_comptent_dans_le_rapport_de_forces() -> None:
    """Les ignorer ferait passer une inferiorite croissante pour un combat egal."""
    debut = (
        _unite("a1", Side.ALLY),
        _unite("a2", Side.ALLY),
        _unite("e1", Side.ENEMY),
        _unite("e2", Side.ENEMY),
    )
    plus_tard = (*debut, _unite("e3", Side.ENEMY), _unite("e4", Side.ENEMY))

    rapport = summarise(_etats(debut, plus_tard))
    assert rapport.ally_total == 2
    assert rapport.enemy_total == 4
    assert rapport.enemy_reinforcements == 2
    assert rapport.ratio == 2.0


def test_un_ecart_decisif_est_annonce_comme_tel() -> None:
    allies = tuple(_unite(f"a{index}", Side.ALLY) for index in range(12))
    ennemis = tuple(_unite(f"e{index}", Side.ENEMY) for index in range(20))

    rapport = summarise(_etats((*allies, *ennemis)))
    assert rapport.lopsided
    assert "n'est pas un test de tactique" in rapport.render()


def test_un_affrontement_serre_ne_declenche_pas_l_avertissement() -> None:
    allies = tuple(_unite(f"a{index}", Side.ALLY) for index in range(12))
    ennemis = tuple(_unite(f"e{index}", Side.ENEMY) for index in range(13))

    rapport = summarise(_etats((*allies, *ennemis)))
    assert not rapport.lopsided
    assert "n'est pas un test de tactique" not in rapport.render()


def test_la_composition_des_deux_camps_est_publiee() -> None:
    """Douze unites contre treize ne dit rien si les treize sont d'elite."""
    etats = _etats(
        (
            _unite("a1", Side.ALLY, UnitRole.MELEE_INFANTRY),
            _unite("a2", Side.ALLY, UnitRole.RANGED_INFANTRY),
            _unite("e1", Side.ENEMY, UnitRole.MONSTER),
            _unite("e2", Side.ENEMY, UnitRole.SHOCK_CAVALRY),
        )
    )

    rapport = summarise(etats)
    assert rapport.ally_roles[UnitRole.RANGED_INFANTRY] == 1
    assert rapport.enemy_roles[UnitRole.MONSTER] == 1
    assert "monster" in rapport.render()


def test_un_enregistrement_vide_le_dit() -> None:
    assert "Aucune unite" in summarise([]).render()
