"""Supervision de l'IA de bataille du jeu.

L'IA du moteur mene la bataille ; ces regles reprennent la seule unite dont elle
fait mauvais usage. Deux exigences opposees s'y rencontrent : intervenir quand
c'est necessaire, et ne pas la contrarier le reste du temps — une supervision
qui reprend tout ne supervise plus, elle remplace.
"""

from __future__ import annotations

import pytest

from totalwar_ai.bridge.supervision import (
    LORD_CRITICAL_HEALTH,
    Intervention,
    Supervisor,
)
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState


def _unite(
    unit_id: str,
    role: UnitRole,
    *,
    engaged: bool = False,
    health: float = 1.0,
    x: float = 0.0,
    z: float = 0.0,
    side: Side = Side.ALLY,
) -> UnitState:
    return UnitState(
        id=unit_id,
        side=side,
        role=role,
        position=Vector3(x, 0.0, z),
        is_engaged=engaged,
        health_ratio=health,
    )


def _etat(*unites: UnitState, temps: float = 100.0) -> BattleState:
    return BattleState(battle_id="t", game_time=temps, units=unites)


def _confiees(*unit_ids: str) -> set[str]:
    return set(unit_ids)


# --- ce qui declenche une reprise ---------------------------------------------


def test_une_artillerie_au_contact_est_reprise() -> None:
    """Une piece prise au corps a corps est perdue : elle ne se defend pas."""
    etat = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
    )
    reprises = Supervisor().review(etat, _confiees("art"))

    assert [item.unit_id for item in reprises] == ["art"]
    assert reprises[0].rule == "artillerie_au_contact"
    assert reprises[0].destination is not None


def test_un_tireur_au_contact_est_repris() -> None:
    etat = _etat(
        _unite("arc", UnitRole.RANGED_INFANTRY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
    )
    reprises = Supervisor().review(etat, _confiees("arc"))

    assert reprises[0].rule == "tireur_au_contact"


def test_un_seigneur_mourant_est_retire_du_combat() -> None:
    """Sa mort coute plus que les hommes qu'il represente."""
    etat = _etat(
        _unite("lord", UnitRole.LORD, health=LORD_CRITICAL_HEALTH - 0.05),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
    )
    reprises = Supervisor().review(etat, _confiees("lord"))

    assert reprises[0].rule == "seigneur_en_danger"


def test_le_repli_s_ecarte_de_la_menace() -> None:
    """Se replier vers le centre de l'armee peut mener droit sur l'ennemi."""
    etat = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True, z=0.0),
        _unite("e1", UnitRole.MELEE_INFANTRY, z=50.0, side=Side.ENEMY),
    )
    reprise = Supervisor().review(etat, _confiees("art"))[0]

    assert reprise.destination is not None
    assert reprise.destination.z < 0.0, "l'unite se replie vers la menace"


# --- ce qui n'en declenche pas -------------------------------------------------


def test_une_unite_de_melee_au_contact_est_laissee_a_l_ia() -> None:
    """C'est son role, et l'IA du jeu le mene mieux que nous."""
    etat = _etat(
        _unite("inf", UnitRole.MELEE_INFANTRY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
    )
    assert Supervisor().review(etat, _confiees("inf")) == []


def test_un_seigneur_en_bonne_sante_reste_a_l_ia() -> None:
    etat = _etat(
        _unite("lord", UnitRole.LORD, engaged=True, health=0.9),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
    )
    assert Supervisor().review(etat, _confiees("lord")) == []


def test_une_unite_non_confiee_n_est_pas_reprise() -> None:
    """Elle est deja a nous : la reprendre n'aurait aucun sens."""
    etat = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
    )
    assert Supervisor().review(etat, _confiees()) == []


def test_une_unite_deja_reprise_ne_l_est_pas_deux_fois() -> None:
    """Elle est sous notre controle, plus sous celui de l'IA du jeu."""
    superviseur = Supervisor()
    etat = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
    )
    confiees = _confiees("art")

    assert len(superviseur.review(etat, confiees)) == 1
    assert superviseur.review(etat, confiees) == []


# --- rendre les unites ---------------------------------------------------------


