"""Le rapport de forces local dit-il ce qu'il pretend dire ?"""

from __future__ import annotations

from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.learning.concentration import OUTNUMBERED_SHARE, study


def _melee(make_unit, make_battle, ennemis: int, allies: int, *, game_time: float = 30.0):  # type: ignore[no-untyped-def]
    """Une unite au contact, entouree du voisinage demande."""
    unites = [make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, is_engaged=True)]
    unites += [
        make_unit(f"e{i}", Side.ENEMY, UnitRole.MELEE_INFANTRY, 5.0 * (i + 1))
        for i in range(ennemis)
    ]
    unites += [
        make_unit(f"a{i + 2}", Side.ALLY, UnitRole.MELEE_INFANTRY, -5.0 * (i + 1))
        for i in range(allies)
    ]
    return make_battle(unites, game_time=game_time)


def test_un_contre_un_est_la_parite(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    rapport = study([_melee(make_unit, make_battle, ennemis=1, allies=0)])

    assert rapport.median_ratio == 1.0
    assert not rapport.engagements[0].outnumbered


def test_deux_ennemis_pour_une_unite_est_une_inferiorite(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """**Le defaut mesure en jeu** : mediane 1,50 et 1,67, pics a 2,00 et 3,00."""
    rapport = study([_melee(make_unit, make_battle, ennemis=2, allies=0)])

    assert rapport.median_ratio == 2.0
    assert rapport.engagements[0].outnumbered


def test_un_allie_proche_retablit_le_rapport(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    rapport = study([_melee(make_unit, make_battle, ennemis=2, allies=1)])

    assert rapport.median_ratio == 1.0
    assert not rapport.engagements[0].outnumbered


def test_les_allies_trop_loin_ne_soutiennent_pas(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Le soutien est local : c'est tout l'objet de la mesure."""
    etat = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, is_engaged=True),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 10.0),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 20.0),
            make_unit("a2", Side.ALLY, UnitRole.MELEE_INFANTRY, 500.0),
        ]
    )

    assert study([etat]).median_ratio == 2.0


def test_les_fuyards_ne_comptent_dans_aucun_camp(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Pendant la cascade, la moitie de la carte fuit : les compter fausserait
    la mesure exactement au moment ou elle importe."""
    etat = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, is_engaged=True),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 10.0),
            make_unit("e2", Side.ENEMY, UnitRole.MELEE_INFANTRY, 20.0, is_routing=True),
            make_unit("a2", Side.ALLY, UnitRole.MELEE_INFANTRY, -10.0, is_routing=True),
        ]
    )

    # Un ennemi debout, aucun allie debout pour soutenir : parite.
    assert study([etat]).median_ratio == 1.0


def test_une_unite_hors_melee_n_est_pas_relevee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    etat = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0),
            make_unit("e1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 10.0),
        ]
    )

    assert study([etat]).engagements == []


def test_une_bataille_sans_melee_ne_conclut_rien(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Ne pas combattre n'est pas combattre concentre."""
    rapport = study([])

    assert not rapport.measured
    assert not rapport.defeated_in_detail
    assert "Aucune melee" in rapport.render()


def test_trop_peu_de_melees_ne_conclut_pas(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    rapport = study([_melee(make_unit, make_battle, ennemis=3, allies=0)])

    assert rapport.outnumbered_share == 1.0
    assert not rapport.measured
    assert not rapport.defeated_in_detail, "une seule melee ne fait pas une defaite en detail"


def test_la_defaite_en_detail_est_annoncee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Les deux batailles perdues etaient a 65 % et 58 % de melees subies."""
    etats = [_melee(make_unit, make_battle, ennemis=2, allies=0) for _ in range(12)]
    rapport = study(etats)

    assert rapport.outnumbered_share >= OUTNUMBERED_SHARE
    assert rapport.measured
    assert rapport.defeated_in_detail
    assert "battus en detail" in rapport.render()


def test_un_combat_concentre_n_est_pas_annonce_comme_une_defaite(  # type: ignore[no-untyped-def]
    make_unit, make_battle
) -> None:
    etats = [_melee(make_unit, make_battle, ennemis=1, allies=1) for _ in range(12)]
    rapport = study(etats)

    assert rapport.measured
    assert not rapport.defeated_in_detail
    assert "concentre" in rapport.render()


def test_le_deroule_est_decoupe_par_minute(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Sans decoupage, la moyenne masquerait le pic a 2,00 pendant la cascade."""
    etats = [
        _melee(make_unit, make_battle, ennemis=1, allies=0, game_time=30.0),
        _melee(make_unit, make_battle, ennemis=4, allies=0, game_time=450.0),
    ]
    tranches = study(etats).by_slice()

    assert [numero for numero, _, _ in tranches] == [0, 7]
    assert [rapport for _, _, rapport in tranches] == [1.0, 4.0]
