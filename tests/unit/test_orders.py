"""Traduction des intentions de l'agent en ordres pour le jeu.

Deux exigences gouvernent ce module : etaler un groupe en ligne plutot que de
l'empiler sur un point, et ne jamais approximer une action sans equivalent.
"""

from __future__ import annotations

import math

import pytest

from totalwar_ai.bridge.orders import OrderTranslator
from totalwar_ai.domain.actions import ActionType, AgentAction, Formation
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitState


def _etat(*unit_ids: str) -> BattleState:
    return BattleState(
        battle_id="t",
        units=tuple(
            UnitState(id=unit_id, side=Side.ALLY, position=Vector3(0.0, 0.0, 0.0))
            for unit_id in unit_ids
        ),
    )


def _move(*unit_ids: str, heading: float | None = 0.0, spacing: float = 30.0) -> AgentAction:
    parameters: dict[str, object] = {
        "destination": Vector3(100.0, 0.0, 200.0),
        "formation": Formation.LINE,
        "spacing": spacing,
    }
    if heading is not None:
        parameters["heading"] = heading
    return AgentAction(type=ActionType.MOVE_GROUP, actor_ids=unit_ids, parameters=parameters)


# --- mise en ligne -----------------------------------------------------------


def test_un_groupe_est_etale_et_non_empile() -> None:
    """Envoyer tout le monde au meme point produirait un tas."""
    resultat = OrderTranslator().translate((_move("a", "b", "c"),), _etat("a", "b", "c"))

    points = [point for _, point in resultat.moves]
    assert len(points) == 3
    assert len({(round(p.x, 3), round(p.z, 3)) for p in points}) == 3


def test_la_ligne_est_perpendiculaire_au_cap() -> None:
    """Une ligne face a l'ennemi, pas une colonne dans son axe.

    Cap 0 signifie « vers +z » : les unites doivent donc s'aligner sur x.
    """
    resultat = OrderTranslator().translate(
        (_move("a", "b", "c", heading=0.0),), _etat("a", "b", "c")
    )

    x = sorted(round(point.x, 3) for _, point in resultat.moves)
    z = {round(point.z, 3) for _, point in resultat.moves}
    assert x == [70.0, 100.0, 130.0]  # espacement 30, centre sur 100
    assert z == {200.0}


def test_un_cap_lateral_fait_pivoter_la_ligne() -> None:
    """Cap pi/2 signifie « vers +x » : la ligne s'aligne alors sur z."""
    action = _move("a", "b", heading=math.pi / 2)
    resultat = OrderTranslator().translate((action,), _etat("a", "b"))

    x = {round(point.x, 3) for _, point in resultat.moves}
    z = sorted(round(point.z, 3) for _, point in resultat.moves)
    assert x == {100.0}
    assert z == [185.0, 215.0]


def test_le_groupe_reste_centre_sur_sa_destination() -> None:
    """Le barycentre de la ligne est le point demande par l'agent."""
    resultat = OrderTranslator().translate((_move("a", "b", "c", "d"),), _etat("a", "b", "c", "d"))

    points = [point for _, point in resultat.moves]
    assert sum(p.x for p in points) / len(points) == pytest.approx(100.0)
    assert sum(p.z for p in points) / len(points) == pytest.approx(200.0)


def test_une_unite_seule_va_exactement_au_point() -> None:
    resultat = OrderTranslator().translate((_move("a"),), _etat("a"))
    assert resultat.moves[0][1] == Vector3(100.0, 0.0, 200.0)


def test_une_unite_absente_de_l_etat_est_ignoree() -> None:
    """Ordonner a une unite morte gaspillerait un ordre et fausserait la ligne."""
    resultat = OrderTranslator().translate((_move("a", "disparue", "b"),), _etat("a", "b"))
    assert [unit_id for unit_id, _ in resultat.moves] == ["a", "b"]


# --- actions sans equivalent -------------------------------------------------


