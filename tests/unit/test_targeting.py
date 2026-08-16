"""Apprendre qui l'IA du moteur attaque — et le verifier sur une politique connue.

Comme pour l'inference, l'instrument s'etalonne avant de servir : la doublure de
`simulation/native_ai.py` envoie sa cavalerie sur les tireurs, et c'est
exactement cela que la table d'affinites doit ressortir. Une table qui echoue
sur une politique dont on connait la reponse ne dirait rien de bon sur l'IA du
jeu — et cela se verifie **sans jouer une seule bataille**.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

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

#: Compteur de decisions distinctes forgees par `_choix`.
#:
#: **Chaque appel doit produire une decision differente.** Les observations
#: portaient toutes `unit_id="u"` et `target_id="c"` : pour
#: `targeting.episodes`, qui replie les releves consecutifs d'une meme paire,
#: vingt appels ne faisaient plus qu'une seule decision tenue — ce qui est le
#: comportement correct sur un corpus a 2 Hz, mais pas ce que ces tests veulent
#: dire. Des identifiants distincts rendent l'intention explicite.
_compteur = itertools.count()


def _choix(
    attaquant: UnitRole,
    cible: UnitRole,
    offert: tuple[UnitRole, ...],
    *,
    move: Move = Move.CLOSE,
    ambigu: bool = False,
) -> Observation:
    index = next(_compteur)
    return Observation(
        game_time=float(index),
        unit_id=f"u{index}",
        role=attaquant,
        move=move,
        target_id=f"c{index}",
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
    """Un modele qui ne bat pas le hasard n'a rien appris du tout.

    Deux batailles tirees de la meme politique : apprendre sur l'une doit
    permettre de predire l'autre.
    """
    mesure = evaluate([_observations_de_la_doublure(), _observations_de_la_doublure()])
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


def test_une_bataille_ne_fuite_pas_dans_son_propre_controle() -> None:
    """**Le defaut que l'audit a trouve, et que la docstring niait.**

    L'ancienne version decoupait une liste plate d'observations a 70 % et
    affirmait que cela empechait « la meme bataille de fuiter des deux cotes ».
    La coupe tombait au milieu d'une bataille, et les decisions d'une meme
    bataille partagent unites, positions et composition adverse.

    Deux batailles de politiques **opposees** le montrent : chacune predite par
    l'autre, le modele doit se tromper partout. S'il obtient un bon score, c'est
    qu'il a vu la reponse.
    """
    duo = (UnitRole.MELEE_INFANTRY, UnitRole.ARTILLERY)
    prend_artillerie = [_choix(UnitRole.LORD, UnitRole.ARTILLERY, duo) for _ in range(50)]
    prend_melee = [_choix(UnitRole.LORD, UnitRole.MELEE_INFANTRY, duo) for _ in range(50)]

    mesure = evaluate([prend_artillerie, prend_melee])

    assert mesure.samples == 100, "les deux batailles doivent servir de controle a tour de role"
    assert mesure.learned == 0.0, "une bataille a fuite dans son propre apprentissage"
    assert mesure.folds == 2


def test_une_seule_bataille_ne_produit_aucun_verdict() -> None:
    """Il n'y a rien contre quoi valider, et inventer une coupe interne rendrait
    exactement le chiffre flatteur que cette fonction existe pour eviter."""
    duo = (UnitRole.MELEE_INFANTRY, UnitRole.ARTILLERY)
    seule = [_choix(UnitRole.LORD, UnitRole.ARTILLERY, duo) for _ in range(50)]

    assert evaluate([seule]).samples == 0


def test_l_ecart_entre_passes_est_publie() -> None:
    """**Un chiffre unique sur trois batailles ne mesure rien.**

    Une politique constante et une politique opposee : la moyenne masque que
    l'une des passes est parfaite et l'autre nulle.
    """
    duo = (UnitRole.MELEE_INFANTRY, UnitRole.ARTILLERY)
    # Effectifs asymetriques a dessein : a egalite d'affinite, `predict` rend le
    # premier role disponible, et la passe ne mesurerait plus une preference.
    lots = [
        [_choix(UnitRole.LORD, UnitRole.ARTILLERY, duo) for _ in range(60)],
        [_choix(UnitRole.LORD, UnitRole.ARTILLERY, duo) for _ in range(60)],
        [_choix(UnitRole.LORD, UnitRole.MELEE_INFANTRY, duo) for _ in range(20)],
    ]
    mesure = evaluate(lots)

    assert mesure.folds == 3
    assert mesure.worst == 0.0
    assert mesure.best == 1.0
    assert "ecart entre passes" in mesure.explain()


# --- de ce qu'on apprend a ce que l'agent en fait -------------------------------


def _modele_appris() -> object:
    """Un modele ou la cavalerie recherche nettement les tireurs."""
    offert = (UnitRole.RANGED_INFANTRY, UnitRole.MELEE_INFANTRY, UnitRole.SPEAR_INFANTRY)
    observations = [
        _choix(UnitRole.SHOCK_CAVALRY, UnitRole.RANGED_INFANTRY, offert) for _ in range(60)
    ]
    observations += [
        _choix(UnitRole.SHOCK_CAVALRY, UnitRole.MELEE_INFANTRY, offert) for _ in range(6)
    ]
    return learn(observations)


def test_un_modele_survit_a_l_aller_retour(tmp_path: Path) -> None:
    from totalwar_ai.learning.targeting import TargetingModel

    modele = _modele_appris()
    assert isinstance(modele, TargetingModel)
    chemin = tmp_path / "targeting.json"
    modele.save(chemin)

    relu = TargetingModel.load(chemin)
    assert relu.samples == modele.samples
    # L'attendu est arrondi a l'ecriture : quatre decimales suffisent largement
    # a un rapport, et un fichier lisible vaut mieux qu'une precision inutile.
    assert relu.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.RANGED_INFANTRY) == pytest.approx(
        modele.affinity(UnitRole.SHOCK_CAVALRY, UnitRole.RANGED_INFANTRY)
    )
    assert relu.usable(UnitRole.SHOCK_CAVALRY)


def test_un_fichier_absent_ou_illisible_donne_un_modele_vide(tmp_path: Path) -> None:
    """L'agent doit pouvoir jouer sans avoir rien appris."""
    from totalwar_ai.learning.targeting import TargetingModel

    assert TargetingModel.load(tmp_path / "jamais_ecrit.json").affinities == {}
    abime = tmp_path / "abime.json"
    abime.write_text("{ceci n'est pas du JSON", encoding="utf-8")
    assert TargetingModel.load(abime).affinities == {}


