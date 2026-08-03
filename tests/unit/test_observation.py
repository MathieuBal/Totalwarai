"""Deduire ce qu'une unite faisait, en la regardant seulement bouger.

Le jeu ne dit pas quel ordre porte une unite. L'inference le reconstitue de deux
etats successifs — et ces tests verifient qu'elle **retrouve une politique dont
on connait la reponse** avant qu'on ne lui demande de decrire l'IA du moteur.
"""

from __future__ import annotations

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState
from totalwar_ai.learning.observation import Move, infer
from totalwar_ai.simulation.environment import SimulationEnvironment, UnitSpec


def _unite(
    unit_id: str,
    role: UnitRole,
    side: Side,
    x: float = 0.0,
    z: float = 0.0,
    *,
    engaged: bool = False,
    ammo: float = 0.0,
    portee: float = 0.0,
) -> UnitState:
    return UnitState(
        id=unit_id,
        side=side,
        role=role,
        position=Vector3(x, 0.0, z),
        is_engaged=engaged,
        ammo_ratio=ammo,
        tags=("missile",) if portee else (),
        metadata={"missile_range": portee} if portee else {},
    )


def _etat(*unites: UnitState, temps: float = 0.0) -> BattleState:
    return BattleState(battle_id="t", game_time=temps, units=unites)


# --- ce que l'on conclut d'un mouvement ----------------------------------------


def test_une_unite_immobile_tient_sa_position() -> None:
    avant = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=200.0),
    )
    apres = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=1.0),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=200.0),
        temps=1.0,
    )

    resultat = infer([avant, apres])
    assert [item.move for item in resultat.observations] == [Move.HOLD]


def test_une_unite_qui_avance_designe_celui_vers_qui_elle_va() -> None:
    avant = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("proche", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=100.0),
        _unite("loin", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=300.0),
    )
    apres = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=20.0),
        _unite("proche", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=100.0),
        _unite("loin", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=300.0),
        temps=1.0,
    )

    observation = infer([avant, apres]).observations[0]
    assert observation.move is Move.CLOSE
    assert observation.target_id == "proche"


def test_une_unite_qui_s_eloigne_decroche() -> None:
    avant = _etat(
        _unite("a", UnitRole.RANGED_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=60.0),
    )
    apres = _etat(
        _unite("a", UnitRole.RANGED_INFANTRY, Side.ALLY, z=-40.0),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=60.0),
        temps=1.0,
    )

    observation = infer([avant, apres]).observations[0]
    assert observation.move is Move.WITHDRAW
    assert observation.target_id is None


def test_le_contact_prime_sur_le_deplacement() -> None:
    avant = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=30.0),
    )
    apres = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=25.0, engaged=True),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=30.0, engaged=True),
        temps=1.0,
    )

    observation = infer([avant, apres]).observations[0]
    assert observation.move is Move.ENGAGE
    assert observation.target_id == "e"


def test_la_baisse_des_munitions_trahit_le_tir() -> None:
    """Aucun accesseur ne dit qu'une unite tire, ni sur qui. Le stock, si."""
    avant = _etat(
        _unite("arc", UnitRole.RANGED_INFANTRY, Side.ALLY, ammo=1.0, portee=120.0),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=80.0),
    )
    apres = _etat(
        _unite("arc", UnitRole.RANGED_INFANTRY, Side.ALLY, ammo=0.8, portee=120.0),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=80.0),
        temps=1.0,
    )

    observation = infer([avant, apres]).observations[0]
    assert observation.move is Move.FIRE
    assert observation.target_id == "e"


# --- ce que l'inference refuse de trancher -------------------------------------


def test_deux_cibles_egalement_approchees_restent_indecidables() -> None:
    """Trancher a pile ou face ferait entrer une supposition dans les donnees."""
    avant = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("g", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=-2.0, z=100.0),
        _unite("d", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=2.0, z=100.0),
    )
    apres = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=30.0),
        _unite("g", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=-2.0, z=100.0),
        _unite("d", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=2.0, z=100.0),
        temps=1.0,
    )

    resultat = infer([avant, apres])
    observation = resultat.observations[0]
    assert observation.ambiguous
    assert observation.target_id is None, "une cible supposee a ete retenue"
    assert resultat.ambiguity == 1.0


