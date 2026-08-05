"""Ce que valent les batailles enregistrees, avant d'apprendre dessus.

Constituer un corpus demande des dizaines de parties, et c'est du temps qui ne
se rattrape pas. Une bataille trouee ne se voit pas a l'oeil nu dans un fichier
de deux mega-octets : ces tests verifient qu'elle se voit ici.
"""

from __future__ import annotations

import json
from pathlib import Path

from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.live import LiveStep
from totalwar_ai.bridge.recording import BattleRecorder
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.learning.corpus import Corpus, inspect


def _etat(sequence: int, *, phase: str = "Deployed") -> ProbeBattleState:
    return ProbeBattleState(
        allies=(ProbeUnitObservation(unit_id="a1", position=Vector3(0.0, 12.0, 0.0)),),
        enemies=(ProbeUnitObservation(unit_id="e1", position=Vector3(0.0, 12.0, 50.0)),),
        sequence=sequence,
        game_time_ms=sequence * 500,
        phase=phase,
    )


def _enregistre(directory: Path, *sequences: int, fin: bool = True) -> BattleRecorder:
    recorder = BattleRecorder(directory=directory)
    for sequence in sequences:
        recorder.observe(LiveStep(state=_etat(sequence)))
    if fin:
        recorder.observe(LiveStep(state=_etat(max(sequences) + 1, phase="Complete")))
    recorder.close()
    return recorder


def test_une_bataille_complete_est_exploitable(tmp_path: Path) -> None:
    recorder = _enregistre(tmp_path, 1, 2, 3)

    assert recorder.path is not None
    fiche = inspect(recorder.path)
    assert fiche.has_units and fiche.finished and fiche.gaps == 0
    assert fiche.completeness == 1.0
    assert fiche.usable
    assert "complete" in fiche.explain()


def test_un_trou_dans_le_flux_est_compte(tmp_path: Path) -> None:
    """Quatre etats publies par la sonde et jamais enregistres."""
    recorder = _enregistre(tmp_path, 1, 2, 7)

    assert recorder.path is not None
    fiche = inspect(recorder.path)
    assert fiche.gaps == 4, f"{fiche.gaps} trou(s) detecte(s)"
    assert "manquant" in fiche.explain()


def test_une_bataille_interrompue_est_ecartee(tmp_path: Path) -> None:
    """L'issue est inconnue : on ne peut pas rattacher une decision a un resultat."""
    recorder = _enregistre(tmp_path, 1, 2, 3, fin=False)

    assert recorder.path is not None
    fiche = inspect(recorder.path)
    assert not fiche.finished
    assert "issue inconnue" in fiche.explain()


def test_les_journaux_du_simulateur_ne_sont_pas_des_batailles(tmp_path: Path) -> None:
    """Le meme repertoire en recoit des centaines : les melanger fausserait tout.

    Constate en developpant : quatre cent quinze journaux de simulation pris
    pour des batailles reelles, faute d'en-tete de format.
    """
    (tmp_path / "simulee.jsonl").write_text(
        json.dumps({"type": "battle_started", "battle_id": "x", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    _enregistre(tmp_path, 1, 2)

    corpus = Corpus.load(tmp_path)
    assert len(corpus.battles) == 1, "un journal de simulation a ete pris pour une bataille"
    assert corpus.ignored == 1


def test_un_corpus_vide_dit_quoi_faire(tmp_path: Path) -> None:
    corpus = Corpus.load(tmp_path)

    assert not corpus.battles
    assert "--observe" in corpus.render()


def test_un_fichier_tronque_ne_condamne_pas_la_bataille(tmp_path: Path) -> None:
    """Un plantage du jeu en pleine ecriture laisse une derniere ligne coupee."""
    recorder = _enregistre(tmp_path, 1, 2)
    assert recorder.path is not None
    with recorder.path.open("a", encoding="utf-8") as handle:
        handle.write('{"turn": 9, "sequ')

    fiche = inspect(recorder.path)
    assert fiche.observations == 3, "les tours valides ont ete perdus"
    assert not fiche.error


# --- les trous, et ce qui n'en est pas ------------------------------------------


def _enregistrement(tmp_path: Path, sequences: list[int]) -> Path:
    from totalwar_ai.bridge.recording import RECORDING_FORMAT

    chemin = tmp_path / "bataille.jsonl"
    lignes = [json.dumps({"format": RECORDING_FORMAT, "battle_id": "b"})]
    lignes += [
        json.dumps(
            {
                "turn": index,
                "sequence": sequence,
                "game_time_ms": index * 500,
                "phase": "Deployed",
                "units": [{"id": "a", "x": 0.0, "y": 0.0, "z": float(index)}],
            }
        )
        for index, sequence in enumerate(sequences)
    ]
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return chemin


def test_la_sonde_avance_de_deux_en_deux_sans_rien_perdre(tmp_path: Path) -> None:
    """Elle emet deux messages par publication, et les deux partagent le compteur.

    Compter les numeros absents declarait la moitie du flux perdue : les trois
    premieres batailles reelles sont ressorties a « 782 etats manquants » sur
    784, alors que rien ne manquait.
    """
    fiche = inspect(_enregistrement(tmp_path, [1, 3, 5, 7, 9, 11, 13]))
    assert fiche.gaps == 0
    assert fiche.observations == 7


def test_un_vrai_trou_reste_visible(tmp_path: Path) -> None:
    """Trois publications sautees entre deux etats, au pas de deux."""
    fiche = inspect(_enregistrement(tmp_path, [1, 3, 5, 13, 15, 17]))
    assert fiche.gaps == 3


def test_un_flux_au_pas_de_un_se_lit_aussi(tmp_path: Path) -> None:
    fiche = inspect(_enregistrement(tmp_path, [1, 2, 3, 6, 7, 8]))
    assert fiche.gaps == 2


def test_un_seul_ecart_isole_ne_redefinit_pas_la_cadence(tmp_path: Path) -> None:
    """Une statistique sensible a un point unique n'est pas une mesure.

    Mesure sur une bataille reelle de 817 etats : 815 ecarts valaient 2 et un
    seul valait 1. Le minimum a pris ce cas isole pour la cadence normale, et
    declare manquant chacun des 815 autres.
    """
    sequences = [1 + 2 * index for index in range(40)]
    # Une seule publication ou la sonde n'a emis qu'un message.
    sequences += [sequences[-1] + 1]
    sequences += [sequences[-1] + 2 * index for index in range(1, 40)]

    assert inspect(_enregistrement(tmp_path, sequences)).gaps == 0


def test_un_trou_reste_visible_malgre_un_ecart_isole(tmp_path: Path) -> None:
    sequences = [1 + 2 * index for index in range(20)]
    sequences += [sequences[-1] + 1]  # l'anomalie isolee
    sequences += [sequences[-1] + 8]  # un vrai trou : trois publications sautees
    sequences += [sequences[-1] + 2 * index for index in range(1, 20)]

    assert inspect(_enregistrement(tmp_path, sequences)).gaps == 3
