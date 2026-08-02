"""Boucle de pilotage complete, executee contre le vrai script Lua.

C'est le raccord des quatre morceaux : observation de la bataille, memoire des
effectifs, agent tactique, commande de groupe. Les tests l'exercent de bout en
bout — le Lua publie, Python decide, le Lua execute — et verifient surtout ce
que la boucle **refuse** de faire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("lupa", reason="lupa n'est pas installe")

from tests.integration.test_lua_probe_execution import Probe

from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.bridge.command_models import ProbeAttack, ProbeOrdersCommand, decode_command
from totalwar_ai.bridge.file_bridge import FileBridge
from totalwar_ai.bridge.live import LiveSession, LiveStep
from totalwar_ai.bridge.orders import Translation
from totalwar_ai.config import load_config
from totalwar_ai.domain.actions import ActionType
from totalwar_ai.domain.geometry import Vector3

#: Une armee plausible : un seigneur, deux lignes, des tireurs, de l'artillerie.
ARMEE = [
    ("1001", "wh3_dlc20_chs_cha_daemon_prince_mnur", 0.0, -330.0, True),
    ("1002", "wh3_main_nur_inf_plaguebearers_1", -40.0, -330.0, True),
    ("1003", "wh3_main_nur_inf_plaguebearers_1", 40.0, -330.0, True),
    ("1004", "wh_main_emp_inf_handgunners", 0.0, -360.0, True),
    ("1005", "wh_main_emp_art_great_cannon", -60.0, -380.0, True),
]

ENNEMIS = [
    ("2001", "wh_main_emp_inf_spearmen", 0.0, 200.0, False),
    ("2002", "wh_main_emp_cav_reiksguard", 60.0, 200.0, False),
    ("2003", "wh_main_emp_inf_handgunners", -60.0, 230.0, False),
]


@pytest.fixture
def bataille(tmp_path: Path) -> Probe:
    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)
    return probe


@pytest.fixture
def session(bataille: Probe, tmp_path: Path) -> LiveSession:
    return LiveSession(
        bridge=FileBridge.open(tmp_path),
        agent=DeterministicTacticalAgent.from_config(load_config()),
    )


# --- le tour nominal ---------------------------------------------------------


def test_la_boucle_met_l_armee_en_mouvement(session: LiveSession, bataille: Probe) -> None:
    """Le jalon vise : l'agent prend l'armee et lui donne des destinations."""
    etape = session.step()

    assert etape.state is not None
    assert etape.acted, etape.summary()
    bataille.advance(1000)

    deplacements = [order for order in bataille.orders() if order["kind"] == "goto"]
    assert deplacements, "aucun ordre n'est arrive jusqu'au jeu"
    assert len(deplacements) == etape.sent


def test_chaque_unite_recoit_sa_propre_destination(session: LiveSession, bataille: Probe) -> None:
    """Un meme point pour tout le monde produirait un tas, pas une formation."""
    etape = session.step()
    assert etape.acted, etape.summary()

    points = {(round(x, 1), round(z, 1)) for _, (x, _, z) in _destinations(etape)}
    assert len(points) == etape.sent, "des unites partagent la meme destination"


def test_l_agent_reste_sobre_quand_l_ennemi_est_loin(session: LiveSession, bataille: Probe) -> None:
    """Cinq cents metres separent les armees : il n'y a rien a faire.

    Un agent qui donnerait des ordres a chaque tour dans cette situation
    consommerait son budget d'ordres et agiterait l'armee sans raison. Le
    silence est ici la bonne reponse.
    """
    session.step()  # premier tour : mise en place
    for _ in range(4):
        bataille.advance(6000)
        assert not session.step().acted


def test_une_unite_ne_recoit_qu_un_ordre(session: LiveSession) -> None:
    """Deux ordres contradictoires pour une meme unite s'annuleraient en jeu."""
    etape = session.step()
    identifiants = [unit_id for unit_id, _ in etape.translation.moves]
    assert len(identifiants) == len(set(identifiants))


