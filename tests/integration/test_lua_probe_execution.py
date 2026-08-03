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

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

lupa = pytest.importorskip("lupa", reason="lupa n'est pas installe")

from totalwar_ai.agent.unit_classifier import UnitClassifier  # noqa: E402
from totalwar_ai.bridge.file_bridge import FileBridge  # noqa: E402
from totalwar_ai.domain.geometry import Vector3  # noqa: E402
from totalwar_ai.domain.unit_state import Side, UnitRole  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "lua_mod" / "script" / "battle" / "mod" / "totalwar_ai_probe.lua"
FAKE_BATTLE = ROOT / "tests" / "fixtures" / "fake_battle.lua"


class Probe:
    """Sonde Lua chargee dans un interpreteur, pilotable depuis Python."""

    def __init__(
        self,
        workdir: Path,
        units: list[tuple[str, str, float, float, bool]],
        *,
        enemies: list[tuple[str, str, float, float, bool]] | None = None,
        reinforcements: list[tuple[str, str, float, float, bool]] | None = None,
        restricted_math: bool = False,
    ) -> None:
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

        if restricted_math:
            # Le sandbox Lua du jeu ne fournit pas tout : `math.huge` y vaut nil,
            # ce qui a fait echouer la sonde lors du troisieme essai en bataille.
            # Le faux jeu doit pouvoir reproduire cette restriction.
            self.runtime.execute("MATH_BACKUP = { huge = math.huge, floor = math.floor }")

        self.runtime.execute(FAKE_BATTLE.read_text(encoding="utf-8"))

        if restricted_math:
            self.runtime.execute("math.huge = nil\nmath.floor = nil\n")
        self.fake = self.lua.FAKE
        self.fake.setup(
            self.fake,
            self._lua_units(units),
            self._lua_units(enemies),
            self._lua_units(reinforcements),
        )

        self.runtime.execute(PROBE.read_text(encoding="utf-8"))

    def _lua_units(self, units: list[tuple[str, str, float, float, bool]] | None) -> Any:
        """Table Lua d'unites, ou `nil` quand il n'y a pas de camp adverse."""
        if units is None:
            return None
        table = self.runtime.eval("{}")
        for index, (unit_id, unit_type, x, z, controllable) in enumerate(units, start=1):
            table[index] = self.fake.make_unit(self.fake, unit_id, unit_type, x, z, controllable)
        return table

    def set_men(self, unit_id: str, men: int) -> None:
        """Fixe l'effectif d'une unite du faux jeu.

        Le seigneur n'a qu'une entite, la troupe en a plusieurs : c'est ce qui
        permet de verifier que le recensement observe bien les deux.
        """
        self.runtime.execute(
            "local units = bm:alliances():item(1):armies():item(1):units()\n"
            "for i = 1, units:count() do\n"
            "  local u = units:item(i)\n"
            f"  if tostring(u:unique_ui_id()) == {json.dumps(unit_id)} then\n"
            f"    u.men = {men}\n    u.men_alive = {men}\n"
            "  end\n"
            "end\n"
        )

    def kill_unit(self, unit_id: str) -> None:
        """Detruit une unite : plus un homme debout.

        **C'est ainsi que le jeu marque une unite detruite**, et non par
        `is_valid_target`. Mesure en bataille reelle : `number_of_men_alive == 0`
        n'apparait jamais — le jeu retire l'unite de ses listes — tandis que
        `is_valid_target() == false` apparait 1 942 fois sur des unites bien
        vivantes. Le faux jeu encodait auparavant notre erreur, et c'est ce qui
        a laisse passer le defaut.
        """
        self._patch_unit(unit_id, "u.men_alive = 0")

    def make_untargetable(self, unit_id: str) -> None:
        """Rend une unite non ciblable **sans la tuer**, comme le jeu le fait.

        Trois unites de tir sont restees ainsi pendant six minutes de bataille,
        avec soixante-huit hommes et un carquois plein.
        """
        self._patch_unit(unit_id, "u.is_valid_target = function() return false end")

    def _patch_unit(self, unit_id: str, mutation: str) -> None:
        self.runtime.execute(
            "for _, alliance in ipairs({bm:alliances():item(1)}) do\n"
            "  local units = alliance:armies():item(1):units()\n"
            "  for i = 1, units:count() do\n"
            "    local u = units:item(i)\n"
            f"    if tostring(u:unique_ui_id()) == {json.dumps(unit_id)} then\n"
            f"      {mutation}\n"
            "    end\n"
            "  end\n"
            "end\n"
        )

    def advance(self, ms: int) -> None:
        self.fake.advance(self.fake, ms)

    def enter_phase(self, name: str) -> None:
        """Declenche le rappel de changement de phase du faux jeu."""
        self.fake.phase_callbacks[name]()

    def deny_writes(self) -> None:
        """Retire le droit d'ecriture apres coup, sans toucher a la lecture.

        Reproduit le cas ou le jeu accorde l'ecriture en phase de deploiement
        puis la retire une fois la bataille engagee — hypothese non infirmee,
        puisque le seul essai concluant s'est deroule avant l'engagement.
        """
        self.runtime.execute(
            "local real_open = io.open\n"
            "io.open = function(path, mode)\n"
            "  if mode == 'a' or mode == 'w' then\n"
            "    return nil, 'permission refusee (simulee)'\n"
            "  end\n"
            "  return real_open(path, mode)\n"
            "end\n"
        )

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
                {
                    key: entry[key]
                    for key in ("kind", "unit_id", "x", "z", "target_id", "forced")
                    if entry[key] is not None
                }
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