def test_une_unite_degagee_est_rendue_apres_le_delai() -> None:
    superviseur = Supervisor(cooldown_seconds=20.0)
    engagee = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
        temps=100.0,
    )
    superviseur.review(engagee, _confiees("art"))

    degagee = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=False),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
        temps=130.0,
    )
    assert superviseur.ready_to_return(degagee) == ["art"]


def test_une_unite_encore_au_contact_n_est_pas_rendue() -> None:
    """La rendre la remettrait dans l'etat exact qui a motive la reprise."""
    superviseur = Supervisor(cooldown_seconds=20.0)
    engagee = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
        temps=100.0,
    )
    superviseur.review(engagee, _confiees("art"))

    toujours = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
        temps=200.0,
    )
    assert superviseur.ready_to_return(toujours) == []


def test_le_delai_est_respecte_meme_si_la_situation_est_reglee() -> None:
    """Rendre trop tot ferait renvoyer l'unite au contact, et osciller."""
    superviseur = Supervisor(cooldown_seconds=20.0)
    engagee = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
        temps=100.0,
    )
    superviseur.review(engagee, _confiees("art"))

    juste_apres = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=False),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
        temps=105.0,
    )
    assert superviseur.ready_to_return(juste_apres) == []


def test_une_unite_disparue_cesse_d_etre_suivie() -> None:
    superviseur = Supervisor(cooldown_seconds=20.0)
    engagee = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
        temps=100.0,
    )
    superviseur.review(engagee, _confiees("art"))

    sans_elle = _etat(_unite("e1", UnitRole.MELEE_INFANTRY, side=Side.ENEMY), temps=200.0)
    assert superviseur.ready_to_return(sans_elle) == ["art"]


def test_oublier_une_unite_permet_de_la_reprendre_plus_tard() -> None:
    """Rendue puis mal employee a nouveau, elle doit pouvoir etre reprise."""
    superviseur = Supervisor()
    etat = _etat(
        _unite("art", UnitRole.ARTILLERY, engaged=True),
        _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY),
    )
    confiees = _confiees("art")
    superviseur.review(etat, confiees)
    superviseur.forget(["art"])

    assert len(superviseur.review(etat, confiees)) == 1


# --- lisibilite ----------------------------------------------------------------


def test_chaque_reprise_porte_son_motif() -> None:
    """Une reprise sans motif serait indistinguable d'un bogue."""
    intervention = Intervention(
        unit_id="art", rule="artillerie_au_contact", reason="prise au corps"
    )
    explication = intervention.explain()

    assert "art" in explication
    assert "artillerie_au_contact" in explication
    assert "prise au corps" in explication


def test_une_bataille_sans_incident_ne_produit_aucune_reprise() -> None:
    """Une supervision qui reprend tout ne supervise plus : elle remplace."""
    etat = _etat(
        _unite("inf1", UnitRole.MELEE_INFANTRY, engaged=True),
        _unite("inf2", UnitRole.SPEAR_INFANTRY, engaged=True),
        _unite("arc", UnitRole.RANGED_INFANTRY, z=-80.0),
        _unite("art", UnitRole.ARTILLERY, z=-120.0),
        _unite("lord", UnitRole.LORD, health=0.8),
        _unite("e1", UnitRole.MELEE_INFANTRY, z=20.0, side=Side.ENEMY),
    )
    assert Supervisor().review(etat, _confiees("inf1", "inf2", "arc", "art", "lord")) == []


def test_le_seuil_du_seigneur_est_une_limite_franche() -> None:
    """Juste au-dessus, on laisse faire ; juste en dessous, on retire."""
    menace = _unite("e1", UnitRole.MELEE_INFANTRY, x=10.0, side=Side.ENEMY)
    au_dessus = _etat(_unite("lord", UnitRole.LORD, health=LORD_CRITICAL_HEALTH + 0.01), menace)
    en_dessous = _etat(_unite("lord", UnitRole.LORD, health=LORD_CRITICAL_HEALTH - 0.01), menace)

    assert Supervisor().review(au_dessus, _confiees("lord")) == []
    assert len(Supervisor().review(en_dessous, _confiees("lord"))) == 1
    assert pytest.approx(0.35) == LORD_CRITICAL_HEALTH
