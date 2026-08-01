"""Execution reelle du script Lua contre un faux jeu.

Ces tests **exécutent** `totalwar_ai_probe.lua` dans un interpreteur Lua, avec
un faux battle_manager (`tests/fixtures/fake_battle.lua`). Ils valident donc la
logique du script — analyse JSON, regles de sequence, prise et restitution du
controle — et non plus seulement sa coherence textuelle avec Python.

**Deux limites a garder en tete.**

1. Le faux jeu n'est pas le jeu : il ne dit rien du comportement reel du moteur,
   des groupes verrouillees, ni du droit d'ecriture en contexte bataille.
2. `lupa` fournit un Lua plus recent que le 5.1 du jeu. La syntaxe employee par
   la sonde est commune aux deux, mais une incompatibilite propre a 5.1 ne
   serait pas vue ici.

Ce que ces tests attrapent en revanche, et qui a reellement coute un essai en
bataille : une erreur qui tue un rappel periodique en silence, une methode d'API
mal nommee, un chemin de code qui ne journalise rien.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa", reason="lupa n'est pas installe")

from totalwar_ai.bridge.file_bridge import FileBridge  # noqa: E402
from totalwar_ai.domain.geometry import Vector3  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "lua_mod" / "script" / "battle" / "mod" / "totalwar_ai_probe.lua"
FAKE_BATTLE = ROOT / "tests" / "fixtures" / "fake_battle.lua"


class Probe:
    """Sonde Lua chargee dans un interpreteur, pilotable depuis Python."""

    def __init__(self, workdir: Path, units: list[tuple[str, str, float, float, bool]]) -> None:
        from lupa import LuaRuntime

        self.workdir = workdir
        self.runtime = LuaRuntime(unpack_returned_tuples=True)
        self.lua = self.runtime.globals()

        # Le script ouvre des chemins relatifs : on se place dans le repertoire
        # qui joue le role du dossier d'installation du jeu.
        self.runtime.execute(f"FAKE_WORKDIR = {json.dumps(str(workdir))}")
        self.runtime.execute(
            "local real_open = io.open\n"
            "io.open = function(path, mode)\n"
            "  if string.sub(path, 1, 2) == './' then\n"
            "    path = FAKE_WORKDIR .. '/' .. string.sub(path, 3)\n"
            "  end\n"
            "  return real_open(path, mode)\n"
            "end\n"
        )

        self.runtime.execute(FAKE_BATTLE.read_text(encoding="utf-8"))
        self.fake = self.lua.FAKE
        lua_units = self.runtime.eval("function(f) return {} end")(self.fake)
        table = self.runtime.eval("{}")
        for index, (unit_id, unit_type, x, z, controllable) in enumerate(units, start=1):
            table[index] = self.fake.make_unit(self.fake, unit_id, unit_type, x, z, controllable)
        del lua_units
        self.fake.setup(self.fake, table)

        self.runtime.execute(PROBE.read_text(encoding="utf-8"))

    def advance(self, ms: int) -> None:
        self.fake.advance(self.fake, ms)

    def log(self) -> list[str]:
        raw = self.fake.log
        return [raw[index] for index in range(1, len(raw) + 1)]

    def grep(self, fragment: str) -> list[str]:
        return [line for line in self.log() if fragment in line]

    def orders(self) -> list[dict[str, object]]:
        raw = self.fake.orders
        result = []
        for index in range(1, len(raw) + 1):
            entry = raw[index]
            result.append(
                {key: entry[key] for key in ("kind", "unit_id", "x", "z") if entry[key] is not None}
            )
        return result


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Repertoire jouant le role du dossier d'installation du jeu."""
    (tmp_path / "totalwar_ai").mkdir()
    return tmp_path


@pytest.fixture
def probe(workdir: Path) -> Probe:
    return Probe(
        workdir,
        units=[
            ("1006", "wh3_main_nur_inf_plaguebearers_1", 24.3, -303.2, True),
            ("1005", "wh3_main_nur_inf_plaguebearers_1", -11.0, -302.9, True),
        ],
    )


# --- chargement et demarrage -------------------------------------------------