def test_une_action_sans_equivalent_est_nommee_et_non_approximee() -> None:
    """Le principe, teste sur la seule action qui reste hors de portee.

    `REORIENT_FRONT` demande un ordre d'orientation que le jeu n'expose pas.
    On pourrait la rendre par un deplacement sur place — l'unite pivoterait
    peut-etre, par effet de bord — mais le compte rendu affirmerait alors une
    reorientation dont rien ne garantit qu'elle a eu lieu.

    Ce test porte sur la regle, pas sur cette action : toute action que le pont
    ne sait pas rendre doit ressortir avec son nom et son motif, jamais sous la
    forme d'un ordre qui lui ressemble.
    """
    reorientation = AgentAction(
        type=ActionType.REORIENT_FRONT,
        actor_ids=("a",),
        parameters={"heading": 1.57},
    )
    resultat = OrderTranslator().translate((reorientation,), _etat("a"))

    assert not resultat.moves
    assert not resultat.attacks
    assert resultat.untranslated == (
        (ActionType.REORIENT_FRONT, "necessite un ordre d'orientation"),
    )


def test_tenir_la_position_ne_produit_aucun_ordre_et_n_est_pas_un_manque() -> None:
    """Ne rien envoyer est exactement ce que cette action demande."""
    tenir = AgentAction(type=ActionType.HOLD_POSITION, actor_ids=("a",))
    resultat = OrderTranslator().translate((tenir,), _etat("a"))

    assert not resultat.moves
    assert not resultat.untranslated


def test_une_destination_manquante_est_signalee() -> None:
    """Un parametre absent doit se voir, pas produire un ordre vers l'origine."""
    bancale = AgentAction(type=ActionType.RETREAT, actor_ids=("a",), parameters={})
    resultat = OrderTranslator().translate((bancale,), _etat("a"))

    assert not resultat.moves
    assert resultat.untranslated[0][0] is ActionType.RETREAT
    assert "destination" in resultat.untranslated[0][1]


def test_le_repli_et_la_reserve_sont_traduits() -> None:
    """Trois actions differentes, trois noms de parametre, un meme ordre."""
    repli = AgentAction(
        type=ActionType.RETREAT,
        actor_ids=("a",),
        parameters={"destination": Vector3(1.0, 0.0, 2.0)},
    )
    reserve = AgentAction(
        type=ActionType.FORM_RESERVE,
        actor_ids=("b",),
        parameters={"rally_point": Vector3(3.0, 0.0, 4.0)},
    )
    resultat = OrderTranslator().translate((repli, reserve), _etat("a", "b"))

    assert dict(resultat.moves) == {"a": Vector3(1.0, 0.0, 2.0), "b": Vector3(3.0, 0.0, 4.0)}


def test_la_premiere_action_gagne_pour_une_unite_donnee() -> None:
    """Deux ordres contradictoires s'annuleraient en jeu.

    L'agent classe ses actions par priorite : la premiere emise pour une unite
    est celle qu'il juge la plus importante.
    """
    prioritaire = AgentAction(
        type=ActionType.RETREAT,
        actor_ids=("a",),
        parameters={"destination": Vector3(1.0, 0.0, 1.0)},
    )
    ensuite = AgentAction(
        type=ActionType.MOVE_GROUP,
        actor_ids=("a",),
        parameters={"destination": Vector3(9.0, 0.0, 9.0), "formation": Formation.LINE},
    )
    resultat = OrderTranslator().translate((prioritaire, ensuite), _etat("a"))

    assert resultat.moves == (("a", Vector3(1.0, 0.0, 1.0)),)


# --- engagement --------------------------------------------------------------


def _cible(state_ids: tuple[str, ...], target: str) -> BattleState:
    return BattleState(
        battle_id="t",
        units=(
            *(
                UnitState(id=unit_id, side=Side.ALLY, position=Vector3(0.0, 0.0, 0.0))
                for unit_id in state_ids
            ),
            UnitState(id=target, side=Side.ENEMY, position=Vector3(0.0, 0.0, 100.0)),
        ),
    )


def test_une_attaque_devient_un_ordre_d_engagement() -> None:
    """Le manque constate en bataille : l'agent decidait sans pouvoir agir."""
    action = AgentAction(
        type=ActionType.ATTACK_TARGET,
        actor_ids=("a", "b"),
        parameters={"target_id": "e1"},
    )
    resultat = OrderTranslator().translate((action,), _cible(("a", "b"), "e1"))

    assert not resultat.untranslated
    assert [(item.unit_id, item.target_id) for item in resultat.attacks] == [
        ("a", "e1"),
        ("b", "e1"),
    ]


