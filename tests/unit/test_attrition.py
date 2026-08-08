"""Nos degats ont-ils achete quelque chose ?"""

from __future__ import annotations

import pytest

from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.learning.attrition import study


def test_une_unite_qui_disparait_est_comptee_detruite(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Le jeu retire les unites detruites de ses listes : attendre un compte
    d'hommes a zero ne verrait jamais aucune mort."""
    intacte = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY),
        ]
    )
    entamee = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, health_ratio=0.4),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY),
        ]
    )
    apres = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY),
        ]
    )
    rapport = study([intacte, entamee, apres])

    assert rapport.destroyed == {"e1"}
    # Une unite detruite a perdu toute sa barre, pas seulement ce qu'on lui
    # avait vu perdre avant de disparaitre.
    assert rapport.damage["e1"] == pytest.approx(1.0)


def test_les_degats_sont_comptes_sur_toute_la_bataille(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    etats = [
        make_battle([make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, health_ratio=part)])
        for part in (1.0, 0.8, 0.55)
    ]
    rapport = study(etats)

    assert rapport.damage["e1"] == pytest.approx(0.45)
    assert rapport.destroyed == set()


def test_une_egratignure_ne_compte_pas_comme_un_engagement(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    etats = [
        make_battle([make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, health_ratio=part)])
        for part in (1.0, 0.995)
    ]

    assert study(etats).engaged == 0


def test_des_degats_etales_sont_annonces(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """**Le defaut mesure en jeu** : 9,77 unites-equivalent, zero regiment abattu."""
    depart = [
        make_unit(f"e{i}", Side.ENEMY, UnitRole.MELEE_INFANTRY, float(i) * 30.0) for i in range(10)
    ]
    arrivee = [
        make_unit(f"e{i}", Side.ENEMY, UnitRole.MELEE_INFANTRY, float(i) * 30.0, health_ratio=0.5)
        for i in range(10)
    ]
    rapport = study([make_battle(depart), make_battle(arrivee)])

    assert rapport.total_damage == pytest.approx(5.0)
    assert not rapport.destroyed
    assert rapport.yield_per_damage == 0.0
    assert rapport.spread
    assert "degats sont etales" in rapport.render()


def test_des_degats_concentres_ne_sont_pas_annonces_comme_etales(  # type: ignore[no-untyped-def]
    make_unit, make_battle
) -> None:
    """Les memes degats, portes au meme endroit, abattent cinq regiments."""
    depart = [
        make_unit(f"e{i}", Side.ENEMY, UnitRole.MELEE_INFANTRY, float(i) * 30.0) for i in range(10)
    ]
    arrivee = [
        make_unit(f"e{i}", Side.ENEMY, UnitRole.MELEE_INFANTRY, float(i) * 30.0)
        for i in range(5, 10)
    ]
    rapport = study([make_battle(depart), make_battle(arrivee)])

    assert rapport.total_damage == pytest.approx(5.0)
    assert len(rapport.destroyed) == 5
    assert rapport.yield_per_damage == pytest.approx(1.0)
    assert not rapport.spread


def test_les_deroutes_sont_comptees_a_part(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Rompre n'est pas mourir : une unite ralliee revient se battre."""
    etat = make_battle([make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, is_routing=True)])
    rapport = study([etat])

    assert rapport.routed == {"e1"}
    assert not rapport.destroyed


def test_une_bataille_sans_adversaire_ne_dit_rien(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    rapport = study([make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])])

    assert rapport.seen == 0
    assert not rapport.spread
    assert "Aucune unite adverse" in rapport.render()
