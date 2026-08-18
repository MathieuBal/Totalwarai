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
from unittest import mock

import pytest

from totalwar_ai.bridge.command_models import (
    ProbeAbortCommand,
    ProbeAck,
    ProbeMessageType,
    ProbeMoveCommand,
    ProbeStatus,
    ProbeUnitObservation,
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
from totalwar_ai.domain.unit_state import Side

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


def test_les_fins_de_ligne_windows_ne_decalent_pas_l_offset(tmp_path: Path) -> None:
    """Le Lua ecrit en mode texte : sous Windows, cela donne des `\\r\\n`.

    Lire ce fichier en mode texte Python traduit la paire en un seul `\\n`, et
    compter les octets de la chaine obtenue en sous-estime un par ligne.
    L'offset derivait ainsi d'un octet par etat, jusqu'a relire des fragments de
    lignes — constate en bataille apres 157 etats publies.
    """
    bridge = FileBridge.open(tmp_path)
    lignes = [
        json.dumps(
            {
                "protocol_version": "0.1.0",
                "type": "unit_state",
                "sequence": index,
                "game_time_ms": index * 1000,
                "unit": {
                    "id": "1001",
                    "type": "wh3_dlc20_chs_cha_daemon_prince_mnur",
                    "position": {"x": float(index), "y": 34.3, "z": -330.9},
                    "controllable": True,
                },
            }
        )
        for index in range(1, 201)
    ]
    # Ecriture en binaire avec des fins de ligne Windows, comme le jeu.
    bridge.paths.state.write_bytes(("\r\n".join(lignes) + "\r\n").encode("utf-8"))

    # Lecture en plusieurs fois : c'est la que l'offset derive.
    recus: list[ProbeUnitState] = []
    for _ in range(200):
        recus.extend(bridge.read_states())

    assert len(recus) == 200, f"{len(recus)} etats lus sur 200"
    assert not bridge.malformed, [item.content[:60] for item in bridge.malformed]
    assert [state.sequence for state in recus] == list(range(1, 201))
    assert recus[-1].position.x == pytest.approx(200.0)


def test_une_ligne_windows_incomplete_n_est_pas_consommee(tmp_path: Path) -> None:
    """Une ligne encore en cours d'ecriture doit etre reprise entiere ensuite."""
    bridge = FileBridge.open(tmp_path)
    complete = json.dumps(
        {
            "protocol_version": "0.1.0",
            "type": "unit_state",
            "sequence": 1,
            "game_time_ms": 1000,
            "unit": {
                "id": "1001",
                "type": "unite",
                "position": {"x": 1.0, "y": 0.0, "z": 0.0},
                "controllable": True,
            },
        }
    )
    suivante = complete.replace('"sequence": 1', '"sequence": 2')

    bridge.paths.state.write_bytes((complete + "\r\n" + suivante[:40]).encode("utf-8"))
    assert [state.sequence for state in bridge.read_states()] == [1]

    # Le Lua termine sa ligne.
    with bridge.paths.state.open("ab") as handle:
        handle.write((suivante[40:] + "\r\n").encode("utf-8"))
    assert [state.sequence for state in bridge.read_states()] == [2]
    assert not bridge.malformed


def test_une_unite_exsangue_ne_vaut_pas_la_moitie_d_une_intacte() -> None:
    """`effective_strength` multiplie effectifs et sante ; le jeu ne donne que la sante.

    `number_of_men` est absent du bac a sable Lua. Laisser `entity_ratio` a 1
    ferait valoir 0,5 a une unite a l'agonie — de quoi fausser tous les rapports
    de puissance, donc le choix de posture et le ciblage.
    """
    intacte = ProbeUnitObservation(
        unit_id="1", position=Vector3(0.0, 0.0, 0.0), hitpoints=1.0
    ).to_unit_state(Side.ALLY)
    exsangue = ProbeUnitObservation(
        unit_id="2", position=Vector3(0.0, 0.0, 0.0), hitpoints=0.05
    ).to_unit_state(Side.ALLY)

    assert intacte.effective_strength == pytest.approx(1.0)
    assert exsangue.effective_strength < 0.1


def test_une_sante_absente_ne_penalise_pas_l_unite() -> None:
    """Faute de donnee, supposer l'unite intacte plutot que d'inventer un chiffre."""
    inconnue = ProbeUnitObservation(
        unit_id="1", position=Vector3(0.0, 0.0, 0.0), hitpoints=None
    ).to_unit_state(Side.ALLY)
    assert inconnue.effective_strength == pytest.approx(1.0)
    assert inconnue.metadata["morale_available"] is False


def test_le_remplacement_attend_que_le_jeu_lache_le_fichier(tmp_path: Path) -> None:
    """Windows refuse de remplacer un fichier qu'un autre processus tient ouvert.

    Le Lua relit la commande toutes les 500 ms ; un `os.replace` tombe pendant
    cette lecture leve `PermissionError`, et une session de supervision est
    morte a la 235e seconde de bataille pour cette raison. Ce n'est pas un
    probleme de droits mais une course.
    """
    import totalwar_ai.bridge.file_bridge as module

    bridge = FileBridge.open(tmp_path)
    essais = {"n": 0}
    vrai_replace = os.replace

    def refuse_deux_fois(source: object, destination: object) -> None:
        essais["n"] += 1
        if essais["n"] <= 2:
            raise PermissionError(5, "Acces refuse")
        vrai_replace(source, destination)  # type: ignore[arg-type]

    with (
        mock.patch.object(module.os, "replace", refuse_deux_fois),
        mock.patch.object(module.time, "sleep", lambda _: None),
    ):
        bridge.delegate(["1001"])

    assert essais["n"] == 3, "le pont n'a pas insiste"
    assert bridge.paths.command.exists()


def test_un_verrou_qui_ne_lache_jamais_finit_par_lever(tmp_path: Path) -> None:
    """Insister indefiniment masquerait une vraie panne."""
    import totalwar_ai.bridge.file_bridge as module

    bridge = FileBridge.open(tmp_path)

    def refuse_toujours(source: object, destination: object) -> None:
        raise PermissionError(5, "Acces refuse")

    with (
        mock.patch.object(module.os, "replace", refuse_toujours),
        mock.patch.object(module.time, "sleep", lambda _: None),
        pytest.raises(PermissionError),
    ):
        bridge.delegate(["1001"])

    # Le temporaire ne doit pas rester derriere lui.
    assert not list(tmp_path.glob("**/.totalwar_ai_command-*.tmp"))


def test_la_sentinelle_d_experience_est_absente_par_defaut(tmp_path: Path) -> None:
    """Le contrat d'isolation : rien ne commande d'unite sans demande explicite.

    Le chronometrage du tir appelle `start_move` et confisque des tireurs
    jusqu'a trente secondes. Actif par defaut, il contaminerait toute session de
    pilotage — et LIVE-001 conclurait a des ordres perdus la ou c'est notre
    propre experience qui occupait l'unite.
    """
    paths = BridgePaths(directory=tmp_path / "totalwar_ai")
    paths.ensure()
    assert not paths.experiment.exists()
    assert paths.experiment.name == "totalwar_ai_experiment"
    assert paths.experiment != paths.stop, "deux sentinelles distinctes"