def test_un_modele_d_un_autre_format_n_est_pas_charge_a_moitie(tmp_path: Path) -> None:
    from totalwar_ai.learning.targeting import TargetingModel

    chemin = tmp_path / "autre.json"
    chemin.write_text('{"format": "autre.chose", "affinities": []}', encoding="utf-8")
    assert TargetingModel.load(chemin).affinities == {}


def test_un_role_trop_peu_observe_n_est_pas_suivi() -> None:
    """Un modele a moitie appris a l'autorite d'une mesure et l'assise d'une anecdote."""
    from totalwar_ai.learning.targeting import TargetingModel

    duo = (UnitRole.RANGED_INFANTRY, UnitRole.MELEE_INFANTRY)
    maigre = learn([_choix(UnitRole.LORD, UnitRole.RANGED_INFANTRY, duo) for _ in range(3)])
    assert isinstance(maigre, TargetingModel)
    assert not maigre.usable(UnitRole.LORD)

    fourni = _modele_appris()
    assert isinstance(fourni, TargetingModel)
    assert fourni.usable(UnitRole.SHOCK_CAVALRY)


def test_le_planificateur_suit_le_modele_quand_il_est_solide() -> None:
    """C'est le point ou l'agent cesse de jouer sur nos convictions."""
    from totalwar_ai.agent.planner import TARGET_PRIORITY, Planner
    from totalwar_ai.learning.targeting import TargetingModel

    modele = _modele_appris()
    assert isinstance(modele, TargetingModel)
    ecrit = Planner()
    appris = Planner(targeting=modele)

    # La table ecrite a la main place les tireurs a 0.85 quel que soit l'attaquant.
    assert (
        ecrit.target_value(UnitRole.SHOCK_CAVALRY, UnitRole.RANGED_INFANTRY)
        == (TARGET_PRIORITY[UnitRole.RANGED_INFANTRY])
    )
    # Le modele les place au-dessus de la melee, et c'est lui qui parle.
    tireurs = appris.target_value(UnitRole.SHOCK_CAVALRY, UnitRole.RANGED_INFANTRY)
    melee = appris.target_value(UnitRole.SHOCK_CAVALRY, UnitRole.MELEE_INFANTRY)
    assert tireurs > melee, f"tireurs {tireurs:.2f}, melee {melee:.2f}"


def test_un_role_non_appris_garde_la_table_ecrite_a_la_main() -> None:
    """Le remplacement se fait role par role, jamais en bloc."""
    from totalwar_ai.agent.planner import TARGET_PRIORITY, Planner
    from totalwar_ai.learning.targeting import TargetingModel

    modele = _modele_appris()
    assert isinstance(modele, TargetingModel)
    planner = Planner(targeting=modele)

    # Rien n'a ete appris sur l'artillerie : elle garde sa valeur ecrite.
    assert (
        planner.target_value(UnitRole.ARTILLERY, UnitRole.RANGED_INFANTRY)
        == (TARGET_PRIORITY[UnitRole.RANGED_INFANTRY])
    )


def test_un_modele_appris_survit_au_rechargement_de_doctrine() -> None:
    """Le perdre la rendrait l'apprentissage silencieusement caduc."""
    from totalwar_ai.agent.planner import Planner
    from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
    from totalwar_ai.learning.adaptation import DoctrineProfile
    from totalwar_ai.learning.targeting import TargetingModel

    modele = _modele_appris()
    assert isinstance(modele, TargetingModel)
    agent = DeterministicTacticalAgent(planner=Planner(targeting=modele))

    agent.apply_doctrine(DoctrineProfile(adjustments={"engagement_distance": 5.0}))

    assert agent.planner.targeting is modele