# --- robustesse a l'environnement restreint du jeu ---------------------------


def test_la_sonde_fonctionne_sans_math_huge(workdir: Path) -> None:
    """Regression du troisieme essai en bataille.

    Le jeu restreint la bibliotheque `math` : `math.huge` y vaut nil. La sonde
    bouclait alors sur `attempt to perform arithmetic on field 'huge'`, une fois
    par seconde, sans jamais publier le moindre etat.
    """
    probe = Probe(
        workdir,
        units=[("1001", "wh3_main_nur_inf_plaguebearers_1", 24.5, -303.1, True)],
        restricted_math=True,
    )
    probe.advance(3000)

    assert not probe.grep("ERREUR dans publish_state"), probe.grep("ERREUR")

    bridge = FileBridge.open(workdir)
    etats = bridge.read_states()
    assert etats, "aucun etat publie alors que math est restreint"
    assert etats[0].unit_id == "1001"
    assert etats[0].position.x == pytest.approx(24.5, abs=0.01)


def test_aller_retour_complet_sans_math_huge(workdir: Path) -> None:
    """L'aller-retour entier doit tenir dans l'environnement restreint du jeu."""
    probe = Probe(
        workdir,
        units=[("1001", "unite", 0.0, 0.0, True)],
        restricted_math=True,
    )
    probe.advance(2000)
    bridge = FileBridge.open(workdir)

    etat = bridge.read_states()[-1]
    commande = bridge.move_unit(etat.unit_id, Vector3(etat.position.x + 20.0, 0.0, etat.position.z))
    probe.advance(1000)

    acks = bridge.read_acks()
    assert acks and acks[0].sequence == commande.sequence
    assert acks[0].accepted
    # `ERREUR dans` prefixe les echecs de rappel ; le recensement, lui, journalise
    # volontairement `ERREUR` pour un accesseur qui leve.
    assert not probe.grep("ERREUR dans")


# --- perte du droit d'ecriture en cours de bataille --------------------------


def test_la_perte_du_droit_d_ecriture_est_signalee(probe: Probe) -> None:
    """Un refus d'ecriture apres le demarrage ne doit pas passer sous silence.

    Le droit d'ecriture n'a ete constate qu'en phase de deploiement. S'il
    disparaissait ensuite, la sonde cesserait de publier ; sans message, cela
    ressemblerait a une panne muette — exactement ce que le deuxieme essai en
    bataille a coute.
    """
    probe.advance(2000)
    probe.deny_writes()
    probe.advance(3000)

    assert probe.grep("ECRITURE REFUSEE")


def test_la_perte_du_droit_d_ecriture_ne_tue_pas_la_sonde(probe: Probe) -> None:
    """Le journal reste un canal de repli : les etats continuent d'y paraitre."""
    probe.advance(2000)
    probe.deny_writes()
    avant = len(probe.grep("STATE "))
    probe.advance(3000)

    assert len(probe.grep("STATE ")) > avant
    assert not probe.grep("ERREUR dans")


# --- plusieurs invocations successives du CLI --------------------------------
#
# Reproduit le defaut constate en bataille reelle : trois `probe --move 20`
# d'affilee publiaient tous `Ordre 1`, parce que chaque processus repartait de
# la sequence 1. Le Lua refusait a juste titre les deux derniers ; Python
# relisait le vieil accuse du premier et annoncait `accepted`. L'unite n'avait
# bouge qu'une fois.


def test_deux_ponts_successifs_ne_reutilisent_pas_la_sequence(probe: Probe, workdir: Path) -> None:
    """Un nouveau pont doit reprendre la numerotation, pas la recommencer."""
    probe.advance(2000)

    premier = FileBridge.open(workdir)
    etat = premier.wait_for_state(timeout=0, sleep=lambda _: None)
    assert etat is not None
    commande_1 = premier.move_unit(etat.unit_id, Vector3(etat.position.x + 20.0, 0.0, 0.0))
    probe.advance(1000)

    # Nouveau processus : le pont est rouvert a partir de zero.
    second = FileBridge.open(workdir)
    commande_2 = second.move_unit(etat.unit_id, Vector3(etat.position.x + 40.0, 0.0, 0.0))
    probe.advance(1000)

    assert commande_2.sequence > commande_1.sequence


def test_trois_deplacements_successifs_bougent_vraiment_l_unite(
    probe: Probe, workdir: Path
) -> None:
    """Chaque ordre doit produire un deplacement, pas seulement le premier."""
    probe.advance(2000)

    for _ in range(3):
        bridge = FileBridge.open(workdir)
        etat = bridge.wait_for_state(timeout=0, sleep=lambda _: None)
        assert etat is not None
        commande = bridge.move_unit(etat.unit_id, Vector3(etat.position.x + 20.0, 0.0, 0.0))
        probe.advance(1000)
        accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
        assert accuse is not None, f"aucun accuse pour la sequence {commande.sequence}"
        assert accuse.accepted, f"ordre {commande.sequence} refuse : {accuse.error}"
        probe.advance(6000)  # laisser le temps du deplacement et de la restitution

    deplacements = [order for order in probe.orders() if order["kind"] == "goto"]
    assert len(deplacements) == 3, f"seulement {len(deplacements)} deplacement(s) executes"


