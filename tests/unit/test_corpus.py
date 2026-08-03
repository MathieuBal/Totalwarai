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