def test_le_tir_concentre_ne_force_pas_la_melee() -> None:
    """Imposer le corps a corps a un tireur lui ferait perdre son avantage."""
    tir = AgentAction(type=ActionType.FOCUS_FIRE, actor_ids=("a",), parameters={"target_id": "e1"})
    charge = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("b",), parameters={"target_id": "e1"}
    )
    resultat = OrderTranslator().translate((tir, charge), _cible(("a", "b"), "e1"))

    par_unite = {item.unit_id: item.melee for item in resultat.attacks}
    assert par_unite == {"a": False, "b": True}


def test_une_cible_disparue_est_signalee() -> None:
    """Attaquer une unite morte gaspillerait l'ordre, sans que rien ne le dise."""
    action = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a",), parameters={"target_id": "fantome"}
    )
    resultat = OrderTranslator().translate((action,), _cible(("a",), "e1"))

    assert not resultat.attacks
    assert resultat.untranslated[0][0] is ActionType.ATTACK_TARGET
    assert "fantome" in resultat.untranslated[0][1]


def test_une_unite_ne_recoit_pas_a_la_fois_un_deplacement_et_une_attaque() -> None:
    """Les deux ordres se contrediraient : le premier emis gagne."""
    attaque = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a",), parameters={"target_id": "e1"}
    )
    repli = AgentAction(
        type=ActionType.RETREAT,
        actor_ids=("a",),
        parameters={"destination": Vector3(0.0, 0.0, -50.0)},
    )
    resultat = OrderTranslator().translate((attaque, repli), _cible(("a",), "e1"))

    assert len(resultat.attacks) == 1
    assert not resultat.moves


def test_deplacements_et_attaques_coexistent_dans_un_meme_tour() -> None:
    """Une manoeuvre reelle melange les deux : les separer perdrait la moitie."""
    attaque = AgentAction(
        type=ActionType.ATTACK_TARGET, actor_ids=("a",), parameters={"target_id": "e1"}
    )
    repli = AgentAction(
        type=ActionType.RETREAT,
        actor_ids=("b",),
        parameters={"destination": Vector3(0.0, 0.0, -50.0)},
    )
    resultat = OrderTranslator().translate((attaque, repli), _cible(("a", "b"), "e1"))

    assert resultat.order_count == 2
    assert [item.unit_id for item in resultat.attacks] == ["a"]
    assert [unit_id for unit_id, _ in resultat.moves] == ["b"]


# --- manoeuvres ---------------------------------------------------------------


def _affrontement() -> BattleState:
    """Notre ligne au sud, une cible au nord : un affrontement lisible."""
    return BattleState(
        battle_id="t",
        units=(
            UnitState(id="cav", side=Side.ALLY, position=Vector3(0.0, 0.0, -100.0)),
            UnitState(id="ligne", side=Side.ALLY, position=Vector3(0.0, 0.0, -100.0)),
            UnitState(id="archers", side=Side.ALLY, position=Vector3(0.0, 0.0, -120.0)),
            UnitState(id="e1", side=Side.ENEMY, position=Vector3(0.0, 0.0, 100.0)),
        ),
    )


def test_le_contournement_se_place_sur_le_flanc_de_la_cible() -> None:
    """Prendre a revers, c'est arriver de cote — pas foncer de face."""
    flanc = AgentAction(
        type=ActionType.FLANK,
        actor_ids=("cav",),
        parameters={"target_id": "e1", "side": "right"},
    )
    resultat = OrderTranslator().translate((flanc,), _affrontement())

    assert not resultat.untranslated
    point = dict(resultat.moves)["cav"]
    # L'axe d'attaque va vers +z : le flanc est donc ecarte sur x.
    assert abs(point.x) > 50.0
    # Et l'unite depasse la cible, au lieu de s'arreter a sa hauteur.
    assert point.z > 100.0


