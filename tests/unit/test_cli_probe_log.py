"""Lecture du journal du jeu par la commande `probe --log`.

Ce journal est le seul canal de diagnostic quand quelque chose cloche cote jeu.
Deux defauts constates en usage reel le rendaient inexploitable :

* il se remplissait d'etats identiques — 297 Ko en dix-sept minutes de bataille,
  ou `--log 80` n'affichait plus que la meme unite immobile ;
* rien n'y signalait qu'un pack embarquait une version anterieure du script,
  cause la plus frequente d'un essai en bataille perdu.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from totalwar_ai.bridge.file_bridge import FileBridge
from totalwar_ai.cli import _print_game_log

ETAT = (
    '<10.0s> [totalwar_ai] STATE {"protocol_version":"0.1.0","type":"unit_state",'
    '"sequence":%d,"unit":{"id":"1001"}}'
)


def _journal(workdir: Path, lignes: list[str]) -> FileBridge:
    """Ecrit un faux journal de script la ou le CLI ira le chercher."""
    (workdir / "script_log_020826_0209.txt").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return FileBridge.open(workdir)


def test_les_etats_repetitifs_sont_masques(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = _journal(
        tmp_path,
        [
            "<1.0s> [totalwar_ai] --- diagnostic des entrees-sorties ---",
            "<1.0s> [totalwar_ai] --- recensement des accesseurs d'unite ---",
            "<2.0s> [totalwar_ai] BATTLE phase Deployed : 11 allies, 11 ennemis",
            "<3.0s> [totalwar_ai] ERREUR dans publish_state : quelque chose",
            *[ETAT % index for index in range(1, 200)],
        ],
    )

    assert _print_game_log(bridge, 80) == 0
    sortie = capsys.readouterr().out

    # L'information utile survit au bruit.
    assert "ERREUR dans publish_state" in sortie
    assert "diagnostic des entrees-sorties" in sortie
    # Les etats sont comptes, pas deroules.
    assert "200 ligne(s) d'etat, masquees ici" in sortie
    assert sortie.count('type\\":\\"unit_state') <= 1


def test_un_pack_perime_est_signale(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Le journal d'un ancien script doit dire qu'il est ancien."""
    bridge = _journal(
        tmp_path,
        [
            "<1.0s> [totalwar_ai] --- diagnostic des entrees-sorties ---",
            "<1.0s> [totalwar_ai] --- recensement des accesseurs d'unite ---",
            ETAT % 1,
        ],
    )

    assert _print_game_log(bridge, 40) == 0
    sortie = capsys.readouterr().out
    assert "aucune ligne `BATTLE`" in sortie
    assert "reconstruire" in sortie.lower()


def test_le_manque_le_plus_ancien_est_signale_en_premier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inutile de reclamer `BATTLE` a un script qui n'a meme pas le diagnostic."""
    bridge = _journal(tmp_path, ["<1.0s> [totalwar_ai] === fichier charge (sonde v0.1.0) ==="])

    assert _print_game_log(bridge, 40) == 0
    sortie = capsys.readouterr().out
    assert "diagnostic des entrees-sorties" in sortie
    assert "BATTLE" not in sortie


def test_un_journal_a_jour_ne_reclame_rien(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = _journal(
        tmp_path,
        [
            "<1.0s> [totalwar_ai] --- diagnostic des entrees-sorties ---",
            "<1.0s> [totalwar_ai] --- recensement des accesseurs d'unite ---",
            "<2.0s> [totalwar_ai] BATTLE phase Deployed : 11 allies, 11 ennemis",
        ],
    )

    assert _print_game_log(bridge, 40) == 0
    assert "econstruire" not in capsys.readouterr().out


def test_un_journal_sans_sonde_le_dit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bridge = _journal(tmp_path, ["<1.0s> Loading mod file [script\\_lib\\mod\\qa_console.lua]"])

    assert _print_game_log(bridge, 40) == 1
    assert "Aucune ligne [totalwar_ai]" in capsys.readouterr().out
