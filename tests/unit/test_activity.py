"""Ce que chaque unite a fait de sa bataille.

Une unite restee en arriere a-t-elle tenu son role -- tirer de loin -- ou n'a-t-elle
recu aucun ordre ? Aucun compte global ne separe les deux, et les deux appellent
des corrections opposees.
"""

from __future__ import annotations

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState
from totalwar_ai.learning.activity import summarise


def _unite(
    unit_id: str,
    role: UnitRole,
    z: float,
    *,
    ammo: float = 0.0,
    melee: bool = False,
    vivante: bool = True,
) -> UnitState:
    return UnitState(
        id=unit_id,
        side=Side.ALLY,
        role=role,
        position=Vector3(0.0, 0.0, z),
        ammo_ratio=ammo,
        is_engaged=melee,
        entity_ratio=1.0 if vivante else 0.0,
        health_ratio=1.0 if vivante else 0.0,
    )


def _etats(*tours: tuple[UnitState, ...]) -> list[BattleState]:
    return [
        BattleState(battle_id="a", game_time=index * 0.5, units=unites)
        for index, unites in enumerate(tours)
    ]


def test_une_unite_qui_ne_fait_rien_est_declaree_inerte() -> None:
    """Ce n'est pas de la prudence : elle n'a recu aucun ordre."""
    etats = _etats(
        (_unite("arc", UnitRole.RANGED_INFANTRY, 0.0, ammo=1.0),),
        (_unite("arc", UnitRole.RANGED_INFANTRY, 0.2, ammo=1.0),),
        (_unite("arc", UnitRole.RANGED_INFANTRY, 0.1, ammo=1.0),),
    )

    fiche = summarise(etats).units[0]
    assert fiche.inert
    assert fiche.travelled == 0.0, "le tassement de formation a compte comme un trajet"


def test_un_tireur_immobile_qui_vide_son_carquois_n_est_pas_inerte() -> None:
    """Tirer de loin sans bouger est exactement son role."""
    etats = _etats(
        (_unite("arc", UnitRole.RANGED_INFANTRY, 0.0, ammo=1.0),),
        (_unite("arc", UnitRole.RANGED_INFANTRY, 0.0, ammo=0.6),),
        (_unite("arc", UnitRole.RANGED_INFANTRY, 0.0, ammo=0.2),),
    )

    fiche = summarise(etats).units[0]
    assert not fiche.inert
    assert round(fiche.ammo_spent, 3) == 0.8


def test_une_unite_qui_manoeuvre_n_est_pas_inerte() -> None:
    etats = _etats(
        (_unite("inf", UnitRole.MELEE_INFANTRY, 0.0),),
        (_unite("inf", UnitRole.MELEE_INFANTRY, 40.0),),
        (_unite("inf", UnitRole.MELEE_INFANTRY, 90.0),),
    )

    fiche = summarise(etats).units[0]
    assert not fiche.inert
    assert fiche.travelled == 90.0


def test_une_unite_au_contact_n_est_pas_inerte() -> None:
    """Une unite prise sur place se bat, meme sans avoir parcouru un metre."""
    etats = _etats(
        (_unite("inf", UnitRole.MELEE_INFANTRY, 0.0),),
        (_unite("inf", UnitRole.MELEE_INFANTRY, 0.0, melee=True),),
    )

    fiche = summarise(etats).units[0]
    assert not fiche.inert
    assert fiche.melee_share == 0.5


def test_le_rapport_nomme_les_unites_sans_ordre() -> None:
    etats = _etats(
        (
            _unite("inf", UnitRole.MELEE_INFANTRY, 0.0),
            _unite("arc", UnitRole.RANGED_INFANTRY, -50.0, ammo=1.0),
        ),
        (
            _unite("inf", UnitRole.MELEE_INFANTRY, 100.0),
            _unite("arc", UnitRole.RANGED_INFANTRY, -50.0, ammo=1.0),
        ),
    )

    rapport = summarise(etats)
    assert [item.unit_id for item in rapport.inert] == ["arc"]
    assert "INERTE" in rapport.render()
    assert "1 unite(s) n'ont pas suivi l'armee" in rapport.render()


def test_une_armee_entierement_active_le_dit() -> None:
    etats = _etats(
        (_unite("inf", UnitRole.MELEE_INFANTRY, 0.0),),
        (_unite("inf", UnitRole.MELEE_INFANTRY, 100.0),),
    )

    assert "Toutes les unites ont manoeuvre" in summarise(etats).render()


def test_une_unite_detruite_est_signalee() -> None:
    etats = _etats(
        (_unite("inf", UnitRole.MELEE_INFANTRY, 0.0, melee=True),),
        (_unite("inf", UnitRole.MELEE_INFANTRY, 0.0, melee=True, vivante=False),),
    )

    fiche = summarise(etats).units[0]
    assert not fiche.survived
    assert "detruite" in fiche.explain()


def test_une_unite_qui_tire_sans_jamais_suivre_est_signalee() -> None:
    """Le seuil absolu ne suffisait pas.

    Sur la premiere bataille reelle, trois unites de tir ont parcouru 4, 21 et
    21 metres pendant que le reste de l'armee en faisait entre 850 et 2 220.
    Elles n'etaient pas inertes -- elles ont tire un cinquieme de leur carquois
    quand l'ennemi est arrive sur elles -- mais elles n'ont jamais manoeuvre.
    """
    etats = []
    for index in range(20):
        etats.append(
            (
                # Le gros de l'armee marche.
                _unite("inf1", UnitRole.MELEE_INFANTRY, index * 50.0),
                _unite("inf2", UnitRole.MELEE_INFANTRY, index * 45.0),
                _unite("inf3", UnitRole.MELEE_INFANTRY, index * 55.0),
                # Les tireurs tiennent leur place et vident leur carquois.
                _unite("arc", UnitRole.RANGED_INFANTRY, 0.0, ammo=1.0 - index * 0.04),
            )
        )
    rapport = summarise(_etats(*etats))

    tireur = next(item for item in rapport.units if item.unit_id == "arc")
    assert not tireur.inert, "elle a tire : ce n'est pas de l'inertie"
    assert tireur.left_behind, "elle n'a jamais suivi l'armee"
    assert "RESTEE EN ARRIERE" in tireur.explain()
    assert [item.unit_id for item in rapport.left_behind] == ["arc"]


def test_une_armee_qui_avance_groupee_ne_declenche_rien() -> None:
    etats = [
        (
            _unite("a", UnitRole.MELEE_INFANTRY, index * 50.0),
            _unite("b", UnitRole.MELEE_INFANTRY, index * 45.0),
            _unite("c", UnitRole.MELEE_INFANTRY, index * 55.0),
        )
        for index in range(20)
    ]
    rapport = summarise(_etats(*etats))
    assert rapport.left_behind == []
    assert "Toutes les unites ont manoeuvre" in rapport.render()


def test_une_armee_immobile_ne_declenche_pas_de_faux_retardataire() -> None:
    """Sans trajet median, la comparaison n'a pas de sens."""
    etats = [
        (
            _unite("a", UnitRole.MELEE_INFANTRY, 0.0, melee=True),
            _unite("b", UnitRole.MELEE_INFANTRY, 0.0, melee=True),
        )
        for _ in range(10)
    ]
    rapport = summarise(_etats(*etats))
    assert rapport.left_behind == []