def test_l_ambiguite_est_chiffree() -> None:
    """Une inference sure d'elle sur des donnees ambigues serait pire qu'inutile."""
    net = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=100.0),
    )
    ensuite = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=30.0),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=100.0),
        temps=1.0,
    )

    assert infer([net, ensuite]).ambiguity == 0.0


# --- etalonnage : retrouver une politique connue --------------------------------


def _joue(specs: list[UnitSpec], ticks: int = 40) -> list[BattleState]:
    """Une bataille menee par la doublure de l'IA du moteur, etat par etat."""
    env = SimulationEnvironment("etalon", specs, seed=11, ally_autopilot=True)
    etats = [env.state()]
    for _ in range(ticks):
        if env.finished:
            break
        etats.append(env.step().state)
    return etats


def _spec(unit_id: str, role: UnitRole, side: Side, x: float, z: float) -> UnitSpec:
    return UnitSpec(id=unit_id, side=side, role=role, position=Vector3(x, 0.0, z))


def test_l_inference_retrouve_la_preference_de_la_cavalerie() -> None:
    """La doublure envoie la cavalerie sur les tireurs : cela doit se voir.

    C'est l'etalonnage de l'instrument. Si l'inference echoue sur une politique
    dont on connait la reponse, elle ne dira rien de bon sur l'IA du jeu.
    """
    etats = _joue(
        [
            _spec("cav", UnitRole.SHOCK_CAVALRY, Side.ALLY, 0.0, -100.0),
            _spec("melee_proche", UnitRole.MELEE_INFANTRY, Side.ENEMY, 0.0, -20.0),
            _spec("archers", UnitRole.RANGED_INFANTRY, Side.ENEMY, 150.0, 60.0),
        ]
    )

    vises = infer(etats).targets_of(UnitRole.SHOCK_CAVALRY)
    assert vises, "aucune cible inferee pour la cavalerie"
    assert vises.count(UnitRole.RANGED_INFANTRY) > vises.count(UnitRole.MELEE_INFANTRY), (
        f"cibles inferees : {[role.value for role in vises]}"
    )


def test_l_inference_retrouve_le_plus_proche_pour_la_melee() -> None:
    """La doublure envoie l'infanterie sur l'ennemi le plus proche."""
    etats = _joue(
        [
            _spec("inf", UnitRole.MELEE_INFANTRY, Side.ALLY, 0.0, -100.0),
            _spec("proche", UnitRole.MELEE_INFANTRY, Side.ENEMY, 0.0, -20.0),
            _spec("loin", UnitRole.MELEE_INFANTRY, Side.ENEMY, 250.0, 100.0),
        ]
    )

    observations = [
        item
        for item in infer(etats).observations
        if item.unit_id == "inf" and item.target_id is not None
    ]
    assert observations, "aucune cible inferee pour l'infanterie"
    assert observations[0].target_id == "proche"


def test_l_inference_reste_lisible_sur_une_bataille_entiere() -> None:
    """Une inference qui ne saurait rien conclure ne servirait a rien."""
    etats = _joue(
        [
            _spec("inf1", UnitRole.MELEE_INFANTRY, Side.ALLY, -40.0, -100.0),
            _spec("inf2", UnitRole.MELEE_INFANTRY, Side.ALLY, 40.0, -100.0),
            _spec("cav", UnitRole.SHOCK_CAVALRY, Side.ALLY, 0.0, -140.0),
            _spec("e1", UnitRole.MELEE_INFANTRY, Side.ENEMY, -40.0, 60.0),
            _spec("e2", UnitRole.RANGED_INFANTRY, Side.ENEMY, 60.0, 90.0),
        ],
        ticks=80,
    )

    resultat = infer(etats)
    comptes = resultat.counts()
    assert sum(comptes.values()) > 50, f"trop peu de decisions inferees : {comptes}"
    assert comptes.get(Move.CLOSE, 0) > 0, "aucune approche detectee"
    assert resultat.ambiguity < 0.5, f"trop d'ambiguite : {resultat.ambiguity:.0%}"


# --- la continuite leve une part des doutes ------------------------------------


