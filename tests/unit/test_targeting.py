"""Apprendre qui l'IA du moteur attaque — et le verifier sur une politique connue.

Comme pour l'inference, l'instrument s'etalonne avant de servir : la doublure de
`simulation/native_ai.py` envoie sa cavalerie sur les tireurs, et c'est
exactement cela que la table d'affinites doit ressortir. Une table qui echoue
sur une politique dont on connait la reponse ne dirait rien de bon sur l'IA du
jeu — et cela se verifie **sans jouer une seule bataille**.
"""

from __future__ import annotations

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.learning.observation import Move, Observation, infer
from totalwar_ai.learning.targeting import (
    NEUTRAL_AFFINITY,
    Accuracy,
    evaluate,
    learn,
)
from totalwar_ai.simulation.environment import SimulationEnvironment, UnitSpec


def _choix(
    attaquant: UnitRole,
    cible: UnitRole,
    offert: tuple[UnitRole, ...],
    *,
    move: Move = Move.CLOSE,
    ambigu: bool = False,
) -> Observation:
    return Observation(
        game_time=0.0,
        unit_id="u",
        role=attaquant,
        move=move,
        target_id="c",
        target_role=cible,
        ambiguous=ambigu,
        available=offert,
    )


# --- ce que l'affinite mesure --------------------------------------------------


def test_frapper_ce_qui_est_seul_disponible_n_est_pas_une_preference() -> None:
    """Neuf lanciers frappes sur dix ne disent rien si l'ennemi n'a que cela."""
    duo = (UnitRole.SPEAR_INFANTRY, UnitRole.MELEE_INFANTRY)
    observations = [_choix(UnitRole.SHOCK_CAVALRY, UnitRole.SPEAR_INFANTRY, duo) for _ in range(20)]
    observations += [
        _choix(UnitRole.SHOCK_CAVALRY, UnitRole.MELEE_INFANTRY, duo) for _ in range(20)
    ]

    modele = learn(observations)
    # Chaque role etait offert la moitie du temps et pris la moitie du temps :
    # le gout est exactement celui du hasard.
    assert modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.SPEAR_INFANTRY) == 1.0
    assert modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.MELEE_INFANTRY) == 1.0


def test_la_composition_de_l_armee_ne_passe_pas_pour_un_gout() -> None:
    """Un role trois fois plus present est trois fois plus frappe sans etre prefere."""
    offert = (
        UnitRole.MELEE_INFANTRY,
        UnitRole.MELEE_INFANTRY,
        UnitRole.MELEE_INFANTRY,
        UnitRole.RANGED_INFANTRY,
    )
    observations = [
        _choix(UnitRole.SHOCK_CAVALRY, UnitRole.MELEE_INFANTRY, offert) for _ in range(30)
    ]
    observations += [
        _choix(UnitRole.SHOCK_CAVALRY, UnitRole.RANGED_INFANTRY, offert) for _ in range(10)
    ]

    modele = learn(observations)
    assert modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.MELEE_INFANTRY) == 1.0
    assert modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.RANGED_INFANTRY) == 1.0


def test_un_gout_reel_ressort_malgre_la_rarete_de_la_cible() -> None:
    """Un role rare toujours choisi est recherche, et l'affinite doit le dire."""
    offert = (
        UnitRole.MELEE_INFANTRY,
        UnitRole.MELEE_INFANTRY,
        UnitRole.MELEE_INFANTRY,
        UnitRole.ARTILLERY,
    )
    observations = [_choix(UnitRole.SHOCK_CAVALRY, UnitRole.ARTILLERY, offert) for _ in range(40)]

    modele = learn(observations)
    # Le hasard l'aurait pris une fois sur quatre ; elle l'est a chaque fois.
    assert modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.ARTILLERY) == 4.0
    assert modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.MELEE_INFANTRY) == 0.0


def test_un_couple_jamais_vu_reste_neutre() -> None:
    """Ne l'avoir jamais vu n'est pas l'avoir vu evite."""
    modele = learn([])
    assert modele.affinity(UnitRole.LORD, UnitRole.ARTILLERY) == NEUTRAL_AFFINITY


# --- ce que l'apprentissage refuse de retenir -----------------------------------


def test_une_cible_ambigue_n_entre_pas_dans_la_table() -> None:
    duo = (UnitRole.SPEAR_INFANTRY, UnitRole.MELEE_INFANTRY)
    modele = learn(
        [
            _choix(UnitRole.SHOCK_CAVALRY, UnitRole.SPEAR_INFANTRY, duo, ambigu=True)
            for _ in range(20)
        ]
    )
    assert modele.samples == 0
    assert modele.skipped == 20