def test_la_boucle_explique_ce_qu_elle_fait(session: LiveSession) -> None:
    """L'explicabilite doit survivre au passage dans le jeu."""
    etape = session.step()
    assert etape.decisions
    assert any("Action" in ligne for ligne in etape.decisions)
    assert "deplacement(s)" in etape.summary()


# --- ce que la boucle refuse de faire ----------------------------------------


def test_rien_n_est_emis_avant_le_debut_de_la_bataille(tmp_path: Path) -> None:
    """Un ordre donne en deploiement est acquitte mais ne produit rien.

    Constate en jeu : unite immobile trente-trois secondes durant apres un ordre
    accepte. Emettre quand meme donnerait l'illusion d'un agent qui agit.
    """
    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployment")
    probe.advance(2000)

    session = LiveSession(
        bridge=FileBridge.open(tmp_path),
        agent=DeterministicTacticalAgent.from_config(load_config()),
    )
    etape = session.step()

    assert not etape.acted
    assert etape.skipped is not None and "Deployment" in etape.skipped
    probe.advance(1000)
    assert not [order for order in probe.orders() if order["kind"] == "goto"]


def test_l_arret_d_urgence_coupe_tout(session: LiveSession, bataille: Probe) -> None:
    session.stop()
    bataille.advance(1000)

    etape = session.step()
    assert not etape.acted
    assert etape.skipped is not None
    assert bataille.grep("SONDE ARRETEE") or bataille.grep("arret")


def test_une_sentinelle_posee_a_la_main_arrete_la_boucle(
    session: LiveSession, tmp_path: Path
) -> None:
    """Le joueur doit pouvoir tout stopper sans passer par Python."""
    (tmp_path / "totalwar_ai" / "totalwar_ai_stop").write_text("stop\n", encoding="utf-8")

    etape = session.step()
    assert not etape.acted
    assert etape.skipped == "arret d'urgence demande"


def test_sans_etat_la_boucle_ne_fait_rien(tmp_path: Path) -> None:
    """Aucun etat ne doit pas devenir une decision prise dans le vide."""
    (tmp_path / "totalwar_ai").mkdir()
    session = LiveSession(
        bridge=FileBridge.open(tmp_path),
        agent=DeterministicTacticalAgent.from_config(load_config()),
    )
    etape = session.step()

    assert etape.state is None
    assert not etape.acted
    assert etape.summary() == "aucun etat recu du jeu"


# --- fidelite de l'observation ------------------------------------------------


def test_les_deux_camps_arrivent_jusqu_a_l_agent(session: LiveSession) -> None:
    etape = session.step()
    assert etape.state is not None
    assert len(etape.state.allies) == len(ARMEE)
    assert len(etape.state.enemies) == len(ENNEMIS)


def test_le_seigneur_est_reconnu_comme_tel(session: LiveSession) -> None:
    """`is_commanding_unit()` doit traverser tout le pont jusqu'a l'agent."""
    etape = session.step()
    assert etape.state is not None
    commandants = [unite for unite in etape.state.allies if unite.commanding]
    assert [unite.unit_id for unite in commandants] == ["1001"]


def _destinations(etape: LiveStep) -> list[tuple[str, tuple[float, float, float]]]:
    return [(unit_id, (point.x, point.y, point.z)) for unit_id, point in etape.translation.moves]


# --- engagement ---------------------------------------------------------------