def test_le_script_s_annonce_au_chargement(probe: Probe) -> None:
    assert probe.grep("=== fichier charge")
    assert probe.grep("contexte de bataille detecte")


def test_la_sonde_demarre(probe: Probe) -> None:
    probe.advance(1500)
    assert probe.grep("sonde active")


def test_le_diagnostic_des_entrees_sorties_est_publie(probe: Probe) -> None:
    """Le point de faisabilite central doit etre tranche des le demarrage."""
    probe.advance(1500)
    assert probe.grep("diagnostic des entrees-sorties")
    assert probe.grep("io.open disponible")
    assert probe.grep("ECRITURE OK")


def test_le_diagnostic_distingue_dossier_absent_et_droit_refuse(tmp_path: Path) -> None:
    """Sans le dossier d'echange, le message doit le dire explicitement."""
    # Pas de `mkdir` : le dossier `totalwar_ai/` n'existe pas.
    probe = Probe(tmp_path, units=[("1", "type", 0.0, 0.0, True)])
    probe.advance(1500)
    assert probe.grep("ecriture refusee dans")
    assert probe.grep("est absent")
    assert probe.grep("creer ce dossier")


def test_l_armee_est_recensee_au_demarrage(probe: Probe) -> None:
    probe.advance(1500)
    lignes = probe.grep("armee du joueur")
    assert lignes
    assert "2 unites" in lignes[0]
    assert "2 controlables" in lignes[0]


def test_le_multijoueur_bloque_la_sonde(workdir: Path) -> None:
    probe = Probe(workdir, units=[("1", "type", 0.0, 0.0, True)])
    probe.fake.multiplayer = True
    probe.advance(1500)
    assert probe.grep("multijoueur")
    assert not probe.grep("sonde active")


# --- publication d'etat ------------------------------------------------------


def test_l_etat_est_ecrit_dans_le_fichier(probe: Probe, workdir: Path) -> None:
    probe.advance(3000)
    bridge = FileBridge.open(workdir)
    etats = bridge.read_states()
    assert etats
    assert etats[0].unit_id == "1006"
    assert etats[0].unit_type == "wh3_main_nur_inf_plaguebearers_1"
    assert etats[0].position.x == pytest.approx(24.3, abs=0.01)
    assert etats[0].position.z == pytest.approx(-303.2, abs=0.01)
    assert etats[0].controllable