def test_un_ordre_qui_persiste_leve_le_doute() -> None:
    """Une unite qui frappait un ennemi et l'a toujours au contact le frappe encore.

    Ce n'est pas une supposition : c'est la propriete la plus stable d'un ordre.
    La continuite ne designe jamais qu'un adversaire deja candidat et proche.
    """
    # Premier tour : la cible est nette.
    debut = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("g", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=40.0),
        _unite("d", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=200.0),
    )
    contact = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=35.0, engaged=True),
        _unite("g", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=40.0, engaged=True),
        _unite("d", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=200.0),
        temps=1.0,
    )
    # Un second ennemi arrive a la meme distance : sans memoire, on hesiterait.
    melee = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=35.0, engaged=True),
        _unite("g", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=40.0, engaged=True),
        _unite("d", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=30.0, engaged=True),
        temps=2.0,
    )

    sans = infer([debut, contact, melee], use_continuity=False).observations[-1]
    avec = infer([debut, contact, melee]).observations[-1]

    assert sans.ambiguous and sans.target_id is None
    assert not avec.ambiguous
    assert avec.target_id == "g", "la continuite a designe un autre adversaire"


def test_la_continuite_ne_ressuscite_pas_une_cible_disparue() -> None:
    """Une cible morte ou hors de vue ne peut plus expliquer un mouvement."""
    debut = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("g", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=40.0),
    )
    contact = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=35.0, engaged=True),
        _unite("g", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=40.0, engaged=True),
        temps=1.0,
    )
    # `g` a disparu ; deux nouveaux adversaires sont a egale distance.
    apres = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=35.0, engaged=True),
        _unite("x", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=-3.0, z=40.0, engaged=True),
        _unite("y", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=3.0, z=40.0, engaged=True),
        temps=2.0,
    )

    observation = infer([debut, contact, apres]).observations[-1]
    assert observation.ambiguous
    assert observation.target_id is None


# --- qui s'est rapproche de qui ------------------------------------------------


def test_une_unite_immobile_ne_charge_pas_ce_qui_fond_sur_elle() -> None:
    """« Il a fondu sur moi » n'est pas « j'ai marche vers lui ».

    Defaut trouve sur les trois premieres batailles reelles : tous les roles
    ressortaient avec une affinite proche de 5 pour les unites volantes, y
    compris les volantes entre elles. Une volante qui traverse le champ se
    rapproche de toute l'armee a la fois, et l'inference lisait cela comme une
    armee entiere decidant de la charger.
    """
    avant = _etat(
        _unite("statique", UnitRole.RANGED_INFANTRY, Side.ALLY),
        _unite("volante", UnitRole.FLYING_UNIT, Side.ENEMY, z=200.0),
    )
    # Nous n'avons pas bouge ; l'ennemi a parcouru cent metres vers nous.
    apres = _etat(
        _unite("statique", UnitRole.RANGED_INFANTRY, Side.ALLY, z=1.0),
        _unite("volante", UnitRole.FLYING_UNIT, Side.ENEMY, z=100.0),
        temps=1.0,
    )

    observation = infer([avant, apres]).observations[0]
    assert observation.move is Move.HOLD, f"conclu {observation.move.value}"
    assert observation.target_id is None


def test_notre_propre_deplacement_designe_encore_la_cible() -> None:
    """La correction ne doit pas aveugler l'inference sur une vraie approche."""
    avant = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=100.0),
        _unite("loin", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=300.0),
    )
    # C'est nous qui avons marche, l'ennemi n'a pas bouge.
    apres = _etat(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY, z=30.0),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=100.0),
        _unite("loin", UnitRole.MELEE_INFANTRY, Side.ENEMY, x=300.0),
        temps=1.0,
    )

    observation = infer([avant, apres]).observations[0]
    assert observation.move is Move.CLOSE
    assert observation.target_id == "e"


def test_une_unite_qui_recule_devant_une_charge_decroche() -> None:
    """Elle s'eloigne, meme si la distance finale a diminue."""
    avant = _etat(
        _unite("a", UnitRole.RANGED_INFANTRY, Side.ALLY),
        _unite("charge", UnitRole.SHOCK_CAVALRY, Side.ENEMY, z=200.0),
    )
    apres = _etat(
        _unite("a", UnitRole.RANGED_INFANTRY, Side.ALLY, z=-40.0),
        _unite("charge", UnitRole.SHOCK_CAVALRY, Side.ENEMY, z=60.0),
        temps=1.0,
    )

    observation = infer([avant, apres]).observations[0]
    assert observation.move is Move.WITHDRAW