def test_l_agent_engage_reellement_l_ennemi(tmp_path: Path) -> None:
    """Le manque revele en bataille : l'agent decidait d'attaquer, sans effet.

    Releve en jeu : « aucun ordre traduisible (ATTACK_TARGET) » a la plupart des
    tours. L'agent voyait juste et ne pouvait rien faire.
    """
    (tmp_path / "totalwar_ai").mkdir()
    # Les armees au contact : l'agent doit vouloir engager.
    proches = [(unit_id, cle, x, -20.0, True) for unit_id, cle, x, _, _ in ARMEE]
    face = [(unit_id, cle, x, 20.0, False) for unit_id, cle, x, _, _ in ENNEMIS]

    probe = Probe(tmp_path, units=proches, enemies=face)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    session = LiveSession(
        bridge=FileBridge.open(tmp_path),
        agent=DeterministicTacticalAgent.from_config(load_config()),
    )
    for _ in range(6):
        etape = session.step()
        probe.advance(2000)
        if etape.translation.attacks:
            break
    else:  # pragma: no cover - filet, l'agent doit engager a cette distance
        pytest.fail("l'agent n'a jamais engage a vingt metres de l'ennemi")

    attaques = [order for order in probe.orders() if order["kind"] == "attack"]
    assert attaques, "aucun ordre d'attaque n'est arrive jusqu'au jeu"
    assert all(order["target_id"] for order in attaques)


def test_deplacements_et_attaques_partent_ensemble(tmp_path: Path) -> None:
    """Deux commandes successives se perdraient : le fichier est remplace.

    Le Lua ne relit le fichier de commande que toutes les 500 ms. Publier les
    deplacements puis les attaques ferait disparaitre les premiers en silence.
    """

    (tmp_path / "totalwar_ai").mkdir()
    bridge = FileBridge.open(tmp_path)
    bridge.send_orders(
        moves=[
            ("1001", __import__("totalwar_ai.domain.geometry", fromlist=["V"]).Vector3(1, 0, 2))
        ],
        attacks=[ProbeAttack(unit_id="1002", target_id="2001", melee=True)],
    )

    publiee = decode_command(json.loads(bridge.paths.command.read_text(encoding="utf-8")))
    assert isinstance(publiee, ProbeOrdersCommand)
    assert publiee.order_count == 2


def test_les_actions_perdues_se_disent_meme_quand_d_autres_partent(
    session: LiveSession,
) -> None:
    """Constate en bataille : deux deplacements masquaient trois manoeuvres perdues.

    Le resume ne montrait les actions non traduites que si *aucun* ordre
    n'etait parti. Un tour ou l'agent perd la moitie de ses intentions
    paraissait alors parfaitement normal.
    """
    from totalwar_ai.bridge.live import LiveStep

    etape = LiveStep(
        state=session.bridge.latest_battle_state(),
        translation=Translation(
            moves=(("1001", Vector3(0.0, 0.0, 0.0)),),
            untranslated=((ActionType.REORIENT_FRONT, "peu importe"),),
        ),
        sent=1,
    )

    resume = etape.summary()
    assert "1 deplacement(s)" in resume
    assert "1 action(s) perdue(s)" in resume
    assert "REORIENT_FRONT" in resume


def test_tenir_la_ligne_arrete_reellement_les_unites(tmp_path: Path) -> None:
    """Bout en bout : « tenir la position » doit produire un arret dans le jeu.

    Constate en bataille : cent quatorze tours, deux ordres. Cinq « tenir la
    position » par tour ne traversaient pas le pont, et l'armee continuait sur
    sa lancee pendant que l'agent croyait tenir.
    """
    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    bridge = FileBridge.open(tmp_path)
    # On met l'armee en mouvement, comme le ferait le joueur.
    etat = bridge.latest_battle_state()
    assert etat is not None
    bridge.send_orders(moves=[(unite.unit_id, Vector3(0.0, 0.0, -200.0)) for unite in etat.allies])
    probe.advance(2000)

    session = LiveSession(
        bridge=FileBridge.open(tmp_path),
        agent=DeterministicTacticalAgent.from_config(load_config()),
    )
    for _ in range(4):
        etape = session.step()
        probe.advance(2000)
        if etape.translation.halts:
            break
    else:  # pragma: no cover - filet
        pytest.fail("aucun arret emis alors que l'armee est en mouvement")

    arrets = [order for order in probe.orders() if order["kind"] == "halt"]
    assert arrets, "aucun ordre d'arret n'est arrive jusqu'au jeu"