def test_l_etat_est_du_json_valide(probe: Probe, workdir: Path) -> None:
    """L'encodeur JSON du Lua est ecrit a la main : il doit produire du JSON."""
    probe.advance(2000)
    lignes = (
        (workdir / "totalwar_ai" / "totalwar_ai_state.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    charges = [json.loads(ligne) for ligne in lignes if ligne.strip()]
    assert charges
    assert charges[0]["type"] == "unit_state"
    assert charges[0]["protocol_version"] == "0.1.0"


def test_l_absence_d_unite_controlable_est_expliquee(workdir: Path) -> None:
    """Le defaut trouve lors du deuxieme essai : ne jamais se taire."""
    probe = Probe(workdir, units=[("1", "type", 0.0, 0.0, False)])
    probe.advance(3000)
    lignes = probe.grep("aucune unite controlable")
    assert lignes
    assert "1 unites vues" in lignes[0]
    assert "0 controlables" in lignes[0]


def test_le_journal_ne_se_noie_pas(workdir: Path) -> None:
    """Le message d'echec se repete de loin en loin, pas a chaque tick."""
    probe = Probe(workdir, units=[("1", "type", 0.0, 0.0, False)])
    probe.advance(15000)
    assert 3 <= len(probe.grep("aucune unite controlable")) <= 5


# --- aller-retour complet ----------------------------------------------------


def test_aller_retour_complet(probe: Probe, workdir: Path) -> None:
    """Le scenario du ticket, execute par le vrai script Lua."""
    probe.advance(2000)
    bridge = FileBridge.open(workdir)

    etat = bridge.read_states()[-1]
    depart = etat.position

    destination = Vector3(depart.x + 20.0, depart.y, depart.z)
    commande = bridge.move_unit(etat.unit_id, destination)

    probe.advance(1000)

    acks = bridge.read_acks()
    assert acks, "aucun accuse ecrit par le Lua"
    assert acks[0].sequence == commande.sequence
    assert acks[0].accepted

    ordres = probe.orders()
    deplacements = [ordre for ordre in ordres if ordre["kind"] == "goto"]
    assert deplacements
    assert deplacements[0]["unit_id"] == etat.unit_id
    assert deplacements[0]["x"] == pytest.approx(depart.x + 20.0, abs=0.01)

    # Le controle doit revenir au joueur apres le delai.
    probe.advance(6000)
    assert any(ordre["kind"] == "release" for ordre in probe.orders())
    liberation = [ack for ack in bridge.read_acks() if ack.status.value == "released"]
    assert liberation


def test_une_commande_n_est_pas_rejouee(probe: Probe, workdir: Path) -> None:
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    bridge.move_unit("1006", Vector3(100.0, 0.0, 100.0))

    probe.advance(1000)
    premiers = len([ordre for ordre in probe.orders() if ordre["kind"] == "goto"])
    assert premiers == 1

    # Le fichier n'a pas change : aucun nouvel ordre ne doit partir.
    probe.advance(3000)
    ensuite = len([ordre for ordre in probe.orders() if ordre["kind"] == "goto"])
    assert ensuite == premiers


def test_une_unite_inconnue_est_refusee(probe: Probe, workdir: Path) -> None:
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    commande = bridge.move_unit("99999", Vector3(1.0, 0.0, 2.0))
    probe.advance(1000)

    acks = [ack for ack in bridge.read_acks() if ack.sequence == commande.sequence]
    assert acks
    assert not acks[0].accepted
    assert acks[0].error is not None
    assert "introuvable" in acks[0].error


def test_une_commande_invalide_est_refusee(probe: Probe, workdir: Path) -> None:
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    bridge.paths.command.write_text(
        json.dumps({"protocol_version": "0.1.0", "type": "move_unit", "sequence": 7}),
        encoding="utf-8",
    )
    probe.advance(1000)
    acks = [ack for ack in bridge.read_acks() if ack.sequence == 7]
    assert acks
    assert not acks[0].accepted


def test_une_version_inconnue_est_ignoree(probe: Probe, workdir: Path) -> None:
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    bridge.paths.command.write_text(
        json.dumps(
            {
                "protocol_version": "9.9.9",
                "type": "move_unit",
                "sequence": 3,
                "unit_id": "1006",
                "destination": {"x": 0, "y": 0, "z": 0},
            }
        ),
        encoding="utf-8",
    )
    probe.advance(1000)
    assert bridge.read_acks() == []


# --- securite ----------------------------------------------------------------


def test_l_arret_libere_tout(probe: Probe, workdir: Path) -> None:
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    bridge.move_unit("1006", Vector3(50.0, 0.0, 50.0))
    probe.advance(1000)

    bridge.abort("essai termine")
    probe.advance(1000)

    assert probe.grep("SONDE ARRETEE")
    assert any(ordre["kind"] == "release" for ordre in probe.orders())


def test_la_sentinelle_seule_suffit(probe: Probe, workdir: Path) -> None:
    """Le canal d'arret doit fonctionner sans passer par l'analyse des commandes."""
    probe.advance(2000)
    (workdir / "totalwar_ai" / "totalwar_ai_stop").write_text("stop\n", encoding="utf-8")
    probe.advance(1000)
    assert probe.grep("SONDE ARRETEE")


def test_la_fin_de_bataille_libere_tout(probe: Probe, workdir: Path) -> None:
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    bridge.move_unit("1006", Vector3(50.0, 0.0, 50.0))
    probe.advance(1000)

    probe.fake.phase_callbacks["Complete"]()
    assert probe.grep("fin de bataille")
    assert any(ordre["kind"] == "release" for ordre in probe.orders())


def test_le_double_chargement_est_ignore(probe: Probe) -> None:
    """Le fichier peut etre place a deux emplacements dans le pack."""
    probe.runtime.execute(PROBE.read_text(encoding="utf-8"))
    assert probe.grep("second exemplaire ignore")