def test_les_deux_ailes_contournent_de_cotes_opposes() -> None:
    """Deux ailes qui se croiseraient ne prendraient personne a revers."""
    droite = AgentAction(
        type=ActionType.FLANK, actor_ids=("cav",), parameters={"target_id": "e1", "side": "right"}
    )
    gauche = AgentAction(
        type=ActionType.FLANK, actor_ids=("ligne",), parameters={"target_id": "e1", "side": "left"}
    )
    resultat = OrderTranslator().translate((droite, gauche), _affrontement())

    points = dict(resultat.moves)
    assert points["cav"].x * points["ligne"].x < 0.0, "les deux ailes vont du meme cote"


def test_un_contournement_sans_cible_est_signale() -> None:
    flanc = AgentAction(
        type=ActionType.FLANK, actor_ids=("cav",), parameters={"target_id": "disparu"}
    )
    resultat = OrderTranslator().translate((flanc,), _affrontement())

    assert not resultat.moves
    assert resultat.untranslated[0] == (ActionType.FLANK, "cible introuvable")


def test_l_escorte_se_place_entre_le_protege_et_la_menace() -> None:
    """Se porter sur le protege ne le protege pas : l'escorte arriverait apres."""
    protection = AgentAction(
        type=ActionType.PROTECT,
        actor_ids=("cav",),
        parameters={"protected_ids": ["archers"]},
    )
    resultat = OrderTranslator().translate((protection,), _affrontement())

    point = dict(resultat.moves)["cav"]
    archers, menace = -120.0, 100.0
    assert archers < point.z < menace, "l'escorte n'est pas sur le trajet"
    # Plus pres de la menace que du protege : on l'accroche avant le contact.
    assert point.z - archers > menace - point.z


def test_une_protection_sans_ennemi_visible_est_signalee() -> None:
    """Sans menace, il n'y a rien a intercepter."""
    sans_ennemi = BattleState(
        battle_id="t",
        units=(UnitState(id="cav", side=Side.ALLY, position=Vector3(0.0, 0.0, 0.0)),),
    )
    protection = AgentAction(
        type=ActionType.PROTECT, actor_ids=("cav",), parameters={"protected_ids": ["cav"]}
    )
    resultat = OrderTranslator().translate((protection,), sans_ennemi)

    assert not resultat.moves
    assert resultat.untranslated[0][0] is ActionType.PROTECT


# --- tenir la position ---------------------------------------------------------


def _avec_mouvement(*unit_ids: str, idle: bool) -> BattleState:
    return BattleState(
        battle_id="t",
        units=tuple(
            UnitState(
                id=unit_id,
                side=Side.ALLY,
                position=Vector3(0.0, 0.0, 0.0),
                metadata={"idle": idle},
            )
            for unit_id in unit_ids
        ),
    )


def test_tenir_la_position_arrete_une_unite_en_mouvement() -> None:
    """Constate en bataille : cinq « tenir la position » ne produisaient rien.

    N'envoyer aucun ordre laisse l'unite poursuivre ce qu'elle faisait. L'agent
    croyait tenir sa ligne pendant que l'armee continuait d'avancer.
    """
    tenir = AgentAction(type=ActionType.HOLD_POSITION, actor_ids=("a",))
    resultat = OrderTranslator().translate((tenir,), _avec_mouvement("a", idle=False))

    assert resultat.halts == ("a",)
    assert not resultat.untranslated


def test_une_unite_deja_immobile_n_est_pas_arretee() -> None:
    """Arreter ce qui ne bouge pas gaspillerait un ordre et prendrait l'unite."""
    tenir = AgentAction(type=ActionType.HOLD_POSITION, actor_ids=("a",))
    resultat = OrderTranslator().translate((tenir,), _avec_mouvement("a", idle=True))

    assert not resultat.halts
    assert resultat.is_empty
    assert not resultat.untranslated


def test_un_arret_n_empeche_pas_un_ordre_prioritaire() -> None:
    """L'agent classe par priorite : le premier ordre emis pour une unite gagne."""
    repli = AgentAction(
        type=ActionType.RETREAT,
        actor_ids=("a",),
        parameters={"destination": Vector3(0.0, 0.0, -50.0)},
    )
    tenir = AgentAction(type=ActionType.HOLD_POSITION, actor_ids=("a",))
    resultat = OrderTranslator().translate((repli, tenir), _avec_mouvement("a", idle=False))

    assert not resultat.halts
    assert len(resultat.moves) == 1