def test_un_vieil_accuse_ne_repond_pas_a_une_nouvelle_commande(probe: Probe, workdir: Path) -> None:
    """Un accuse anterieur a la commande ne doit jamais etre pris pour sa reponse."""
    probe.advance(2000)

    bridge = FileBridge.open(workdir)
    etat = bridge.wait_for_state(timeout=0, sleep=lambda _: None)
    assert etat is not None
    bridge.move_unit(etat.unit_id, Vector3(etat.position.x + 20.0, 0.0, 0.0))
    probe.advance(1000)

    # Une commande reutilisant de force un numero deja accuse : le Lua la
    # refuse en silence (deja traitee), donc aucun accuse ne doit remonter.
    rejoue = bridge.move_unit(etat.unit_id, Vector3(0.0, 0.0, 0.0), sequence=1)
    probe.advance(1000)

    assert bridge.wait_for_ack(rejoue.sequence, timeout=0, sleep=lambda _: None) is None


def test_le_cli_constate_le_deplacement_reel(probe: Probe, workdir: Path) -> None:
    """Le CLI doit mesurer le deplacement, pas se fier a l'accuse seul.

    Un accuse dit que l'ordre est parti. Vingt metres sur une carte de bataille
    ne se voient pas a l'oeil nu — sans mesure, on ne sait pas si l'unite a
    bouge.
    """
    from totalwar_ai.cli import _confirm_movement

    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    etat = bridge.wait_for_state(timeout=0, sleep=lambda _: None)
    assert etat is not None

    bridge.move_unit(etat.unit_id, Vector3(etat.position.x + 20.0, 0.0, etat.position.z))
    probe.advance(1000)  # le Lua execute l'ordre : l'unite se teleporte dans le faux jeu
    probe.advance(1000)  # un etat de plus, a la nouvelle position

    assert _confirm_movement(bridge, etat.unit_id, etat.position, timeout=1.0) == 0


def test_le_cli_signale_une_unite_qui_n_a_pas_bouge(probe: Probe, workdir: Path) -> None:
    """Un ordre accepte mais sans effet doit etre signale, pas passe sous silence."""
    from totalwar_ai.cli import _confirm_movement

    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    etat = bridge.wait_for_state(timeout=0, sleep=lambda _: None)
    assert etat is not None

    # Aucun ordre envoye : l'unite reste ou elle est.
    probe.advance(2000)
    assert _confirm_movement(bridge, etat.unit_id, etat.position, timeout=1.0) == 1


