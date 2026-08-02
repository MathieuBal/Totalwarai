"""Lecture du journal du jeu par la commande `probe --log`.

Ce journal est le seul canal de diagnostic quand quelque chose cloche cote jeu.
Deux defauts constates en usage reel le rendaient inexploitable :

* il se remplissait d'etats identiques — 297 Ko en dix-sept minutes de bataille,
  ou `--log 80` n'affichait plus que la meme unite immobile ;
* rien n'y signalait qu'un pack embarquait une version anterieure du script,
  cause la plus frequente d'un essai en bataille perdu.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from totalwar_ai.bridge.file_bridge import FileBridge
from totalwar_ai.bridge.paths import EXPECTED_PROBE_REVISION
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


def test_un_script_sans_revision_est_signale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Les versions anterieures du script n'annoncent aucune revision."""
    bridge = _journal(
        tmp_path,
        [
            "<1.0s> [totalwar_ai] === fichier charge (sonde v0.1.0) ===",
            "<1.0s> [totalwar_ai] --- diagnostic des entrees-sorties ---",
            ETAT % 1,
        ],
    )

    assert _print_game_log(bridge, 40) == 0
    sortie = capsys.readouterr().out
    assert "n'annonce aucune revision" in sortie
    assert f"revision {EXPECTED_PROBE_REVISION}" in sortie


def test_un_journal_sans_sonde_le_dit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bridge = _journal(tmp_path, ["<1.0s> Loading mod file [script\\_lib\\mod\\qa_console.lua]"])

    assert _print_game_log(bridge, 40) == 1
    assert "Aucune ligne [totalwar_ai]" in capsys.readouterr().out


# --- accord entre le script Lua et le Python ---------------------------------


def test_la_revision_attendue_est_celle_du_script_lua() -> None:
    """Les deux numeros doivent bouger ensemble, ou le diagnostic ment.

    Python affirme quelle revision de script il attend ; le Lua annonce la
    sienne dans le journal. Si l'un est modifie sans l'autre, `probe --log`
    reclamerait une reconstruction sans raison, ou en tairait une necessaire.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "lua_mod"
        / "script"
        / "battle"
        / "mod"
        / "totalwar_ai_probe.lua"
    ).read_text(encoding="utf-8")

    declaree = re.search(r"TOTALWAR_AI_PROBE_REVISION = (\d+)", source)
    assert declaree is not None, "le script Lua n'annonce plus sa revision"
    assert int(declaree.group(1)) == EXPECTED_PROBE_REVISION, (
        "revision du script Lua et EXPECTED_PROBE_REVISION divergent : "
        "incrementer les deux ensemble"
    )


def test_une_revision_ancienne_reclame_une_reconstruction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = _journal(
        tmp_path,
        [
            "<1.0s> [totalwar_ai] === fichier charge (sonde v0.1.0, revision 0) ===",
            "<1.0s> [totalwar_ai] --- diagnostic des entrees-sorties ---",
            "<1.0s> [totalwar_ai] --- recensement des accesseurs d'unite ---",
            "<2.0s> [totalwar_ai] BATTLE phase Deployed : 11 allies, 11 ennemis",
        ],
    )

    assert _print_game_log(bridge, 40) == 0
    sortie = capsys.readouterr().out
    assert "Pack en revision 0" in sortie
    assert "reconstruire le pack" in sortie


def test_une_revision_a_jour_est_confirmee(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dire que tout va bien evite de chercher un probleme qui n'existe pas."""
    bridge = _journal(
        tmp_path,
        [
            "<1.0s> [totalwar_ai] === fichier charge (sonde v0.1.0, revision "
            f"{EXPECTED_PROBE_REVISION}) ===",
            "<1.0s> [totalwar_ai] --- diagnostic des entrees-sorties ---",
            "<1.0s> [totalwar_ai] --- recensement des accesseurs d'unite ---",
            "<2.0s> [totalwar_ai] BATTLE phase Deployed : 11 allies, 11 ennemis",
        ],
    )

    assert _print_game_log(bridge, 40) == 0
    assert f"Pack a jour (revision {EXPECTED_PROBE_REVISION})" in capsys.readouterr().out


def test_un_python_en_retard_le_dit_aussi(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le pack peut aussi etre en avance : c'est alors Python qu'il faut mettre a jour."""
    bridge = _journal(
        tmp_path,
        [
            "<1.0s> [totalwar_ai] === fichier charge (sonde v0.1.0, revision "
            f"{EXPECTED_PROBE_REVISION + 5}) ===",
            "<1.0s> [totalwar_ai] --- diagnostic des entrees-sorties ---",
            "<1.0s> [totalwar_ai] --- recensement des accesseurs d'unite ---",
            "<2.0s> [totalwar_ai] BATTLE phase Deployed : 11 allies, 11 ennemis",
        ],
    )

    assert _print_game_log(bridge, 40) == 0
    assert "mettre a jour le paquet Python" in capsys.readouterr().out
