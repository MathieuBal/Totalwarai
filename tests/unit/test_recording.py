"""Enregistrement des batailles pilotees dans le jeu.

Ces batailles sont la seule mesure capable de departager un jour le simulateur
et le jeu, dont les verdicts divergent (voir `docs/decisions/0005`). Leur
fidelite compte donc autant que celle du pont : une donnee inventee ici
ressortirait plus tard comme un fait.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from totalwar_ai.bridge.command_models import ProbeAttack, ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.live import LiveStep
from totalwar_ai.bridge.orders import Translation
from totalwar_ai.bridge.recording import LIVE_SCENARIO, BattleRecorder
from totalwar_ai.domain.actions import ActionType
from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.domain.geometry import Vector3


def _unite(unit_id: str, *, alive: bool = True, hp: float = 1.0) -> ProbeUnitObservation:
    return ProbeUnitObservation(
        unit_id=unit_id,
        position=Vector3(0.0, 0.0, 0.0),
        alive=alive,
        hitpoints=hp,
    )


def _etat(
    allies: int, enemies: int, *, ms: int = 0, phase: str = "Deployed", morts: int = 0
) -> ProbeBattleState:
    unites = [_unite(f"a{i}") for i in range(allies - morts)]
    unites += [_unite(f"m{i}", alive=False) for i in range(morts)]
    return ProbeBattleState(
        allies=tuple(unites),
        enemies=tuple(_unite(f"e{i}") for i in range(enemies)),
        game_time_ms=ms,
        phase=phase,
    )


def _tour(state: ProbeBattleState, **kwargs: object) -> LiveStep:
    return LiveStep(state=state, **kwargs)  # type: ignore[arg-type]


# --- ce qui est retenu --------------------------------------------------------


def test_chaque_tour_est_ecrit_au_fil_de_l_eau(tmp_path: Path) -> None:
    """Une interruption ne doit pas emporter la bataille avec elle."""
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(5, 5, ms=1000)))
    recorder.observe(_tour(_etat(5, 4, ms=2000)))

    assert recorder.path is not None
    lignes = recorder.path.read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 2  # ecrit avant meme d'avoir ferme
    assert json.loads(lignes[0])["allies"] == 5
    assert json.loads(lignes[1])["enemies"] == 4
    recorder.close()


def test_les_ordres_et_les_refus_sont_comptes(tmp_path: Path) -> None:
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(
        _tour(
            _etat(5, 5),
            sent=3,
            blocked=("refus a", "refus b"),
            translation=Translation(
                moves=(("a0", Vector3(1.0, 0.0, 2.0)),),
                attacks=(ProbeAttack(unit_id="a1", target_id="e0"),),
                halts=("a2",),
                untranslated=((ActionType.REORIENT_FRONT, "sans equivalent"),),
            ),
        )
    )
    recorder.close()

    resume = recorder.summary()
    assert resume.actions_sent == 3
    assert resume.actions_blocked == 2
    assert recorder.actions_lost == 1

    entree = recorder.entries[0]
    assert entree["orders"]["moves"][0]["unit_id"] == "a0"
    assert entree["orders"]["attacks"][0]["target_id"] == "e0"
    assert entree["orders"]["halts"] == ["a2"]
    assert entree["untranslated"][0]["action"] == "REORIENT_FRONT"


def test_un_tour_sans_etat_n_est_pas_un_tour() -> None:
    """Le jeu n'a rien publie : il n'y a rien a enregistrer."""
    recorder = BattleRecorder()
    recorder.observe(LiveStep())
    assert recorder.turns == 0
    assert not recorder.entries


def test_la_duree_est_celle_du_jeu_pas_celle_de_l_horloge(tmp_path: Path) -> None:
    """Le temps de jeu peut etre accelere ou en pause : seul le sien compte."""
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(5, 5, ms=30_000)))
    recorder.observe(_tour(_etat(5, 5, ms=210_000)))
    recorder.close()

    assert recorder.summary().duration == pytest.approx(180.0)


# --- ce que l'enregistrement refuse de pretendre ------------------------------


def test_l_issue_reste_inconnue_tant_que_le_jeu_ne_l_annonce_pas() -> None:
    """Deviner une victoire depuis les forces restantes polluerait la memoire.

    Une session interrompue par l'operateur, ou une armee en bonne posture au
    moment ou l'on cesse d'observer, ne sont pas des victoires.
    """
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 1, phase="Deployed")))  # position dominante

    assert recorder.outcome is BattleOutcomeKind.UNKNOWN
    assert recorder.summary().outcome is BattleOutcomeKind.UNKNOWN


def test_l_issue_est_lue_quand_la_bataille_se_termine() -> None:
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 5)))
    recorder.observe(_tour(ProbeBattleState(allies=(_unite("a0"),), phase="Complete")))

    assert recorder.outcome is BattleOutcomeKind.VICTORY


def test_une_armee_aneantie_est_une_defaite() -> None:
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 5)))
    recorder.observe(_tour(ProbeBattleState(enemies=(_unite("e0"),), phase="Complete")))

    assert recorder.outcome is BattleOutcomeKind.DEFEAT


def test_l_episode_ne_fabrique_ni_transitions_ni_recompense() -> None:
    """Le jeu ne dit pas qui a tire sur qui : rien ne permet de les calculer.

    Les inventer ferait entrer du bruit dans la memoire d'apprentissage sous
    les traits d'une mesure.
    """
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 5)))

    episode = recorder.episode()
    assert episode.transitions == []
    assert episode.summary.total_reward == 0.0


# --- comparabilite avec les batailles simulees --------------------------------


def test_les_batailles_reelles_sont_isolables_des_simulees() -> None:
    """Melanger les deux sources rendrait toute comparaison inutilisable."""
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 5)))

    resume = recorder.summary()
    assert resume.scenario == LIVE_SCENARIO
    assert resume.agent_mode == "deterministic-live"
    assert resume.metrics["source"] == "game"


def test_les_pertes_sont_rapportees_a_l_effectif_de_depart() -> None:
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(4, 4)))
    recorder.observe(_tour(_etat(4, 4, morts=3)))

    resume = recorder.summary()
    assert resume.ally_remaining == pytest.approx(0.25)
    assert resume.enemy_remaining == pytest.approx(1.0)


def test_une_bataille_enregistree_se_relit_en_memoire(tmp_path: Path) -> None:
    """Le but de tout ceci : comparer plus tard le jeu et le simulateur."""
    from totalwar_ai.memory.repository import MemoryRepository

    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(5, 5, ms=1000), sent=2))
    recorder.close()

    repository = MemoryRepository(tmp_path / "memoire.sqlite3")
    repository.save_episode(recorder.episode())

    relues = repository.list_battles(scenario=LIVE_SCENARIO)
    assert [item.battle_id for item in relues] == [recorder.battle_id]
    assert relues[0].actions_sent == 2


def test_un_tour_sans_nouvel_etat_ne_gonfle_pas_le_compte(tmp_path: Path) -> None:
    """La boucle interroge plus souvent que le jeu ne publie.

    Compter ces tours vides fausserait la duree comme la cadence d'ordres, et
    rendrait deux enregistrements incomparables selon la vitesse de la machine.
    """
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(5, 5, ms=1000)))
    recorder.observe(LiveStep())  # rien de nouveau cote jeu
    recorder.observe(LiveStep())
    recorder.observe(_tour(_etat(5, 5, ms=2000)))
    recorder.close()

    assert recorder.turns == 2
    assert recorder.summary().metrics["turns"] == 2


def test_une_unite_detruite_mais_encore_listee_ne_sauve_pas_l_adversaire() -> None:
    """L'issue se juge sur les unites vivantes, pas sur la longueur des listes.

    A la premiere bataille menee a son terme, le camp vaincu avait disparu des
    listes du jeu et l'issue etait juste. Rien ne garantit qu'il en aille
    toujours ainsi : une unite detruite mais encore publiee ferait basculer un
    aneantissement en match nul.
    """
    from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
    from totalwar_ai.bridge.live import LiveStep
    from totalwar_ai.bridge.recording import BattleRecorder
    from totalwar_ai.domain.battle_state import BattleOutcomeKind
    from totalwar_ai.domain.geometry import Vector3

    def unite(unit_id: str, *, alive: bool) -> ProbeUnitObservation:
        return ProbeUnitObservation(unit_id=unit_id, position=Vector3(0.0, 0.0, 0.0), alive=alive)

    recorder = BattleRecorder(directory=None)
    recorder.observe(
        LiveStep(
            state=ProbeBattleState(
                allies=(unite("a1", alive=True),),
                enemies=(unite("e1", alive=True),),
                phase="Deployed",
            )
        )
    )
    recorder.observe(
        LiveStep(
            state=ProbeBattleState(
                allies=(unite("a1", alive=True),),
                enemies=(unite("e1", alive=False),),
                phase="Complete",
            )
        )
    )

    assert recorder.outcome is BattleOutcomeKind.VICTORY