def test_le_cli_mesure_le_deplacement_total_pas_le_premier_pas(
    probe: Probe, workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rendre le premier mouvement detecte annoncerait 2,7 m pour 150 m parcourus.

    Defaut constate en bataille : l'unite avait bien fait ses 150 metres, mais
    le CLI s'arretait au premier etat depassant le seuil.
    """
    from totalwar_ai.cli import _confirm_movement

    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    etat = bridge.wait_for_state(timeout=0, sleep=lambda _: None)
    assert etat is not None

    bridge.move_unit(etat.unit_id, Vector3(etat.position.x + 150.0, 0.0, etat.position.z))
    probe.advance(1000)  # execution de l'ordre
    for _ in range(5):  # plusieurs etats a la position d'arrivee
        probe.advance(1000)

    assert _confirm_movement(bridge, etat.unit_id, etat.position, timeout=4.0) == 0
    sortie = capsys.readouterr().out
    assert "150.0 m" in sortie, sortie


# --- recensement des capacites -----------------------------------------------


def test_le_recensement_distingue_present_absent_et_en_erreur(probe: Probe) -> None:
    """Trois issues, trois messages : c'est tout l'interet du recensement.

    Le mod tiers etudie ne lit ni le moral ni la fatigue. Plutot que de
    supposer, la sonde demande au jeu et journalise ce qu'elle obtient.
    """
    probe.advance(1500)

    assert probe.grep("recensement des accesseurs")
    # Present et fonctionnel.
    assert probe.grep("number_of_men_alive : number 64")
    assert probe.grep("unary_hitpoints : number 0.800")
    assert probe.grep("is_routing : boolean false")
    # Present mais qui leve : distinct d'un accesseur absent.
    assert probe.grep("unary_morale : ERREUR")
    # Absent du faux jeu.
    assert probe.grep("ammo_left : ABSENT")
    assert probe.grep("fatigue : ABSENT")


def test_le_recensement_resume_les_accesseurs_utilisables(probe: Probe) -> None:
    probe.advance(1500)
    resume = probe.grep("accesseurs utilisables :")
    assert resume
    assert "number_of_men_alive" in resume[0]
    assert "unary_morale" not in resume[0]  # il a leve : inutilisable
    assert "ammo_left" not in resume[0]  # absent


def test_le_recensement_decrit_les_alliances(probe: Probe) -> None:
    probe.advance(1500)
    assert probe.grep("recensement des alliances")
    lignes = probe.grep("alliance 1 :")
    assert lignes
    assert "2 unite(s)" in lignes[0]


def test_le_recensement_ne_tue_pas_la_sonde(probe: Probe, workdir: Path) -> None:
    """Un accesseur qui leve ne doit pas empecher la publication d'etats."""
    probe.advance(3000)
    assert not probe.grep("ERREUR dans publish_state")
    assert FileBridge.open(workdir).read_states()


# --- ordres survivant a la bataille ------------------------------------------


def test_une_commande_d_une_partie_precedente_n_est_pas_executee(
    workdir: Path,
) -> None:
    """Un ordre ne survit pas a la bataille qui l'a recu.

    La memoire anti-rejeu du Lua vit en memoire : elle repart vide a chaque
    bataille. Un fichier de commande oublie sur le disque etait donc execute au
    demarrage de la bataille suivante — constate en jeu, un ordre d'une partie
    passee deplacant une unite d'une nouvelle partie.
    """
    # Une commande deposee AVANT que la sonde ne demarre : elle vient d'ailleurs.
    bridge = FileBridge.open(workdir)
    ancienne = bridge.move_unit("1006", Vector3(999.0, 0.0, 999.0))

    probe = Probe(workdir, units=[("1006", "unite", 0.0, 0.0, True)])
    probe.advance(3000)

    assert probe.grep("commande anterieure a cette bataille ignoree")
    assert not probe.orders(), probe.orders()
    assert not bridge.read_acks()
    assert ancienne.sequence == 1


def test_une_commande_posterieure_au_demarrage_est_bien_executee(
    probe: Probe, workdir: Path
) -> None:
    """Neutraliser l'ancienne commande ne doit pas bloquer les suivantes."""
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    etat = bridge.wait_for_state(timeout=0, sleep=lambda _: None)
    assert etat is not None

    commande = bridge.move_unit(etat.unit_id, Vector3(etat.position.x + 20.0, 0.0, 0.0))
    probe.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None and accuse.accepted


# --- phase de bataille -------------------------------------------------------


def test_la_phase_est_publiee_dans_l_etat(probe: Probe, workdir: Path) -> None:
    """Python doit distinguer « rien produit » de « bataille pas commencee »."""
    probe.advance(2000)
    bridge = FileBridge.open(workdir)

    etat = bridge.read_states()[-1]
    assert etat.phase == "unknown"
    assert etat.orders_take_effect  # dans le doute, on ne bloque pas

    probe.enter_phase("Deployment")
    probe.advance(2000)
    etat = bridge.read_states()[-1]
    assert etat.phase == "Deployment"
    assert not etat.orders_take_effect

    probe.enter_phase("Deployed")
    probe.advance(2000)
    etat = bridge.read_states()[-1]
    assert etat.phase == "Deployed"
    assert etat.orders_take_effect


# --- observation de la bataille entiere --------------------------------------


@pytest.fixture
def bataille(workdir: Path) -> Probe:
    """Deux camps, comme en jeu : onze contre onze."""
    return Probe(
        workdir,
        units=[
            ("1001", "wh3_dlc20_chs_cha_daemon_prince_mnur", 0.0, -330.0, True),
            ("1002", "wh3_main_nur_inf_plaguebearers_1", 20.0, -330.0, True),
            ("1003", "wh_main_emp_art_great_cannon", -20.0, -350.0, False),
        ],
        enemies=[
            ("2001", "wh_main_emp_inf_handgunners", 0.0, 330.0, False),
            ("2002", "wh_main_emp_cav_reiksguard", 40.0, 330.0, False),
        ],
    )


def test_la_bataille_entiere_est_publiee(bataille: Probe, workdir: Path) -> None:
    probe, bridge = bataille, FileBridge.open(workdir)
    probe.advance(2000)

    etat = bridge.latest_battle_state()
    assert etat is not None
    assert [unite.unit_id for unite in etat.allies] == ["1001", "1002", "1003"]
    assert [unite.unit_id for unite in etat.enemies] == ["2001", "2002"]


def test_les_champs_absents_du_jeu_valent_none(bataille: Probe, workdir: Path) -> None:
    """Un champ que le jeu n'expose pas doit manquer, pas valoir zero.

    Le recensement en bataille reelle a montre `unary_morale` et toutes les
    formes de fatigue absentes du bac a sable. Un moral a zero se confondrait
    avec une unite qui rompt.
    """
    bataille.advance(2000)
    etat = FileBridge.open(workdir).latest_battle_state()
    assert etat is not None

    unite = etat.allies[0]
    assert unite.hitpoints == pytest.approx(0.8)  # expose par le faux jeu
    assert unite.men_alive == 64
    assert unite.ammo is None  # absent du faux jeu, comme du vrai pour ce type
    assert unite.missile_range is None

    domaine = unite.to_unit_state(Side.ALLY)
    assert domaine.metadata["morale_available"] is False
    assert domaine.metadata["fatigue_available"] is False


def test_l_etat_se_traduit_pour_l_agent(bataille: Probe, workdir: Path) -> None:
    """Le raccord vers le domaine : c'est ce que l'agent consommera."""
    bataille.advance(2000)
    etat = FileBridge.open(workdir).latest_battle_state()
    assert etat is not None

    domaine = etat.to_battle_state()
    assert len(domaine.allies()) == 3
    assert len(domaine.enemies()) == 2
    assert domaine.unit("1001") is not None
    assert domaine.unit("2001") is not None

    # Le classifieur doit savoir quoi faire des cles reelles du jeu.
    classifier = UnitClassifier.from_config()
    roles = {unite.id: classifier.classify(unite) for unite in domaine.units}
    assert roles["1001"] is UnitRole.LORD
    assert roles["1003"] is UnitRole.ARTILLERY
    assert roles["2001"] is UnitRole.RANGED_INFANTRY
    assert roles["2002"] is UnitRole.SHOCK_CAVALRY


def test_une_unite_morte_est_ecartee(workdir: Path) -> None:
    """Garder les morts fausserait barycentres et rapports de force."""
    probe = Probe(
        workdir,
        units=[("1001", "unite", 0.0, 0.0, True), ("1002", "unite", 10.0, 0.0, True)],
    )
    probe.kill_unit("1002")
    probe.advance(2000)

    etat = FileBridge.open(workdir).latest_battle_state()
    assert etat is not None
    assert len(etat.allies) == 2  # publiees telles quelles
    assert [u.id for u in etat.to_battle_state().allies()] == ["1001"]  # filtrees ici


def test_une_unite_non_ciblable_n_est_pas_une_unite_morte(workdir: Path) -> None:
    """`is_valid_target` dit « peut-on lui tirer dessus », pas « est-elle en vie ».

    Trois unites de tir sont restees marquees mortes pendant six minutes de
    bataille reelle, avec soixante-huit hommes et un carquois plein. Elles n'ont
    jamais ete confiees a l'IA du jeu, et l'armee a attaque sans elles.
    """
    probe = Probe(
        workdir,
        units=[("1001", "unite", 0.0, 0.0, True), ("1002", "unite", 10.0, 0.0, True)],
    )
    probe.make_untargetable("1002")
    probe.advance(2000)

    etat = FileBridge.open(workdir).latest_battle_state()
    assert etat is not None
    vivantes = [unite.unit_id for unite in etat.allies if unite.alive]
    assert vivantes == ["1001", "1002"], "une unite bien vivante a ete comptee morte"
    assert [u.id for u in etat.to_battle_state().allies()] == ["1001", "1002"]


def test_le_flux_mixte_sert_les_deux_lecteurs(bataille: Probe, workdir: Path) -> None:
    """Un seul fichier, deux types de messages, aucun lecteur prive de l'autre."""
    bataille.advance(3000)
    bridge = FileBridge.open(workdir)

    batailles = bridge.read_battle_states()
    unites = bridge.read_states()
    assert batailles, "aucun etat de bataille"
    assert unites, "l'etat mono-unite a ete avale par l'autre lecteur"
    assert not bridge.malformed


def test_le_journal_n_est_pas_noye_par_les_etats(probe: Probe) -> None:
    """Tant que l'ecriture marche, le journal n'a pas a recevoir chaque etat.

    Constate en jeu : 297 Ko de journal en dix-sept minutes, ou `probe --log`
    n'affichait plus que des lignes identiques. Le canal de repli noyait le
    diagnostic qu'il est cense servir.
    """
    probe.advance(30_000)  # une trentaine d'etats publies
    assert len(probe.grep("STATE ")) < 10


def test_sans_ecriture_le_journal_recoit_tout(tmp_path: Path) -> None:
    """Quand le fichier est inaccessible, le journal redevient le seul canal."""
    # Pas de dossier d'echange : l'ecriture echoue des le depart.
    probe = Probe(tmp_path, units=[("1001", "unite", 0.0, 0.0, True)])
    probe.advance(30_000)
    assert len(probe.grep("STATE ")) > 20


# --- deplacement de groupe ---------------------------------------------------


def test_toute_l_armee_bouge_en_une_commande(bataille: Probe, workdir: Path) -> None:
    """Une armee se deploie d'un bloc, pas une unite par seconde."""
    probe, bridge = bataille, FileBridge.open(workdir)
    probe.advance(2000)

    commande = bridge.move_units(
        [
            ("1001", Vector3(0.0, 0.0, -300.0)),
            ("1002", Vector3(20.0, 0.0, -300.0)),
        ]
    )
    probe.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None and accuse.accepted

    deplacements = [order for order in probe.orders() if order["kind"] == "goto"]
    assert {order["unit_id"] for order in deplacements} == {"1001", "1002"}
    # Chaque unite garde sa destination : c'est une formation, pas un troupeau.
    par_unite = {order["unit_id"]: order for order in deplacements}
    assert par_unite["1001"]["x"] == pytest.approx(0.0)
    assert par_unite["1002"]["x"] == pytest.approx(20.0)
    assert par_unite["1001"]["z"] == pytest.approx(-300.0)


def test_une_unite_en_echec_n_annule_pas_les_autres(bataille: Probe, workdir: Path) -> None:
    """Dix-neuf unites ne doivent pas rester immobiles a cause d'une vingtieme."""
    probe, bridge = bataille, FileBridge.open(workdir)
    probe.advance(2000)

    commande = bridge.move_units(
        [
            ("1001", Vector3(0.0, 0.0, -300.0)),
            ("9999", Vector3(0.0, 0.0, -300.0)),  # n'existe pas
            ("1002", Vector3(20.0, 0.0, -300.0)),
        ]
    )
    probe.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None
    assert accuse.accepted
    # Un accuse « accepte » qui tairait l'echec serait un mensonge.
    assert accuse.detail is not None
    assert "2 ordre(s) lance(s), 1 refuse(s)" in str(accuse.detail)
    assert accuse.error is not None and "9999" in accuse.error

    bouges = {order["unit_id"] for order in probe.orders() if order["kind"] == "goto"}
    assert bouges == {"1001", "1002"}


def test_un_groupe_entierement_en_echec_est_refuse(bataille: Probe, workdir: Path) -> None:
    probe, bridge = bataille, FileBridge.open(workdir)
    probe.advance(2000)

    commande = bridge.move_units([("9998", Vector3(0.0, 0.0, 0.0))])
    probe.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None
    assert not accuse.accepted


def test_le_groupe_rend_le_controle_de_chaque_unite(bataille: Probe, workdir: Path) -> None:
    """La restitution vaut pour toutes les unites prises, pas seulement la premiere."""
    probe, bridge = bataille, FileBridge.open(workdir)
    probe.advance(2000)

    bridge.move_units(
        [("1001", Vector3(0.0, 0.0, -300.0)), ("1002", Vector3(20.0, 0.0, -300.0))],
        release_after_ms=1000,
    )
    probe.advance(3000)

    liberations = [order for order in probe.orders() if order["kind"] == "release"]
    assert len(liberations) >= 2
    assert len(probe.grep("controle rendu")) >= 2


def test_le_recensement_observe_aussi_une_unite_de_troupe(workdir: Path) -> None:
    """Recenser sur le seigneur seul ne dit rien des vraies unites.

    La premiere unite d'une armee est le seigneur : une figurine unique, ou
    `number_of_men_alive` vaut 1 et `unary_hitpoints` ne permet pas de savoir si
    ce nombre designe une fraction d'unite ou une sante individuelle. Il faut
    donc recenser aussi une unite de plusieurs hommes.
    """
    probe = Probe(
        workdir,
        units=[
            ("1001", "wh3_dlc20_chs_cha_daemon_prince_mnur", 0.0, 0.0, True),
            ("1002", "wh3_main_nur_inf_plaguebearers_1", 20.0, 0.0, True),
        ],
    )
    probe.set_men("1001", 1)
    probe.set_men("1002", 80)
    probe.advance(1500)

    assert probe.grep("premiere unite : wh3_dlc20_chs_cha_daemon_prince_mnur")
    assert probe.grep("unite de troupe : wh3_main_nur_inf_plaguebearers_1")
    assert probe.grep("number_of_men_alive : number 1")
    assert probe.grep("number_of_men_alive : number 80")


def test_une_armee_sans_troupe_le_signale(workdir: Path) -> None:
    """Ne pas taire un recensement partiel : il expliquerait un chiffre bizarre."""
    probe = Probe(workdir, units=[("1001", "seigneur", 0.0, 0.0, True)])
    probe.set_men("1001", 1)
    probe.advance(1500)

    assert probe.grep("aucune unite de plus d'une entite trouvee")


# --- delegation a l'IA du jeu -------------------------------------------------


def test_des_unites_sont_confiees_a_l_ia_du_jeu(probe: Probe, workdir: Path) -> None:
    """L'IA du moteur connait le terrain et les formations ; nous non.

    C'est ce qui rend la delegation utile — et ce qui la distingue de notre
    agent, qui explique ses decisions et peut etre regle.
    """
    probe.advance(2000)
    bridge = FileBridge.open(workdir)

    commande = bridge.delegate(["1006", "1005"])
    probe.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None and accuse.accepted
    assert probe.grep("confiee(s) a l'IA du jeu")
    assert [order for order in probe.orders() if order["kind"] == "delegate"]


def test_les_unites_confiees_sont_reprises_sur_demande(probe: Probe, workdir: Path) -> None:
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    bridge.delegate(["1006"])
    probe.advance(1000)

    commande = bridge.reclaim()
    probe.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None
    assert [order for order in probe.orders() if order["kind"] == "reclaim"]
    assert probe.grep("reprises a l'IA du jeu")


def test_la_sentinelle_d_arret_reprend_les_unites_confiees(probe: Probe, workdir: Path) -> None:
    """Le joueur doit pouvoir tout reprendre sans passer par Python.

    Une voie d'arret incapable de defaire la delegation laisserait les unites
    confiees a une IA que plus rien ne pilote.
    """
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    bridge.delegate(["1006", "1005"])
    probe.advance(1000)

    bridge.paths.stop.write_text("stop\n", encoding="utf-8")
    probe.advance(1000)

    assert [order for order in probe.orders() if order["kind"] == "reclaim"]
    assert probe.grep("SONDE ARRETEE")


def test_la_fin_de_bataille_reprend_les_unites_confiees(probe: Probe, workdir: Path) -> None:
    probe.advance(2000)
    FileBridge.open(workdir).delegate(["1006"])
    probe.advance(1000)

    probe.enter_phase("Complete")

    assert [order for order in probe.orders() if order["kind"] == "reclaim"]


def test_une_unite_sous_notre_controle_est_rendue_avant_d_etre_confiee(
    probe: Probe, workdir: Path
) -> None:
    """Deux pilotes sur une meme unite se disputeraient les ordres."""
    probe.advance(2000)
    bridge = FileBridge.open(workdir)
    bridge.move_unit("1006", Vector3(50.0, 0.0, -300.0))
    probe.advance(500)

    bridge.delegate(["1006"])
    probe.advance(1000)

    ordres = [order["kind"] for order in probe.orders()]
    assert "release" in ordres[: ordres.index("delegate")], (
        "l'unite n'a pas ete rendue avant d'etre confiee"
    )


def test_une_delegation_vide_est_refusee(probe: Probe, workdir: Path) -> None:
    """Confier une unite inexistante ne doit pas creer de planificateur."""
    probe.advance(2000)
    bridge = FileBridge.open(workdir)

    commande = bridge.delegate(["9999"])
    probe.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None and not accuse.accepted
    assert not [order for order in probe.orders() if order["kind"] == "delegate"]


def test_reprendre_sans_rien_avoir_confie_est_sans_effet(probe: Probe, workdir: Path) -> None:
    """La reprise doit pouvoir etre demandee par simple precaution."""
    probe.advance(2000)
    bridge = FileBridge.open(workdir)

    commande = bridge.reclaim()
    probe.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None
    assert not probe.grep("ERREUR dans")


# --- une alliance a plusieurs armees -------------------------------------------
#
# Le defaut le plus couteux du projet, et le plus discret : `alliance_snapshot`
# parcourait toutes les armees de l'alliance, `find_unit_by_id` une seule. La
# sonde publiait donc dix-huit unites dont douze n'etaient joignables par aucun
# ordre. Le faux jeu ne savait pas representer ce cas ; il le sait maintenant.


@pytest.fixture
def renforts(workdir: Path) -> Probe:
    """Notre alliance compte deux armees, comme une bataille avec renforts."""
    return Probe(
        workdir,
        units=[
            ("1001", "wh3_dlc20_chs_cha_daemon_prince_mnur", 0.0, -330.0, True),
            ("1002", "wh3_main_nur_inf_plaguebearers_1", 20.0, -330.0, True),
        ],
        reinforcements=[
            ("1007", "wh3_main_pro_ksl_inf_tzar_guard_0", 60.0, -330.0, True),
            ("1008", "wh3_main_pro_ksl_inf_tzar_guard_0", 80.0, -330.0, True),
        ],
        enemies=[("2001", "wh_main_emp_inf_handgunners", 0.0, 330.0, False)],
    )


def test_toute_unite_observee_peut_recevoir_un_ordre(renforts: Probe, workdir: Path) -> None:
    """Observer et commander doivent porter sur le meme ensemble d'unites.

    Sans cette egalite, l'agent raisonne sur une armee qu'il ne commande pas :
    il place ses tireurs, choisit ses cibles, et le jeu refuse chaque ordre par
    « unite introuvable » sans que le plan en soit averti.
    """
    renforts.advance(2000)
    renforts.enter_phase("Deployed")
    renforts.advance(2000)

    bridge = FileBridge.open(workdir)
    etat = bridge.latest_battle_state()
    assert etat is not None
    observees = [unite.unit_id for unite in etat.allies]
    assert set(observees) == {"1001", "1002", "1007", "1008"}

    commande = bridge.delegate(observees)
    renforts.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None and accuse.accepted
    assert accuse.refused_ids == (), "des unites publiees restent hors de portee des ordres"


def test_une_unite_d_une_autre_armee_se_deplace(renforts: Probe, workdir: Path) -> None:
    """La regression se voit sur le deplacement, pas seulement sur la delegation."""
    renforts.advance(2000)
    renforts.enter_phase("Deployed")
    renforts.advance(2000)

    bridge = FileBridge.open(workdir)
    commande = bridge.move_unit("1007", Vector3(180.0, 0.0, -330.0))
    renforts.advance(3000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None, "aucun accuse"
    assert accuse.accepted, f"ordre refuse : {accuse.error}"
    deplacements = [
        order
        for order in renforts.orders()
        if order["kind"] == "goto" and order["unit_id"] == "1007"
    ]
    assert deplacements, "l'unite du renfort n'a recu aucun ordre de deplacement"


def test_le_jeu_nomme_les_unites_qu_il_refuse(renforts: Probe, workdir: Path) -> None:
    """Un compte global ne suffit pas : Python doit savoir lesquelles ecarter.

    Un accuse peut etre **accepte et partiel**. Sans la liste, l'appelant garde
    les unites refusees dans son perimetre et leur adresse un ordre par seconde
    jusqu'a la fin de la bataille.
    """
    renforts.advance(2000)
    renforts.enter_phase("Deployed")
    renforts.advance(2000)

    bridge = FileBridge.open(workdir)
    commande = bridge.delegate(["1001", "9998", "1007", "9999"])
    renforts.advance(1000)

    accuse = bridge.wait_for_ack(commande.sequence, timeout=0, sleep=lambda _: None)
    assert accuse is not None and accuse.accepted, "les unites valides devaient etre confiees"
    assert set(accuse.refused_ids) == {"9998", "9999"}


# --- la sentinelle d'arret ne doit pas survivre a sa bataille -------------------
#
# Elle protege des unites. Celles de la bataille precedente n'existent plus, et
# la lire comme un ordre coupait la sonde quelques secondes apres le chargement,
# avant meme le deploiement. L'arret du Lua etant definitif, plus rien du cote
# Python ne pouvait rattraper la situation : il fallait relancer la bataille.


def test_une_sentinelle_heritee_ne_coupe_pas_la_bataille_suivante(workdir: Path) -> None:
    """Constate en bataille : `SONDE ARRETEE` a 26,7 s, avant le deploiement."""
    stop = workdir / "totalwar_ai" / "totalwar_ai_stop"
    stop.write_text("arret demande par l'operateur\n", encoding="utf-8")

    probe = Probe(workdir, units=[("1001", "wh_main_grn_inf_orc_boyz", 0.0, -330.0, True)])
    probe.advance(3000)

    assert probe.grep("levee"), "la sentinelle heritee n'a pas ete levee"
    assert not probe.grep("SONDE ARRETEE"), "la sonde s'est coupee sur une sentinelle perimee"

    bridge = FileBridge.open(workdir)
    assert bridge.latest_battle_state() is not None, "aucun etat publie"
    assert not bridge.stop_requested, "Python lit encore un arret la ou il n'y en a plus"


def test_un_arret_demande_pendant_la_bataille_coupe_bien_la_sonde(workdir: Path) -> None:
    """La correction ne doit pas desarmer le garde-fou qu'elle assouplit."""
    probe = Probe(workdir, units=[("1001", "wh_main_grn_inf_orc_boyz", 0.0, -330.0, True)])
    probe.advance(3000)
    assert not probe.grep("SONDE ARRETEE")

    bridge = FileBridge.open(workdir)
    bridge.abort("arret demande par l'operateur")
    assert bridge.stop_requested
    probe.advance(2000)

    assert probe.grep("SONDE ARRETEE"), "l'arret d'urgence n'a plus d'effet"


# --- recenser l'API du moteur avant de batir dessus -----------------------------
#
# Un plan entier de « profils d'agressivite » repose sur `rush_force`,
# `attack_force` et `set_should_reorder`, dont nous n'avons jamais constate
# l'existence. Le projet a deja paye trois fois le prix d'une API plausible mais
# fausse : `math.huge`, `unary_morale`, `number_of_men`.


def test_l_api_du_planificateur_est_recensee(probe: Probe) -> None:
    """Presente ou absente, chaque methode candidate doit etre nommee."""
    probe.advance(2000)

    recensement = probe.grep("recensement de script_ai_planner")
    assert recensement, "le planificateur n'a pas ete recense"
    assert probe.grep("  new : presente"), "la seule methode connue n'est pas vue"
    # Les methodes que le faux jeu ne definit pas doivent etre dites absentes,
    # jamais passees sous silence.
    assert probe.grep("  rush_force : ABSENT")
    assert probe.grep("  attack_force : ABSENT")


def test_le_recensement_ne_confie_aucune_unite(probe: Probe) -> None:
    """Un recensement qui delegue changerait la bataille qu'il observe."""
    probe.advance(2000)

    assert not [order for order in probe.orders() if order["kind"] == "delegate"]


def test_la_difficulte_de_bataille_est_relevee_des_deux_cotes(workdir: Path) -> None:
    """`army_handicap` decide si la difficulte change le planificateur.

    Absente du faux jeu : le recensement doit le dire au lieu de se taire, sans
    quoi son silence se lirait comme une reponse.
    """
    probe = Probe(
        workdir,
        units=[("1001", "wh_main_grn_inf_orc_boyz", 0.0, -100.0, True)],
        enemies=[("2001", "wh_main_emp_inf_swordsmen", 0.0, -40.0, False)],
    )
    probe.advance(2000)

    assert probe.grep("recensement de l'armee"), "l'armee n'a pas ete recensee"
    assert probe.grep("army_handicap"), "la difficulte de bataille n'est pas mentionnee"
    assert not probe.grep("ERREUR dans census_army_api")


# --- le terrain : poser la question au jeu --------------------------------------
#
# La fiche de faisabilite a longtemps porte « aucune donnee de terrain ». C'etait
# vrai des accesseurs d'unite recenses, et faux du reste : `position():get_y()`
# repond, et `v_to_ground` projette un point sur le sol. Deux voies jamais
# testees, et le recensement pose enfin la question.


def test_le_terrain_est_recense(probe: Probe) -> None:
    """Presente ou absente, la sonde d'altitude doit etre nommee."""
    probe.advance(2000)

    assert probe.grep("recensement du terrain"), "le terrain n'a pas ete recense"
    assert probe.grep("  v_to_ground : presente")
    # Une croix de cinq points : des altitudes qui different prouveraient que la
    # sonde lit le relief. Le faux jeu rend toujours zero — c'est le vrai qui
    # tranchera.
    assert len(probe.grep("  sol en (")) == 5


def test_le_recensement_du_terrain_ne_deplace_rien(probe: Probe) -> None:
    """Sonder le sol ne doit pas donner d'ordre : `v_to_ground` sert aux deux."""
    probe.advance(2000)

    assert not probe.orders(), "le recensement a produit un ordre"


def test_la_sonde_publie_deux_etats_par_seconde(probe: Probe) -> None:
    """Observer plus finement que l'on ne decide rend l'inference plus sure.

    La boucle Python decide une fois par seconde ; le jeu publie deux fois. Un
    etat sur deux serait perdu si la boucle n'en gardait qu'un, ce qui etait le
    cas avant que `LiveStep.observed` n'existe.
    """
    # La sonde ne demarre qu'a t=1000 ms : elle vit donc deux secondes ici.
    probe.advance(3000)
    bridge = FileBridge.open(probe.workdir)

    etats = bridge.read_battle_states()
    assert len(etats) >= 4, f"{len(etats)} etat(s) en deux secondes, deux par seconde attendus"
    ecarts = [
        seconde.game_time_ms - premiere.game_time_ms
        for premiere, seconde in itertools.pairwise(etats)
    ]
    assert max(ecarts) <= 500, f"ecarts de publication : {ecarts}"