def test_la_melee_n_est_pas_un_choix_de_cible() -> None:
    """Une unite au contact subit son adversaire, elle ne le choisit pas.

    C'est la conclusion de l'etalonnage : melees comprises, la doublure — dont
    la cavalerie va aux tireurs — ressortait avec un gout prononce pour les
    lanciers, qui sont simplement ce qui l'arretait en chemin.
    """
    duo = (UnitRole.SPEAR_INFANTRY, UnitRole.RANGED_INFANTRY)
    modele = learn(
        [
            _choix(UnitRole.SHOCK_CAVALRY, UnitRole.SPEAR_INFANTRY, duo, move=Move.ENGAGE)
            for _ in range(30)
        ]
    )
    assert modele.samples == 0


def test_un_seul_role_en_face_n_apprend_rien() -> None:
    seul = (UnitRole.MELEE_INFANTRY, UnitRole.MELEE_INFANTRY)
    modele = learn([_choix(UnitRole.LORD, UnitRole.MELEE_INFANTRY, seul) for _ in range(20)])
    assert modele.samples == 0


# --- etalonnage : retrouver la politique de la doublure -------------------------


def _spec(unit_id: str, role: UnitRole, side: Side, x: float, z: float) -> UnitSpec:
    return UnitSpec(id=unit_id, side=side, role=role, position=Vector3(x, 0.0, z))


def _observations_de_la_doublure() -> list[Observation]:
    """Quatre batailles ou la cavalerie a le choix entre melee et tireurs."""
    resultat: list[Observation] = []
    for index, (x, z) in enumerate([(0.0, -20.0), (-60.0, 0.0), (60.0, -40.0), (0.0, 40.0)]):
        env = SimulationEnvironment(
            "etalon",
            [
                _spec("cav", UnitRole.SHOCK_CAVALRY, Side.ALLY, 0.0, -100.0),
                _spec("melee", UnitRole.MELEE_INFANTRY, Side.ENEMY, x, z),
                _spec("archers", UnitRole.RANGED_INFANTRY, Side.ENEMY, 150.0, 60.0),
            ],
            seed=11 + index,
            ally_autopilot=True,
        )
        etats: list[BattleState] = [env.state()]
        for _ in range(60):
            if env.finished:
                break
            etats.append(env.step().state)
        resultat += infer(etats).observations
    return resultat


def test_la_table_retrouve_la_chasse_aux_tireurs() -> None:
    """La doublure envoie la cavalerie sur les tireurs. La table doit le dire."""
    modele = learn(_observations_de_la_doublure())

    tireurs = modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.RANGED_INFANTRY)
    melee = modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.MELEE_INFANTRY)
    assert modele.samples > 0, "aucun choix retenu : la mesure n'a pas eu lieu"
    assert tireurs > 1.0, f"les tireurs ne ressortent pas recherches : {tireurs:.2f}"
    assert melee < 1.0, f"la melee ne ressort pas evitee : {melee:.2f}"
    assert modele.preference(UnitRole.SHOCK_CAVALRY)[0].target is UnitRole.RANGED_INFANTRY


def test_le_modele_appris_predit_mieux_que_le_hasard() -> None:
    """Un modele qui ne bat pas le hasard n'a rien appris du tout."""
    mesure = evaluate(_observations_de_la_doublure())
    assert mesure.samples > 0
    assert mesure.beats_random, mesure.explain()


def test_la_prediction_choisit_parmi_ce_qui_est_offert() -> None:
    """Predire un role absent du champ de bataille n'aurait aucun sens."""
    modele = learn(_observations_de_la_doublure())
    choix = modele.predict(UnitRole.SHOCK_CAVALRY, [UnitRole.LORD, UnitRole.MELEE_INFANTRY])
    assert choix in {UnitRole.LORD, UnitRole.MELEE_INFANTRY}
    assert modele.predict(UnitRole.SHOCK_CAVALRY, []) is None


def test_un_corpus_vide_ne_produit_pas_de_verdict() -> None:
    assert evaluate([]) == Accuracy(samples=0, learned=0.0, random=0.0, handwritten=0.0)


# --- la coupe ne fait pas fuiter la reponse ------------------------------------


def test_la_coupe_est_chronologique() -> None:
    """Melanger ferait apprendre et mesurer sur les memes instants de bataille."""
    duo = (UnitRole.MELEE_INFANTRY, UnitRole.ARTILLERY)
    # Premiere moitie : on prend l'artillerie. Seconde : on prend la melee.
    observations = [_choix(UnitRole.LORD, UnitRole.ARTILLERY, duo) for _ in range(50)]
    observations += [_choix(UnitRole.LORD, UnitRole.MELEE_INFANTRY, duo) for _ in range(50)]

    # Apprise sur le debut, la table predit l'artillerie et se trompe partout.
    mesure = evaluate(observations, holdout=0.3)
    assert mesure.samples == 30
    assert mesure.learned == 0.0, "la seconde moitie a fuite dans l'apprentissage"
