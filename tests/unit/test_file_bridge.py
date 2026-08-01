"""Pont par fichiers : ecriture atomique, lecture incrementale, tolerance.

Ces tests valident le **cote Python** du prototype. Ils ne prouvent rien sur le
comportement du jeu : voir `tests/integration/test_lua_protocol.py` pour le
contrat commun, et `docs/feasibility.md` pour ce qui reste a verifier en
bataille reelle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from totalwar_ai.bridge.command_models import (
    ProbeAbortCommand,
    ProbeAck,
    ProbeMessageType,
    ProbeMoveCommand,
    ProbeStatus,
    ProbeUnitState,
    decode_command,
)
from totalwar_ai.bridge.file_bridge import FileBridge, read_states_from, summarise
from totalwar_ai.bridge.paths import (
    BRIDGE_DIR_ENV_VAR,
    BridgeDirectoryNotFoundError,
    BridgePaths,
    resolve_bridge_dir,
)
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.serialization import SchemaError

PROTOCOL = "0.1.0"


@pytest.fixture
def bridge(tmp_path: Path) -> FileBridge:
    return FileBridge.open(tmp_path / "totalwar_ai")


def _state_line(sequence: int = 1, x: float = 10.0, z: float = 20.0) -> str:
    return json.dumps(
        {
            "protocol_version": PROTOCOL,
            "type": "unit_state",
            "sequence": sequence,
            "game_time_ms": sequence * 1000,
            "unit": {
                "id": "12345",
                "type": "emp_spearmen",
                "position": {"x": x, "y": 0.0, "z": z},
                "controllable": True,
            },
        }
    )


def _ack_line(sequence: int = 1, status: str = "accepted", error: str | None = None) -> str:
    return json.dumps(
        {
            "protocol_version": PROTOCOL,
            "type": "action_result",
            "sequence": sequence,
            "status": status,
            "error": error,
        }
    )


# --- chemins -----------------------------------------------------------------


def test_resolution_depuis_le_repertoire_du_jeu(tmp_path: Path) -> None:
    paths = BridgePaths.resolve(tmp_path, create=True)
    assert paths.directory == tmp_path / "totalwar_ai"
    assert paths.state.name == "totalwar_ai_state.jsonl"
    assert paths.command.name == "totalwar_ai_command.json"
    assert paths.ack.name == "totalwar_ai_ack.jsonl"


def test_resolution_depuis_le_repertoire_d_echange(tmp_path: Path) -> None:
    """On accepte les deux : ce que l'utilisateur connait et ce dont le code a besoin."""
    direct = tmp_path / "totalwar_ai"
    assert BridgePaths.resolve(direct).directory == direct


def test_resolution_par_variable_d_environnement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BRIDGE_DIR_ENV_VAR, str(tmp_path))
    assert resolve_bridge_dir() == tmp_path / "totalwar_ai"


def test_echec_de_resolution_explicite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans indice, on refuse de deviner."""
    monkeypatch.delenv(BRIDGE_DIR_ENV_VAR, raising=False)
    monkeypatch.setattr("totalwar_ai.bridge.paths.find_game_directory", lambda explicit=None: None)
    with pytest.raises(BridgeDirectoryNotFoundError, match=BRIDGE_DIR_ENV_VAR):
        resolve_bridge_dir()


# --- ecriture des commandes --------------------------------------------------


def test_la_commande_est_publiee_atomiquement(bridge: FileBridge) -> None:
    command = bridge.move_unit("12345", Vector3(30.0, 0.0, 20.0))
    payload = json.loads(bridge.paths.command.read_text(encoding="utf-8"))
    assert payload == {
        "protocol_version": PROTOCOL,
        "type": "move_unit",
        "sequence": command.sequence,
        "unit_id": "12345",
        "destination": {"x": 30.0, "y": 0.0, "z": 20.0},
        "release_after_ms": 5000,
    }


def test_aucun_fichier_temporaire_ne_subsiste(bridge: FileBridge) -> None:
    bridge.move_unit("12345", Vector3(30.0, 0.0, 20.0))
    restants = [item.name for item in bridge.paths.directory.iterdir() if item.suffix == ".tmp"]
    assert restants == []


def test_le_temporaire_est_dans_le_repertoire_cible(
    bridge: FileBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.replace` n'est atomique qu'au sein d'un meme systeme de fichiers."""
    vus: list[tuple[str, str]] = []
    vrai_replace = os.replace

    def espion(src: object, dst: object) -> None:
        vus.append((str(src), str(dst)))
        vrai_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", espion)
    bridge.move_unit("12345", Vector3(1.0, 0.0, 2.0))
    assert len(vus) == 1
    source, destination = vus[0]
    assert Path(source).parent == Path(destination).parent == bridge.paths.directory


def test_une_commande_en_remplace_une_autre(bridge: FileBridge) -> None:
    bridge.move_unit("12345", Vector3(30.0, 0.0, 20.0))
    bridge.move_unit("12345", Vector3(50.0, 0.0, 20.0))
    payload = json.loads(bridge.paths.command.read_text(encoding="utf-8"))
    assert payload["destination"]["x"] == 50.0
    assert payload["sequence"] == 2  # la sequence progresse toujours


