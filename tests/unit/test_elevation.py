"""L'ecart d'altitude dit-il ce qu'il pretend dire ?

Les unites sont construites a la main plutot qu'avec la fabrique partagee :
celle-ci pose toujours l'altitude a zero, or c'est precisement la coordonnee
mesuree ici.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from totalwar_ai.domain.battle_state import BattlePhase, BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState
from totalwar_ai.learning.elevation import COMMANDING_HEIGHT, study


def _unite(
    unit_id: str,
    side: Side,
    altitude: float,
    *,
    role: UnitRole = UnitRole.MELEE_INFANTRY,
    tags: tuple[str, ...] = (),
) -> UnitState:
    return UnitState(
        id=unit_id,
        side=side,
        role=role,
        position=Vector3(0.0, altitude, 0.0),
        tags=tags,
    )


def _etat(allies: list[float], enemies: list[float], *, game_time: float = 30.0) -> BattleState:
    unites = [_unite(f"a{i}", Side.ALLY, alt) for i, alt in enumerate(allies)]
    unites += [_unite(f"e{i}", Side.ENEMY, alt) for i, alt in enumerate(enemies)]
    return BattleState(
        battle_id="test",
        sequence=1,
        game_time=game_time,
        phase=BattlePhase.ENGAGEMENT,
        units=tuple(unites),
    )


def test_tenir_la_hauteur_donne_un_ecart_positif() -> None:
    rapport = study([_etat([50.0, 52.0], [40.0, 42.0])])

    assert rapport.readings[0].advantage == pytest.approx(10.0)
    assert not rapport.readings[0].uphill


def test_combattre_en_contrebas_est_annonce() -> None:
    """**Le defaut mesure en jeu** : -5,25 m et -6,46 m a l'arrivee au contact."""
    etats = [_etat([40.0], [48.5]) for _ in range(12)]
    rapport = study(etats)

    assert rapport.average_advantage == pytest.approx(-8.5)
    assert rapport.uphill_share == 1.0
    assert rapport.fought_uphill
    assert "en contrebas" in rapport.render()


def test_un_terrain_plat_ne_conclut_rien() -> None:
    """Sur une carte plate il n'y a aucune hauteur a disputer."""
    etats = [_etat([20.0], [20.5]) for _ in range(12)]
    rapport = study(etats)

    assert rapport.relief < COMMANDING_HEIGHT
    assert not rapport.fought_uphill
    assert "Terrain plat" in rapport.render()


def test_une_volante_ne_renseigne_pas_le_sol() -> None:
    """Une volante relevee a 222 m quand le terrain est a 60 fausserait tout."""
    unites = [
        _unite("a1", Side.ALLY, 40.0),
        _unite("a2", Side.ALLY, 40.0),
        _unite("a_vol", Side.ALLY, 222.0, role=UnitRole.FLYING_UNIT, tags=("flying",)),
        _unite("e1", Side.ENEMY, 40.0),
    ]
    etat = BattleState(
        battle_id="test",
        sequence=1,
        game_time=30.0,
        phase=BattlePhase.ENGAGEMENT,
        units=tuple(unites),
    )

    assert study([etat]).readings[0].advantage == pytest.approx(0.0)


def test_un_seigneur_volant_ne_fausse_pas_la_mesure() -> None:
    """**Il n'est ni de role `flying_unit` ni etiquete `flying`.**

    Le classifieur cede la priorite a `lord` pour ne pas lui retirer sa
    protection : une telle unite passe au travers du filtre. C'est la mediane qui
    la neutralise.
    """
    unites = [
        _unite("a1", Side.ALLY, 40.0),
        _unite("a2", Side.ALLY, 41.0),
        _unite("a3", Side.ALLY, 39.0),
        # Un prince demon volant, ni marque ni etiquete.
        _unite("a_lord", Side.ALLY, 220.0, role=UnitRole.LORD),
        _unite("e1", Side.ENEMY, 40.0),
    ]
    etat = BattleState(
        battle_id="test",
        sequence=1,
        game_time=30.0,
        phase=BattlePhase.ENGAGEMENT,
        units=tuple(unites),
    )

    # Mediane de (39, 40, 41, 220) = 40,5 : le point aberrant ne pese presque rien.
    assert study([etat]).readings[0].advantage == pytest.approx(0.5)


def test_un_camp_absent_ne_produit_pas_d_avantage_imaginaire() -> None:
    """Comparer notre ligne a rien du tout inventerait une hauteur."""
    assert study([_etat([50.0], [])]).readings == []


def test_les_unites_mortes_ne_comptent_pas() -> None:
    unites = [
        _unite("a1", Side.ALLY, 50.0),
        _unite("e1", Side.ENEMY, 40.0),
        UnitState(
            id="e_mort",
            side=Side.ENEMY,
            role=UnitRole.MELEE_INFANTRY,
            position=Vector3(0.0, 200.0, 0.0),
            entity_ratio=0.0,
            health_ratio=0.0,
        ),
    ]
    etat = BattleState(
        battle_id="test",
        sequence=1,
        game_time=30.0,
        phase=BattlePhase.ENGAGEMENT,
        units=tuple(unites),
    )

    assert study([etat]).readings[0].advantage == pytest.approx(10.0)


def test_le_deroule_est_decoupe_par_minute() -> None:
    """La hauteur se prend pendant l'approche : la moyenne globale le masquerait."""
    etats = [
        _etat([50.0], [40.0], game_time=30.0),
        _etat([40.0], [55.0], game_time=450.0),
    ]
    tranches = study(etats).by_slice()

    assert [numero for numero, _, _ in tranches] == [0, 7]
    assert [round(ecart, 1) for _, _, ecart in tranches] == [10.0, -15.0]


def test_trop_peu_de_releves_ne_conclut_pas() -> None:
    rapport = study([_etat([40.0], [60.0])])

    assert rapport.uphill_share == 1.0
    assert not rapport.measured
    assert not rapport.fought_uphill


def test_le_verdict_juge_l_approche_et_non_la_bataille_entiere() -> None:
    """**Sans cette distinction, la mesure disait le contraire de la verite.**

    Sur `854ebefb`, l'agent est arrive au contact a -11 m, puis a fini a +11 m
    parce que ses unites en deroute refluaient vers la hauteur. La moyenne des
    deux vaut -2 m et ne decrit aucun moment de la bataille : le verdict passait
    a « la hauteur ne nous etait pas defavorable » pour une bataille livree en
    contrebas de bout en bout.
    """
    approche = [_etat([30.0], [41.0], game_time=float(i)) for i in range(12)]
    apres = []
    for i in range(12):
        etat = _etat([60.0], [49.0], game_time=300.0 + i)
        au_contact = tuple(
            unite if unite.side is Side.ENEMY else replace(unite, is_engaged=True)
            for unite in etat.units
        )
        apres.append(replace(etat, units=au_contact))

    rapport = study([*approche, *apres])

    # La bataille entiere ne dit rien : les deux moities s'annulent.
    assert rapport.average_advantage == pytest.approx(0.0)
    # L'approche, elle, etait franchement dominee — et c'est elle qui juge.
    assert rapport.approach_advantage == pytest.approx(-11.0)
    assert rapport.fought_uphill


def test_une_bataille_sans_contact_juge_sur_tout_ce_qu_on_a() -> None:
    """Faute de contact, il n'y a pas d'approche a isoler : on prend le tout."""
    rapport = study([_etat([30.0], [40.0]) for _ in range(12)])

    assert rapport.approach_advantage == pytest.approx(-10.0)
    assert rapport.fought_uphill
