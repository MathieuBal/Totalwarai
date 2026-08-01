"""Contrat commun entre le script Lua et le pont Python.

**Ce que ces tests prouvent, et ce qu'ils ne prouvent pas.**

Ils verifient que les deux moities parlent le meme protocole : que le JSON
produit par Python est lisible par l'analyseur du Lua, que les regles de
sequence sont respectees, que le script declare les memes chemins et la meme
version que Python.

Ils ne prouvent **rien** sur le comportement du jeu. Aucun interpreteur Lua
n'est execute ici, et encore moins WARHAMMER III. La partie « le script est
charge », « une unite reelle est detectee », « l'unite se deplace » ne peut etre
etablie que par un essai en bataille — voir `docs/feasibility.md`, ou ces points
sont explicitement marques comme non testes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from totalwar_ai.bridge.command_models import (
    ProbeAck,
    ProbeStatus,
    ProbeUnitState,
    decode_command,
)
from totalwar_ai.bridge.file_bridge import FileBridge
from totalwar_ai.domain.geometry import Vector3

LUA_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "lua_mod"
    / "script"
    / "battle"
    / "mod"
    / "totalwar_ai_probe.lua"
)


@pytest.fixture(scope="module")
def lua_source() -> str:
    return LUA_SCRIPT.read_text(encoding="utf-8")


# --- coherence des deux moities ----------------------------------------------


def test_le_script_lua_existe() -> None:
    assert LUA_SCRIPT.is_file()


def test_les_noms_de_fichiers_concordent(lua_source: str, tmp_path: Path) -> None:
    """Le Lua code les chemins en dur : ils doivent correspondre a `paths.py`."""
    paths = FileBridge.open(tmp_path).paths
    for path in (paths.state, paths.command, paths.ack, paths.stop):
        assert path.name in lua_source, path.name


def test_la_version_de_protocole_concorde(lua_source: str) -> None:
    from totalwar_ai.bridge.protocol import PROTOCOL_VERSION

    assert f'protocol_version = "{PROTOCOL_VERSION}"' in lua_source


def test_les_types_de_messages_concordent(lua_source: str) -> None:
    for message_type in ("unit_state", "move_unit", "action_result", "abort"):
        assert message_type in lua_source, message_type


def test_le_script_ne_contient_pas_de_code_tiers(lua_source: str) -> None:
    """Garde-fou de licence : aucune trace du mod etudie ne doit subsister."""
    interdits = (
        "aigeneral",
        "pancake",
        "modder_API_uc_manager",
        "pan_util",
        "script_ai_planner",
    )
    minuscules = lua_source.lower()
    for terme in interdits:
        assert terme.lower() not in minuscules, terme


def test_le_depot_ne_contient_aucun_fichier_tiers() -> None:
    """Aucun `.pack`, et aucun Lua qui ne soit le notre."""
    racine = Path(__file__).resolve().parents[2]
    assert list(racine.rglob("*.pack")) == []

    autorises = {"totalwar_ai_probe.lua", "fake_battle.lua"}
    interdits = ("aigeneral", "pancake", "modder_api_uc_manager", "pan_util")
    for chemin in racine.rglob("*.lua"):
        if ".venv" in chemin.parts:
            continue
        assert chemin.name in autorises, chemin
        contenu = chemin.read_text(encoding="utf-8").lower()
        for terme in interdits:
            assert terme not in contenu, f"{chemin} contient {terme}"


# --- reproduction de l'analyseur Lua -----------------------------------------
#
# Le Lua analyse le JSON avec trois motifs `string.match`. On reproduit ici leur
# semantique exacte pour verifier qu'ils lisent bien ce que Python ecrit. C'est
# une reproduction de la logique, pas une execution du Lua : si le script change
# ses motifs, `test_les_motifs_lua_sont_ceux_reproduits` le signale.

_LUA_STRING = r'"{key}"\s*:\s*"([^"]*)"'
_LUA_NUMBER = r'"{key}"\s*:\s*(-?\d+\.?\d*)'


def lua_read_string(text: str, key: str) -> str | None:
    match = re.search(_LUA_STRING.format(key=key), text)
    return match.group(1) if match else None


def lua_read_number(text: str, key: str) -> float | None:
    match = re.search(_LUA_NUMBER.format(key=key), text)
    return float(match.group(1)) if match else None


def lua_read_balanced_block(text: str, key: str) -> str | None:
    """Equivalent de `%b{}` : le bloc d'accolades equilibrees suivant la cle."""
    match = re.search(rf'"{key}"\s*:\s*', text)
    if match is None:
        return None
    start = match.end()
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def lua_read_vector(text: str, key: str) -> dict[str, float] | None:
    block = lua_read_balanced_block(text, key)
    if block is None:
        return None
    x = lua_read_number(block, "x")
    y = lua_read_number(block, "y")
    z = lua_read_number(block, "z")
    if x is None or z is None:
        return None
    return {"x": x, "y": y or 0.0, "z": z}