def test_les_sequences_progressent(bridge: FileBridge) -> None:
    premiere = bridge.move_unit("a", Vector3())
    seconde = bridge.move_unit("a", Vector3())
    assert seconde.sequence == premiere.sequence + 1


def test_echec_d_ecriture_ne_laisse_pas_de_trace(
    bridge: FileBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    def echec(src: object, dst: object) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr(os, "replace", echec)
    with pytest.raises(OSError, match="disque plein"):
        bridge.move_unit("12345", Vector3())
    assert list(bridge.paths.directory.glob("*.tmp")) == []


# --- arret d'urgence ---------------------------------------------------------


def test_l_arret_ecrit_la_sentinelle_et_la_commande(bridge: FileBridge) -> None:
    bridge.abort("test")
    assert bridge.stop_requested
    assert bridge.paths.stop.read_text(encoding="utf-8").strip() == "test"
    payload = json.loads(bridge.paths.command.read_text(encoding="utf-8"))
    assert payload["type"] == "abort"


def test_la_sentinelle_peut_etre_retiree(bridge: FileBridge) -> None:
    bridge.abort()
    bridge.clear_stop()
    assert not bridge.stop_requested


# --- lecture des flux --------------------------------------------------------


def test_lecture_incrementale_des_etats(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    bridge.paths.state.write_text(_state_line(1) + "\n", encoding="utf-8")
    premiers = bridge.read_states()
    assert len(premiers) == 1
    assert premiers[0].unit_id == "12345"
    assert premiers[0].position == Vector3(10.0, 0.0, 20.0)

    # Rien de neuf : rien n'est relivre.
    assert bridge.read_states() == []

    with bridge.paths.state.open("a", encoding="utf-8") as handle:
        handle.write(_state_line(2, x=30.0) + "\n")
    suivants = bridge.read_states()
    assert len(suivants) == 1
    assert suivants[0].sequence == 2


def test_ligne_incomplete_non_consommee(bridge: FileBridge) -> None:
    """Le Lua ecrivait encore : la ligne doit etre reprise entiere plus tard."""
    bridge.paths.ensure()
    complete = _state_line(1)
    bridge.paths.state.write_text(complete + "\n" + complete[:40], encoding="utf-8")
    assert len(bridge.read_states()) == 1

    # Le Lua termine sa ligne.
    bridge.paths.state.write_text(complete + "\n" + _state_line(2) + "\n", encoding="utf-8")
    seconds = bridge.read_states()
    assert len(seconds) == 1
    assert seconds[0].sequence == 2


def test_ligne_illisible_ignoree_mais_signalee(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    bridge.paths.state.write_text(
        "{ ceci n'est pas du JSON\n" + _state_line(2) + "\n", encoding="utf-8"
    )
    etats = bridge.read_states()
    assert len(etats) == 1
    assert etats[0].sequence == 2
    assert len(bridge.malformed) == 1
    assert bridge.malformed[0].path == bridge.paths.state


def test_lignes_vides_ignorees(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    bridge.paths.state.write_text("\n\n" + _state_line(1) + "\n\n", encoding="utf-8")
    assert len(bridge.read_states()) == 1
    assert bridge.malformed == []


def test_flux_absent(bridge: FileBridge) -> None:
    assert bridge.read_states() == []
    assert bridge.read_acks() == []


def test_lecture_des_accuses(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    bridge.paths.ack.write_text(
        _ack_line(1) + "\n" + _ack_line(2, "rejected", "unite introuvable") + "\n",
        encoding="utf-8",
    )
    acks = bridge.read_acks()
    assert [ack.status for ack in acks] == [ProbeStatus.ACCEPTED, ProbeStatus.REJECTED]
    assert acks[0].accepted
    assert not acks[1].accepted
    assert acks[1].error == "unite introuvable"


def test_les_deux_flux_avancent_independamment(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    bridge.paths.state.write_text(_state_line(1) + "\n", encoding="utf-8")
    bridge.paths.ack.write_text(_ack_line(1) + "\n", encoding="utf-8")
    assert len(bridge.read_states()) == 1
    assert len(bridge.read_acks()) == 1
    assert bridge.read_states() == []
    assert bridge.read_acks() == []


# --- attentes ----------------------------------------------------------------


def test_attente_d_un_accuse(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    bridge.paths.ack.write_text(_ack_line(7) + "\n", encoding="utf-8")
    ack = bridge.wait_for_ack(7, timeout=1.0, sleep=lambda _: None)
    assert ack is not None
    assert ack.sequence == 7


def test_attente_d_un_accuse_qui_arrive_en_retard(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    horloge = iter([0.0, 0.0, 1.0, 2.0, 3.0, 4.0])

    def ecrire_plus_tard(_: float) -> None:
        bridge.paths.ack.write_text(_ack_line(3) + "\n", encoding="utf-8")

    ack = bridge.wait_for_ack(
        3, timeout=10.0, sleep=ecrire_plus_tard, monotonic=lambda: next(horloge)
    )
    assert ack is not None


def test_attente_expiree(bridge: FileBridge) -> None:
    horloge = iter([0.0, 99.0])
    assert (
        bridge.wait_for_ack(1, timeout=1.0, sleep=lambda _: None, monotonic=lambda: next(horloge))
        is None
    )


def test_attente_d_un_etat(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    bridge.paths.state.write_text(_state_line(1) + "\n" + _state_line(2) + "\n", encoding="utf-8")
    state = bridge.wait_for_state(timeout=1.0, sleep=lambda _: None)
    assert state is not None
    assert state.sequence == 2  # le plus recent


# --- messages ----------------------------------------------------------------


def test_aller_retour_des_messages() -> None:
    state = ProbeUnitState(
        unit_id="12345",
        position=Vector3(10.0, 0.0, 20.0),
        unit_type="emp_spearmen",
        controllable=True,
        sequence=1,
        game_time_ms=10000,
    )
    assert ProbeUnitState.from_dict(json.loads(json.dumps(state.to_dict()))) == state

    command = ProbeMoveCommand(unit_id="12345", destination=Vector3(30.0, 0.0, 20.0))
    assert ProbeMoveCommand.from_dict(json.loads(json.dumps(command.to_dict()))) == command

    ack = ProbeAck(sequence=1, status=ProbeStatus.ACCEPTED)
    assert ProbeAck.from_dict(json.loads(json.dumps(ack.to_dict()))) == ack


def test_decodage_par_type() -> None:
    move = ProbeMoveCommand(unit_id="a", destination=Vector3())
    assert isinstance(decode_command(move.to_dict()), ProbeMoveCommand)
    abort = ProbeAbortCommand(reason="stop")
    assert isinstance(decode_command(abort.to_dict()), ProbeAbortCommand)
    with pytest.raises(SchemaError, match="inconnu"):
        decode_command({"protocol_version": PROTOCOL, "type": "danse", "sequence": 1})


def test_message_de_mauvais_type_rejete() -> None:
    ack = ProbeAck(sequence=1, status=ProbeStatus.ACCEPTED)
    with pytest.raises(SchemaError, match="attendu"):
        ProbeUnitState.from_dict(ack.to_dict())


def test_version_incompatible_rejetee() -> None:
    payload = ProbeMoveCommand(unit_id="a", destination=Vector3()).to_dict()
    payload["protocol_version"] = "9.9.9"
    with pytest.raises(SchemaError):
        ProbeMoveCommand.from_dict(payload)


def test_commande_sans_unite_refusee() -> None:
    with pytest.raises(SchemaError, match="designer une unite"):
        ProbeMoveCommand(unit_id="", destination=Vector3())


def test_sequence_invalide_refusee() -> None:
    with pytest.raises(SchemaError, match="sequence"):
        ProbeMoveCommand(unit_id="a", destination=Vector3(), sequence=0)


def test_traduction_vers_le_domaine() -> None:
    """Le raccord vers l'agent existant, une fois la sonde concluante."""
    state = ProbeUnitState(
        unit_id="12345",
        position=Vector3(1.0, 0.0, 2.0),
        unit_type="emp_spearmen",
        controllable=True,
    )
    unit = state.to_unit_state()
    assert unit.id == "12345"
    assert unit.position == Vector3(1.0, 0.0, 2.0)
    assert unit.unit_key == "emp_spearmen"
    assert unit.metadata["controllable"] is True


def test_type_de_message_dans_l_enumeration() -> None:
    assert ProbeMessageType.UNIT_STATE.value == "unit_state"
    assert ProbeMessageType.MOVE_UNIT.value == "move_unit"
    assert ProbeMessageType.ACTION_RESULT.value == "action_result"


# --- entretien et analyse ----------------------------------------------------


def test_reinitialisation(bridge: FileBridge) -> None:
    bridge.paths.ensure()
    bridge.paths.state.write_text(_state_line(1) + "\n", encoding="utf-8")
    bridge.move_unit("a", Vector3())
    bridge.reset()
    assert not bridge.paths.state.exists()
    assert not bridge.paths.command.exists()
    assert bridge.next_sequence == 1


def test_relecture_hors_session(tmp_path: Path) -> None:
    fichier = tmp_path / "etats.jsonl"
    fichier.write_text(
        _state_line(1, x=0.0) + "\nligne cassee\n" + _state_line(2, x=20.0) + "\n",
        encoding="utf-8",
    )
    etats = read_states_from(fichier)
    assert [state.sequence for state in etats] == [1, 2]
    assert "20.0 m parcourus" in summarise(etats)


def test_resume_sans_etat() -> None:
    assert summarise([]) == "aucun etat recu"


def test_relecture_d_un_fichier_absent(tmp_path: Path) -> None:
    assert read_states_from(tmp_path / "absent.jsonl") == []