def test_les_motifs_lua_sont_ceux_reproduits(lua_source: str) -> None:
    """Si le Lua change ses motifs, cette reproduction devient mensongere."""
    assert '\'"\' .. key .. \'"%s*:%s*"([^"]*)"\'' in lua_source
    assert "'\"' .. key .. '\"%s*:%s*(-?%d+%.?%d*)'" in lua_source
    assert "'\"' .. key .. '\"%s*:%s*(%b{})'" in lua_source


# --- l'analyseur Lua lit-il ce que Python ecrit ? ----------------------------


def test_le_lua_lit_la_commande_produite_par_python(tmp_path: Path) -> None:
    bridge = FileBridge.open(tmp_path)
    bridge.move_unit("12345", Vector3(30.0, 0.0, 20.5), release_after_ms=5000)
    brut = bridge.paths.command.read_text(encoding="utf-8")

    assert lua_read_string(brut, "protocol_version") == "0.1.0"
    assert lua_read_string(brut, "type") == "move_unit"
    assert lua_read_string(brut, "unit_id") == "12345"
    assert lua_read_number(brut, "sequence") == 1
    assert lua_read_number(brut, "release_after_ms") == 5000
    assert lua_read_vector(brut, "destination") == {"x": 30.0, "y": 0.0, "z": 20.5}


def test_le_json_indente_reste_lisible_par_le_lua(tmp_path: Path) -> None:
    """Python ecrit du JSON indente : les motifs Lua tolerent les espaces."""
    bridge = FileBridge.open(tmp_path)
    bridge.move_unit("a", Vector3(1.0, 2.0, 3.0))
    brut = bridge.paths.command.read_text(encoding="utf-8")
    assert "\n" in brut and "  " in brut  # bien indente
    assert lua_read_vector(brut, "destination") == {"x": 1.0, "y": 2.0, "z": 3.0}


@pytest.mark.parametrize(
    ("x", "z"),
    [(0.0, 0.0), (-125.5, 82.25), (1234.0, -9876.5), (0.001, -0.001)],
)
def test_coordonnees_variees_restent_lisibles(tmp_path: Path, x: float, z: float) -> None:
    bridge = FileBridge.open(tmp_path)
    bridge.move_unit("a", Vector3(x, 0.0, z))
    brut = bridge.paths.command.read_text(encoding="utf-8")
    vecteur = lua_read_vector(brut, "destination")
    assert vecteur is not None
    assert vecteur["x"] == pytest.approx(x)
    assert vecteur["z"] == pytest.approx(z)


@pytest.mark.parametrize("minuscule", [1e-05, -1e-07, 2.5e-09])
def test_pas_de_notation_scientifique(tmp_path: Path, minuscule: float) -> None:
    """Piege reel : `1e-05` serait lu `1` par le motif Lua, sans erreur visible.

    Les coordonnees sont donc arrondies avant ecriture. Ce test a ete ecrit
    apres avoir constate le defaut.
    """
    bridge = FileBridge.open(tmp_path)
    bridge.move_unit("a", Vector3(minuscule, 0.0, minuscule))
    bloc = lua_read_balanced_block(bridge.paths.command.read_text(encoding="utf-8"), "destination")
    assert bloc is not None
    assert "e" not in bloc.lower()
    vecteur = lua_read_vector(bridge.paths.command.read_text(encoding="utf-8"), "destination")
    assert vecteur == {"x": 0.0, "y": 0.0, "z": 0.0}


def test_commande_d_arret_lisible_par_le_lua(tmp_path: Path) -> None:
    bridge = FileBridge.open(tmp_path)
    bridge.abort("essai termine")
    brut = bridge.paths.command.read_text(encoding="utf-8")
    assert lua_read_string(brut, "type") == "abort"
    assert bridge.paths.stop.exists()


# --- le Lua simule : regles du protocole -------------------------------------


class FauxLua:
    """Reproduction en Python des regles que le script Lua doit respecter.

    Ce n'est **pas** le script : c'est un banc qui verifie que les regles du
    protocole tiennent debout. Le vrai script doit se comporter de meme, ce que
    seul un essai en bataille peut confirmer.
    """

    def __init__(self, bridge: FileBridge) -> None:
        self.bridge = bridge
        self.last_sequence = 0
        self.controlled: set[str] = set()
        self.aborted = False
        self.units = {"12345": Vector3(10.0, 0.0, 20.0)}

    def _append(self, path: Path, payload: dict[str, object]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def publish_state(self, unit_id: str = "12345") -> None:
        self.last_state_sequence = getattr(self, "last_state_sequence", 0) + 1
        state = ProbeUnitState(
            unit_id=unit_id,
            position=self.units[unit_id],
            unit_type="emp_spearmen",
            controllable=True,
            sequence=self.last_state_sequence,
            game_time_ms=self.last_state_sequence * 1000,
        )
        self.bridge.paths.ensure()
        self._append(self.bridge.paths.state, state.to_dict())

    def poll(self) -> str | None:
        """Un tick de la boucle Lua. Renvoie le statut emis, ou `None`."""
        if self.aborted:
            return None
        if self.bridge.paths.stop.exists():
            self.controlled.clear()
            self.aborted = True
            return None
        if not self.bridge.paths.command.exists():
            return None

        brut = self.bridge.paths.command.read_text(encoding="utf-8")
        if lua_read_string(brut, "protocol_version") != "0.1.0":
            return None
        sequence = lua_read_number(brut, "sequence")
        if sequence is None or int(sequence) <= self.last_sequence:
            return None  # deja traitee, ou incomplete

        self.last_sequence = int(sequence)
        command_type = lua_read_string(brut, "type")

        if command_type == "abort":
            self.controlled.clear()
            self.aborted = True
            return self._ack(int(sequence), ProbeStatus.RELEASED)

        if command_type != "move_unit":
            return self._ack(int(sequence), ProbeStatus.REJECTED, "type inconnu")

        unit_id = lua_read_string(brut, "unit_id")
        destination = lua_read_vector(brut, "destination")
        if not unit_id or destination is None:
            return self._ack(int(sequence), ProbeStatus.REJECTED, "commande incomplete")
        if unit_id not in self.units:
            return self._ack(int(sequence), ProbeStatus.REJECTED, "unite introuvable")

        self.controlled.add(unit_id)
        self.units[unit_id] = Vector3(destination["x"], destination["y"], destination["z"])
        return self._ack(int(sequence), ProbeStatus.ACCEPTED)

    def release_all(self, sequence: int) -> str:
        self.controlled.clear()
        return self._ack(sequence, ProbeStatus.RELEASED)

    def _ack(self, sequence: int, status: ProbeStatus, error: str | None = None) -> str:
        self.bridge.paths.ensure()
        self._append(
            self.bridge.paths.ack, ProbeAck(sequence=sequence, status=status, error=error).to_dict()
        )
        return status.value


@pytest.fixture
def duo(tmp_path: Path) -> tuple[FileBridge, FauxLua]:
    bridge = FileBridge.open(tmp_path)
    return bridge, FauxLua(bridge)


def test_aller_retour_complet(duo: tuple[FileBridge, FauxLua]) -> None:
    """Le scenario du ticket, de bout en bout, cote protocole."""
    bridge, lua = duo

    # 1-5. Le Lua publie un etat, Python le lit.
    lua.publish_state()
    state = bridge.wait_for_state(timeout=1.0, sleep=lambda _: None)
    assert state is not None
    assert state.unit_id == "12345"
    assert state.controllable
    depart = state.position

    # 6-7. Python ordonne un deplacement d'environ vingt metres, le Lua le lit.
    destination = Vector3(depart.x + 20.0, depart.y, depart.z)
    commande = bridge.move_unit(state.unit_id, destination)
    assert lua.poll() == "accepted"

    # 8-9. L'unite a bouge, Python recoit l'accuse.
    ack = bridge.wait_for_ack(commande.sequence, timeout=1.0, sleep=lambda _: None)
    assert ack is not None
    assert ack.accepted
    lua.publish_state()
    arrivee = bridge.wait_for_state(timeout=1.0, sleep=lambda _: None)
    assert arrivee is not None
    assert arrivee.position.distance_2d(depart) == pytest.approx(20.0)

    # 10. Le controle revient au joueur.
    lua.release_all(commande.sequence)
    assert lua.controlled == set()
    liberation = [item for item in bridge.read_acks() if item.status is ProbeStatus.RELEASED]
    assert liberation


def test_une_commande_n_est_jamais_rejouee(duo: tuple[FileBridge, FauxLua]) -> None:
    bridge, lua = duo
    bridge.move_unit("12345", Vector3(30.0, 0.0, 20.0))
    assert lua.poll() == "accepted"
    # Le fichier n'a pas change : les ticks suivants ne doivent rien faire.
    assert lua.poll() is None
    assert lua.poll() is None
    assert len(bridge.read_acks()) == 1


def test_une_sequence_ancienne_est_ignoree(duo: tuple[FileBridge, FauxLua]) -> None:
    from totalwar_ai.bridge.command_models import ProbeMoveCommand

    bridge, lua = duo
    bridge.send_command(ProbeMoveCommand(unit_id="12345", destination=Vector3(), sequence=5))
    assert lua.poll() == "accepted"
    bridge.send_command(ProbeMoveCommand(unit_id="12345", destination=Vector3(), sequence=3))
    assert lua.poll() is None


def test_commande_incomplete_refusee(duo: tuple[FileBridge, FauxLua]) -> None:
    bridge, lua = duo
    bridge.paths.ensure()
    bridge.paths.command.write_text(
        json.dumps({"protocol_version": "0.1.0", "type": "move_unit", "sequence": 1}),
        encoding="utf-8",
    )
    assert lua.poll() == "rejected"
    acks = bridge.read_acks()
    assert acks[0].error == "commande incomplete"


def test_commande_de_version_inconnue_ignoree(duo: tuple[FileBridge, FauxLua]) -> None:
    bridge, lua = duo
    bridge.paths.ensure()
    bridge.paths.command.write_text(
        json.dumps(
            {
                "protocol_version": "9.9.9",
                "type": "move_unit",
                "sequence": 1,
                "unit_id": "12345",
                "destination": {"x": 0, "y": 0, "z": 0},
            }
        ),
        encoding="utf-8",
    )
    assert lua.poll() is None
    assert bridge.read_acks() == []


def test_unite_inconnue_refusee(duo: tuple[FileBridge, FauxLua]) -> None:
    bridge, lua = duo
    commande = bridge.move_unit("99999", Vector3(1.0, 0.0, 2.0))
    assert lua.poll() == "rejected"
    ack = bridge.wait_for_ack(commande.sequence, timeout=1.0, sleep=lambda _: None)
    assert ack is not None
    assert not ack.accepted
    assert ack.error == "unite introuvable"


def test_l_arret_libere_tout_et_coupe_la_lecture(duo: tuple[FileBridge, FauxLua]) -> None:
    bridge, lua = duo
    bridge.move_unit("12345", Vector3(30.0, 0.0, 20.0))
    lua.poll()
    assert lua.controlled == {"12345"}

    bridge.abort("essai termine")
    assert lua.poll() is None
    assert lua.aborted
    assert lua.controlled == set()

    # Plus rien n'est execute apres l'arret.
    bridge.clear_stop()
    bridge.move_unit("12345", Vector3(99.0, 0.0, 0.0))
    assert lua.poll() is None


def test_la_sentinelle_seule_suffit(duo: tuple[FileBridge, FauxLua]) -> None:
    """Le canal d'arret doit fonctionner meme si l'analyse des commandes echoue."""
    bridge, lua = duo
    bridge.paths.ensure()
    bridge.paths.stop.write_text("stop\n", encoding="utf-8")
    assert lua.poll() is None
    assert lua.aborted


def test_les_commandes_sont_decodables_par_le_domaine(tmp_path: Path) -> None:
    """Ce que Python ecrit doit aussi se relire par le decodeur du domaine."""
    bridge = FileBridge.open(tmp_path)
    commande = bridge.move_unit("12345", Vector3(30.0, 0.0, 20.0))
    relu = decode_command(json.loads(bridge.paths.command.read_text(encoding="utf-8")))
    assert relu == commande
