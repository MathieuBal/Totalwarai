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


@pytest.mark.parametrize("phase", ["unknown", "Deployment", "Complete"])
def test_seule_la_phase_deployed_autorise_le_pilotage(tmp_path: Path, phase: str) -> None:
    """Le meme refus, sur le chemin que le pilotage emprunte reellement.

    Un test de phase existait deja, mais il interrogeait
    `ProbeUnitState.orders_take_effect` — une classe que `LiveSession` n'utilise
    jamais. Pendant ce temps `ProbeBattleState`, la seule que la boucle consulte,
    acceptait encore `unknown`. Le test etait vert et le defaut intact.

    Celui-ci part de la sonde et va jusqu'aux ordres arrives dans le jeu : aucune
    classe intermediaire ne peut le satisfaire a la place de la bonne.

    Deux phases manquent a cette liste, faute d'etre atteignables ici. La chaine
    vide est couverte par `test_la_chaine_vide_n_est_pas_une_phase_jouable` : la
    revision 16 publie toujours le champ, donc seule une regression de protocole
    la produirait. `VictoryCountdown` n'est pas dans les `PHASES` de la sonde
    (`totalwar_ai_probe.lua:95`) : le jeu l'annonce dans son propre journal, mais
    la sonde reste sur `Deployed` — et pendant le decompte, un ordre prend
    effectivement encore effet.
    """
    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    if phase != "unknown":
        probe.enter_phase(phase)
    probe.advance(2000)

    session = LiveSession(
        bridge=FileBridge.open(tmp_path),
        agent=DeterministicTacticalAgent.from_config(load_config()),
    )
    etape = session.step()

    assert etape.state is not None, "la sonde n'a rien publie"
    assert etape.state.phase == phase
    assert not etape.acted
    assert etape.skipped is not None and phase in etape.skipped
    assert etape.sent == 0
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


def test_les_refus_de_securite_ne_passent_pas_pour_des_ordres(session: LiveSession) -> None:
    """Constate en bataille : neuf lignes affichees, deux ordres partis.

    `explanations()` reunit les decisions retenues et celles que les regles de
    securite ont refusees. Les imprimer ensemble laissait croire que l'agent
    avait agi neuf fois, alors que sept de ses intentions avaient ete bloquees.
    """
    etape = session.step()

    # Les deux listes sont disjointes, et seules les retenues sont traduites.
    assert not (set(etape.decisions) & set(etape.blocked))
    assert etape.sent <= len(etape.decisions)


def test_un_refus_de_securite_apparait_dans_le_resume(session: LiveSession) -> None:
    """Un refus est une decision, pas un silence : il doit se voir."""
    etape = LiveStep(
        state=session.bridge.latest_battle_state(),
        blocked=("Action : charger (1002) | Cause : ...",),
    )
    assert "1 refusee(s) par la securite" in etape.summary()


# --- enregistrement -----------------------------------------------------------


def test_une_bataille_pilotee_est_enregistree(
    session: LiveSession, bataille: Probe, tmp_path: Path
) -> None:
    """Le but : comparer un jour le simulateur au jeu sur les memes mesures.

    Deux corrections mesurees comme benefiques en jeu se sont revelees nuisibles
    au banc, sans qu'on puisse dire lequel des deux juges dit vrai. Departager
    demande des batailles reelles enregistrees dans le meme format.
    """
    from totalwar_ai.bridge.recording import LIVE_SCENARIO, BattleRecorder
    from totalwar_ai.memory.repository import MemoryRepository

    enregistrement = tmp_path / "enregistrement"
    recorder = BattleRecorder(directory=enregistrement)
    for _ in range(3):
        recorder.observe(session.step())
        bataille.advance(2000)  # le jeu publie un nouvel etat
    recorder.close()

    assert recorder.turns == 3
    assert recorder.path is not None
    # L'inventaire des unites occupe ses propres lignes : on ne compte que les
    # tours.
    lignes = [json.loads(ligne) for ligne in recorder.path.read_text(encoding="utf-8").splitlines()]
    # Le jeu publie plus souvent que la boucle ne decide : on compte les tours
    # de decision, et l'on verifie au passage qu'aucun etat n'a ete jete.
    tours = [ligne for ligne in lignes if ligne.get("decision")]
    assert len(tours) == 3
    # L'en-tete de format et les inventaires occupent leurs propres lignes.
    observations = [ligne for ligne in lignes if "turn" in ligne and "roster" not in ligne]
    assert len(observations) >= len(tours)
    assert recorder.observations == len(observations)

    premier = tours[0]
    assert premier["allies"] == len(ARMEE)
    assert premier["enemies"] == len(ENNEMIS)
    assert premier["phase"] == "Deployed"
    assert premier["decisions"], "les decisions de l'agent ne sont pas conservees"

    repository = MemoryRepository(tmp_path / "memoire.sqlite3")
    repository.save_episode(recorder.episode())
    assert repository.list_battles(scenario=LIVE_SCENARIO)


def test_les_ordres_reellement_emis_sont_traces(session: LiveSession, tmp_path: Path) -> None:
    """Un enregistrement qui perdrait les ordres ne servirait a rien."""
    from totalwar_ai.bridge.recording import BattleRecorder

    recorder = BattleRecorder(directory=tmp_path)
    etape = session.step()
    recorder.observe(etape)
    recorder.close()

    entree = next(item for item in recorder.entries if item.get("decision"))
    ordres = entree["orders"]
    total = len(ordres["moves"]) + len(ordres["attacks"]) + len(ordres["halts"])
    assert total == etape.sent


# --- posture imposee par l'operateur ------------------------------------------


def test_une_posture_imposee_fait_avancer_l_armee(tmp_path: Path) -> None:
    """En escarmouche l'adversaire attend : sans ordre, rien ne se passe.

    Deux tentatives pour faire *decider* a l'agent de rompre l'impasse ont ete
    mesurees nuisibles au banc (ADR 0005). Celle-ci n'est pas une decision de
    l'agent : c'est un ordre que l'operateur lui donne, pour qu'une bataille ait
    lieu et puisse etre observee.
    """
    from totalwar_ai.agent.planner import Posture

    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    agent = DeterministicTacticalAgent.from_config(load_config())
    agent.planner.forced_posture = Posture.ADVANCE
    session = LiveSession(bridge=FileBridge.open(tmp_path), agent=agent)

    etape = session.step()
    assert agent.plan is not None
    assert agent.plan.posture is Posture.ADVANCE
    assert "imposee par l'operateur" in agent.plan.rationale
    assert etape.acted, etape.summary()


def test_la_posture_imposee_survit_au_rechargement_de_doctrine() -> None:
    """La perdre rendrait l'ordre de l'operateur silencieusement caduc."""
    from totalwar_ai.agent.planner import Posture
    from totalwar_ai.learning.adaptation import DoctrineProfile

    agent = DeterministicTacticalAgent.from_config(load_config())
    agent.planner.forced_posture = Posture.ENVELOP
    agent.apply_doctrine(
        DoctrineProfile(fingerprint="t", adjustments={"line_spacing": 50.0}, rationale="essai")
    )

    assert agent.planner.forced_posture is Posture.ENVELOP


# --- supervision de l'IA du jeu -----------------------------------------------


def test_la_supervision_confie_l_armee_puis_la_surveille(tmp_path: Path) -> None:
    """L'IA du jeu mene ; nous ne reprenons que ce dont elle fait mauvais usage."""
    from totalwar_ai.bridge.live import SupervisedSession

    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    # La delegation attend l'accuse du jeu : la fausse sonde doit donc tourner
    # pendant l'attente, comme le vrai jeu le ferait.
    session = SupervisedSession(bridge=FileBridge.open(tmp_path), wait=lambda _: probe.advance(600))
    etat = session.bridge.latest_battle_state()
    assert etat is not None

    confiees = session.delegate_all(etat)
    probe.advance(1000)
    assert len(confiees) == len(ARMEE)
    assert [order for order in probe.orders() if order["kind"] == "delegate"]

    # Rien d'anormal : l'IA du jeu garde la main.
    etape = session.step()
    assert not etape.interventions
    assert not etape.returned


def test_l_arret_d_urgence_rend_tout_meme_en_supervision(tmp_path: Path) -> None:
    from totalwar_ai.bridge.live import SupervisedSession

    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    session = SupervisedSession(bridge=FileBridge.open(tmp_path), wait=lambda _: probe.advance(600))
    etat = session.bridge.latest_battle_state()
    assert etat is not None
    session.delegate_all(etat)
    probe.advance(1000)

    session.stop()
    probe.advance(1000)

    assert [order for order in probe.orders() if order["kind"] == "reclaim"]
    assert probe.grep("SONDE ARRETEE")
    assert not session.delegated


# --- la boucle fermee : ce que le jeu refuse ne compte pas comme fait -----------


def test_une_reprise_refusee_par_le_jeu_n_est_pas_comptee(tmp_path: Path) -> None:
    """Sans lecture de l'accuse, la supervision croit avoir agi et recommence.

    Constate en bataille : vingt-trois interventions, aucune appliquee, la meme
    unite reprise quatre fois. L'unite refusee doit sortir du perimetre — la
    reprendre ne la ramenera pas, et insister produit un ordre refuse par
    seconde jusqu'a la fin de la bataille.
    """
    from totalwar_ai.bridge.live import SupervisedSession

    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    # Le seigneur a l'agonie : la regle du seigneur en danger va se declencher.
    probe.runtime.execute(
        "local units = bm:alliances():item(1):armies():item(1):units()\n"
        "for i = 1, units:count() do\n"
        "  local u = units:item(i)\n"
        "  if tostring(u:unique_ui_id()) == '1001' then u.men_alive = 10 end\n"
        "end\n"
    )
    probe.advance(2000)

    session = SupervisedSession(bridge=FileBridge.open(tmp_path), wait=lambda _: probe.advance(600))

    # Le perimetre annonce le seigneur, mais rien n'a jamais ete confie au jeu :
    # c'est exactement l'ecart observe en bataille entre ce que Python croyait
    # tenir et ce que le Lua tenait reellement.
    session.delegated = {"1001"}

    premier = session.step()
    assert premier.state is not None, "aucun etat lu"
    probe.advance(1000)
    assert [item for item in premier.refused if item[0] == "1001"], (
        "le refus du jeu n'a pas ete remonte"
    )
    assert "1001" in session.unreachable, "l'unite refusee reste dans le perimetre"
    assert "1001" not in session.delegated

    # Le tour suivant ne doit plus rien tenter sur elle.
    probe.advance(1000)
    second = session.step()
    assert not second.refused
    assert not second.interventions


def test_le_pont_ignore_les_etats_de_la_session_precedente(tmp_path: Path) -> None:
    """Piloter sur une archive fait annoncer une armee que l'on ne tient plus.

    Constate en bataille : apres un Ctrl+C, deux sessions ont annonce dix-huit
    unites confiees alors que la sonde etait arretee depuis plusieurs minutes,
    et n'ont recu aucun etat pendant cinq minutes.
    """
    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    # Une session qui relit l'archive y trouve bien un etat.
    assert FileBridge.open(tmp_path).latest_battle_state() is not None

    # Une session de pilotage, elle, n'accepte que ce qui vient apres son ouverture.
    pilote = FileBridge.open(tmp_path).tail()
    assert pilote.latest_battle_state() is None, "un etat perime a ete pris pour l'etat courant"

    probe.advance(2000)
    assert pilote.latest_battle_state() is not None, "les etats suivants doivent arriver"


def test_le_mode_de_reference_confie_tout_et_n_intervient_jamais(tmp_path: Path) -> None:
    """La mesure de reference ne vaut que si rien ne contrarie l'IA du jeu.

    Une seule regle qui se declencherait suffirait a fausser la comparaison
    entre « l'IA du jeu seule » et « l'IA du jeu supervisee » : on ne mesurerait
    plus la difference entre les deux, mais deux supervisions differentes.
    """
    from totalwar_ai.bridge.live import SupervisedSession
    from totalwar_ai.bridge.supervision import Supervisor

    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    # Un seigneur a l'agonie : la supervision le reprendrait, la reference non.
    probe.runtime.execute(
        "local units = bm:alliances():item(1):armies():item(1):units()\n"
        "for i = 1, units:count() do\n"
        "  local u = units:item(i)\n"
        "  if tostring(u:unique_ui_id()) == '1001' then u.men_alive = 10 end\n"
        "end\n"
    )
    probe.advance(2000)

    session = SupervisedSession(
        bridge=FileBridge.open(tmp_path).tail(),
        supervisor=Supervisor(rules=()),
        wait=lambda _: probe.advance(600),
    )
    probe.advance(1200)
    etat = session.bridge.latest_battle_state()
    assert etat is not None
    assert len(session.delegate_all(etat)) == len(ARMEE)

    for _ in range(3):
        probe.advance(1200)
        etape = session.step()
        assert not etape.interventions, "une reprise a eu lieu sans aucune regle active"
        assert not etape.returned


def test_la_fin_de_bataille_est_publiee_et_enregistree(tmp_path: Path) -> None:
    """Sans état final, deux batailles enregistrées ne sont pas comparables.

    La sonde s'arrête à la phase `Complete` et retire son publieur d'états. Si
    elle ne publie rien avant, Python ne voit jamais la fin : l'issue reste
    `unknown`, et la boucle de pilotage tourne jusqu'à son minuteur sur une
    bataille déjà terminée.
    """
    from totalwar_ai.bridge.recording import BattleRecorder
    from totalwar_ai.domain.battle_state import BattleOutcomeKind

    (tmp_path / "totalwar_ai").mkdir()
    probe = Probe(tmp_path, units=ARMEE, enemies=ENNEMIS)
    probe.advance(2000)
    probe.enter_phase("Deployed")
    probe.advance(2000)

    bridge = FileBridge.open(tmp_path).tail()
    probe.advance(1200)
    recorder = BattleRecorder(directory=None)
    from totalwar_ai.bridge.live import LiveStep

    en_cours = bridge.latest_battle_state()
    assert en_cours is not None
    recorder.observe(LiveStep(state=en_cours))
    assert recorder.outcome is BattleOutcomeKind.UNKNOWN

    probe.enter_phase("Complete")

    final = bridge.latest_battle_state()
    assert final is not None, "aucun etat publie a la fin de la bataille"
    assert final.phase == "Complete"

    recorder.observe(LiveStep(state=final))
    assert recorder.outcome is not BattleOutcomeKind.UNKNOWN, "l'issue reste inconnue"


def test_la_boucle_rend_compte_de_tous_les_etats_publies(
    session: LiveSession, bataille: Probe
) -> None:
    """Le jeu publie plus souvent que la boucle ne decide.

    `latest_battle_state()` vidait le flux et ne rendait que le dernier : les
    autres etaient jetes sans trace. En supervision, l'attente d'un accuse peut
    durer deux secondes — autant d'etats perdus, et le corpus d'apprentissage
    ampute d'autant.
    """
    session.step()  # premier tour : on part d'un flux vide
    bataille.advance(3000)  # le jeu publie trois etats de plus

    etape = session.step()

    assert etape.state is not None
    assert len(etape.observed) >= 3, f"{len(etape.observed)} etat(s) retenu(s) sur trois publies"
    assert etape.observed[-1] is etape.state, "on decide bien sur le plus recent"
    sequences = [state.sequence for state in etape.observed]
    assert sequences == sorted(sequences), "les etats ne sont pas dans l'ordre"


# --- la decision fantome ------------------------------------------------------
#
# L'IA du moteur mene la bataille ; notre agent decide en parallele, dans le
# vide. Chaque tour devient un couple etiquete « elle a fait ceci, nous aurions
# fait cela » — la matiere premiere de l'apprentissage par observation, obtenue
# sans jouer une bataille de plus.


def _session_observante(tmp_path: Path, probe: Probe) -> object:
    from totalwar_ai.bridge.live import SupervisedSession
    from totalwar_ai.bridge.supervision import DEFAULT_RULES, Supervisor

    return SupervisedSession(
        bridge=FileBridge.open(tmp_path).tail(),
        supervisor=Supervisor(rules=()),  # observation pure : aucune regle n'agit
        shadow_agent=DeterministicTacticalAgent.from_config(load_config()),
        shadow_rules=Supervisor(rules=DEFAULT_RULES),
        wait=lambda _: probe.advance(600),
    )


def test_l_agent_decide_dans_le_vide_pendant_l_observation(bataille: Probe, tmp_path: Path) -> None:
    session = _session_observante(tmp_path, bataille)
    bataille.advance(1200)

    etape = session.step()  # type: ignore[attr-defined]

    assert etape.shadow is not None, "aucune decision fantome enregistree"
    assert etape.shadow.decisions, "l'agent n'a rien decide"
    assert etape.shadow.translation.moves or etape.shadow.translation.attacks


def test_l_observation_n_envoie_rien_au_jeu(bataille: Probe, tmp_path: Path) -> None:
    """Contrainte absolue : `--observe` observe, il ne joue pas.

    La garantie tient par construction — ni l'agent ni la traduction ne touchent
    au pont — mais elle est trop importante pour n'etre garantie que par
    lecture du code.
    """
    session = _session_observante(tmp_path, bataille)

    for _ in range(4):
        bataille.advance(1200)
        session.step()  # type: ignore[attr-defined]
    bataille.advance(2000)

    manoeuvres = [
        order for order in bataille.orders() if order["kind"] in {"goto", "attack", "halt"}
    ]
    assert not manoeuvres, f"l'observation a donne des ordres : {manoeuvres}"


def test_les_regles_qui_se_seraient_declenchees_sont_comptees(
    bataille: Probe, tmp_path: Path
) -> None:
    """`artillerie_au_contact` n'a jamais rien declenche au banc.

    Le fantome dira si c'est le banc qui manque de cas, ou la regle qui ne sert
    a rien — sans qu'il faille jouer une bataille de plus pour le savoir.
    """
    bataille.runtime.execute(
        "local units = bm:alliances():item(1):armies():item(1):units()\n"
        "for i = 1, units:count() do\n"
        "  local u = units:item(i)\n"
        "  if tostring(u:unique_ui_id()) == '1001' then u.men_alive = 10 end\n"
        "end\n"
    )
    session = _session_observante(tmp_path, bataille)
    bataille.advance(1200)

    etape = session.step()  # type: ignore[attr-defined]

    assert etape.shadow is not None
    regles = {rule for _, rule in etape.shadow.rules}
    assert "seigneur_en_danger" in regles, f"regles vues : {etape.shadow.rules}"
    assert not etape.interventions, "l'observation a repris une unite"


# --- l'armee entiere, du debut a la fin ----------------------------------------


def test_une_unite_laissee_de_cote_est_reprise_au_tour_suivant(
    bataille: Probe, tmp_path: Path
) -> None:
    """Une delegation faite une fois au depart ne couvre pas une bataille.

    Constate au premier soir de corpus : neuf unites confiees sur douze allies,
    et l'operateur voyant son armee « pousser, mais pas avec toutes les unites ».
    Une unite peut n'etre pas encore controlable au moment de la delegation,
    arriver en renfort, ou etre relachee par le planificateur du jeu — dans les
    trois cas elle restait plantee jusqu'a la fin.
    """
    from totalwar_ai.bridge.live import SupervisedSession
    from totalwar_ai.bridge.supervision import Supervisor

    session = SupervisedSession(
        bridge=FileBridge.open(tmp_path).tail(),
        supervisor=Supervisor(rules=()),
        wait=lambda _: bataille.advance(600),
    )
    bataille.advance(1200)
    state = session.bridge.latest_battle_state()
    assert state is not None

    # On ne confie qu'une partie de l'armee : l'autre echappe a l'IA du jeu.
    partielle = [state.allies[0].unit_id]
    session.bridge.delegate(partielle)
    bataille.advance(1200)
    session.delegated = set(partielle)

    session.step()

    attendues = {unite.unit_id for unite in state.allies if unite.controllable and unite.alive}
    assert session.delegated == attendues, (
        f"{len(attendues) - len(session.delegated)} unite(s) toujours sans pilote"
    )


def test_une_unite_reprise_par_la_supervision_n_est_pas_reconfiee(
    bataille: Probe, tmp_path: Path
) -> None:
    """Reconfier une unite reprise annulerait la correction dans le tour meme."""
    from totalwar_ai.bridge.live import SupervisedSession
    from totalwar_ai.bridge.supervision import DEFAULT_RULES, Supervisor

    superviseur = Supervisor(rules=DEFAULT_RULES)
    session = SupervisedSession(
        bridge=FileBridge.open(tmp_path).tail(),
        supervisor=superviseur,
        wait=lambda _: bataille.advance(600),
    )
    bataille.advance(1200)
    state = session.bridge.latest_battle_state()
    assert state is not None
    tenue = state.allies[0].unit_id
    superviseur.reclaimed[tenue] = 0.0

    session.step()

    assert tenue not in session.delegated, "l'unite tenue par la supervision a ete reconfiee"


def test_une_unite_refusee_retrouve_sa_chance(bataille: Probe, tmp_path: Path) -> None:
    """Le refus est temporaire, et le croire definitif coute des unites.

    Constate en bataille : quatre unites de tir reprises puis rendues ont ete
    refusees d'affilee — « unite non controlable » — parce qu'elles etaient en
    deroute a cet instant. Ecartees pour de bon, elles ont fini la bataille sans
    pilote, ni a nous ni a l'IA du jeu.
    """
    from totalwar_ai.bridge.live import SupervisedSession
    from totalwar_ai.bridge.supervision import Supervisor

    session = SupervisedSession(
        bridge=FileBridge.open(tmp_path).tail(),
        supervisor=Supervisor(rules=()),
        wait=lambda _: bataille.advance(600),
        retry_after=30.0,
    )
    bataille.advance(1200)
    etat = session.bridge.latest_battle_state()
    assert etat is not None
    ecartee = etat.allies[0].unit_id

    # Le jeu l'a refusee a l'instant zero : elle est mise de cote.
    session._reject([ecartee], 0.0)
    assert ecartee in session.unreachable

    # Vingt secondes plus tard, elle l'est toujours.
    session._forget_expired(20.0)
    assert ecartee in session.unreachable

    # Passe le delai, on lui redonne sa chance.
    session._forget_expired(31.0)
    assert ecartee not in session.unreachable


def test_la_prise_en_main_insiste_jusqu_a_obtenir_le_controle(
    bataille: Probe, tmp_path: Path
) -> None:
    """L'operateur relançait la commande jusqu'a dix fois pendant que la
    bataille avancait sans nous : la derniere session supervisee n'a pris la
    main qu'a la 179e seconde, armees deja au contact."""
    from totalwar_ai.bridge.live import SupervisedSession
    from totalwar_ai.bridge.supervision import Supervisor
    from totalwar_ai.cli import _take_command

    bridge = FileBridge.open(tmp_path).tail()
    session = SupervisedSession(
        bridge=bridge,
        supervisor=Supervisor(rules=()),
        wait=lambda _: bataille.advance(600),
    )

    essais = {"n": 0}
    vraie_delegation = session.delegate_all

    def refuse_deux_fois(state: object) -> list[str]:
        essais["n"] += 1
        if essais["n"] <= 2:
            session.last_refusal = "la bataille n'a pas commence"
            return []
        return vraie_delegation(state)  # type: ignore[arg-type]

    session.delegate_all = refuse_deux_fois  # type: ignore[method-assign]
    bataille.advance(1200)

    resultat = _take_command(session, bridge, patience=30.0, sleep=lambda _: bataille.advance(1200))

    assert resultat is not None, "la prise en main a renonce trop tot"
    assert essais["n"] >= 3, "elle n'a pas insiste"
    assert resultat[2], "aucune unite confiee alors que la delegation a fini par passer"


def test_le_pilotage_attend_que_la_bataille_soit_jouable(bataille: Probe, tmp_path: Path) -> None:
    """**`--play` est le seul mode ou le travail tactique s'applique.**

    En supervision, nos regles n'envoient que des destinations de repli : le
    ciblage du planificateur n'y sert a rien. C'est en pilotage que le choix de
    cible atteint le jeu — et c'etait justement le mode reste sans attente, qui
    consommait son chronometre a tourner dans le vide.
    """
    from totalwar_ai.cli import _await_battle

    bridge = FileBridge.open(tmp_path).tail()
    bataille.advance(1200)

    etat = _await_battle(bridge, patience=30.0, sleep=lambda _: bataille.advance(1200))

    assert etat is not None, "l'attente a renonce alors que la bataille etait jouable"
    assert [unite for unite in etat.allies if unite.controllable and unite.alive]


def test_le_pilotage_renonce_proprement_si_la_bataille_ne_vient_pas(tmp_path: Path) -> None:
    """Sans jeu en face, on renonce au bout du delai plutot que de piloter le vide."""
    from totalwar_ai.cli import _await_battle

    bridge = FileBridge.open(tmp_path).tail()
    horloge = {"t": 0.0}

    def dormir(_: float) -> None:
        horloge["t"] += 1.0

    assert _await_battle(bridge, patience=0.0, sleep=dormir) is None


def test_le_pilotage_rend_compte_des_ordres_refuses(bataille: Probe, tmp_path: Path) -> None:
    """**Ce mode ne lisait aucun accuse.**

    Un ordre refuse ne laissait aucune trace : le compte rendu annoncait « 3
    attaque(s) » pour trois ordres tombes dans le vide, et l'unite restait
    plantee sans que rien ne le dise a l'operateur.
    """
    from totalwar_ai.bridge.live import LiveSession
    from totalwar_ai.config import load_config

    bridge = FileBridge.open(tmp_path).tail()
    session = LiveSession(
        bridge=bridge,
        agent=DeterministicTacticalAgent.from_config(load_config(data_dir=tmp_path)),
        wait=lambda _: bataille.advance(600),
    )
    bataille.advance(1200)

    # Toutes nos unites deviennent intouchables : le vrai Lua refusera chaque
    # ordre avec « unite non controlable ».
    for unite in ARMEE:
        bataille.make_uncontrollable(unite[0])

    etape = session.step()

    assert etape.sent, "aucun ordre emis : le test ne prouverait rien"
    assert etape.refused, "un ordre refuse par le jeu est passe inapercu"
    assert "refusee(s) par le jeu" in etape.summary()


def test_une_unite_qui_n_est_pas_arrivee_est_relancee(bataille: Probe, tmp_path: Path) -> None:
    """**Le defaut qui a immobilise l'armee pendant trois minutes.**

    Le jeu rend la main au bout de cinq secondes et l'unite s'arrete ou elle se
    trouve. L'agent recalculait la meme destination, la jugeait deja envoyee, et
    ne la renvoyait jamais. Bataille `a1274d62` : douze deplacements a t=3 s,
    puis cent quatre-vingt-dix secondes sans un ordre, jusqu'a ce que
    l'operateur deplace une unite a la souris.
    """
    from totalwar_ai.bridge.live import MIN_REORDER_DISTANCE, LiveSession
    from totalwar_ai.bridge.orders import Translation
    from totalwar_ai.config import load_config
    from totalwar_ai.domain.geometry import Vector3

    bridge = FileBridge.open(tmp_path).tail()
    session = LiveSession(
        bridge=bridge,
        agent=DeterministicTacticalAgent.from_config(load_config(data_dir=tmp_path)),
        wait=lambda _: bataille.advance(600),
    )
    bataille.advance(1200)
    etat = bridge.latest_battle_state()
    assert etat is not None
    domaine = etat.to_battle_state("test")

    unite = domaine.allies()[0]
    loin = Vector3(unite.position.x, 0.0, unite.position.z + 500.0)
    ordre = Translation(moves=((unite.id, loin),))

    # Premier envoi : rien a taire.
    assert session._drop_micro_moves(ordre, domaine).moves

    # L'unite n'a pas bouge : le meme ordre doit repartir.
    repete = session._drop_micro_moves(ordre, domaine)
    assert repete.moves, "l'unite immobile n'a jamais recu son ordre une seconde fois"

    # Une fois sur place, on cesse de la harceler.
    arrivee = Vector3(unite.position.x, 0.0, unite.position.z + MIN_REORDER_DISTANCE / 2)
    session._last_destination[unite.id] = arrivee
    assert not session._drop_micro_moves(Translation(moves=((unite.id, arrivee),)), domaine).moves


# --- LIVE-001 : nommer l'etage ou la commande disparait -----------------------


def test_un_tour_muet_nomme_l_etage_ou_la_commande_a_disparu(
    session: LiveSession, bataille: Probe
) -> None:
    """Le defaut que le banc ne peut structurellement pas voir.

    En bataille reelle, l'agent est reste **364 secondes** sans emettre une
    commande pendant que son armee passait de 12 a 9 unites. Le banc, lui, ne
    depasse jamais 11 s de silence — parce qu'il appelle l'agent directement et
    **ne traverse ni la traduction ni le filtre de micro-deplacements**.

    Or `_drop_micro_moves` documente exactement cette paralysie, deja constatee :
    « douze deplacements a t=3 s, puis cent quatre-vingt-dix secondes sans un
    ordre, jusqu'a ce que l'operateur deplace une unite a la souris ».

    Un tour muet doit donc dire **ou** la commande est morte, sinon un agent qui
    decide correctement et un pont qui n'envoie rien produisent le meme silence.
    """
    premier = session.step()
    assert premier.sent, "le premier tour doit lancer l'armee"

    # Rejoue sans laisser le temps aux unites d'avancer : les destinations
    # recalculees sont les memes, et le pont les ecarte.
    muets = []
    for _ in range(6):
        bataille.advance(500)
        etape = session.step()
        if etape.state is not None and not etape.sent and not etape.skipped:
            muets.append(etape)

    assert muets, "au moins un tour doit rester muet"
    for etape in muets:
        assert etape.no_command_stage is not None, (
            "un tour muet sans etage nomme est exactement le silence "
            "qu'on a passe une session a ne pas savoir expliquer"
        )
        assert etape.stages, "les comptes par etage doivent accompagner le verdict"
        assert "NO_COMMAND stage=" in etape.summary()


def test_un_tour_qui_commande_ne_porte_aucun_etage(session: LiveSession) -> None:
    """`no_command_stage` ne se remplit que quand rien n'est parti.

    Sans cela, le champ se remplirait a chaque tour et ne designerait plus rien.
    """
    etape = session.step()
    assert etape.sent
    assert etape.no_command_stage is None


def test_un_ordre_jamais_acquitte_ne_passe_pas_pour_un_succes(
    session: LiveSession, bataille: Probe
) -> None:
    """`ACK absent` valait `ACK accepte`.

    Le code rendait le meme tuple vide dans les deux cas, si bien que « Python a
    ecrit quatre ordres » et « le Lua n'a jamais vu le fichier » se lisaient
    pareil — et l'instrument annoncait que tout allait bien.
    """
    session.ack_timeout = 0.0  # le jeu n'aura pas le temps de repondre
    etape = session.step()
    assert etape.sent, "Python a bien ecrit des ordres"
    assert etape.acknowledgement.sent_by_python == etape.sent
    assert etape.acknowledgement.ack_timeout is True
    assert etape.acknowledgement.acknowledged_by_lua == 0


def test_chaque_tour_laisse_un_battement_a_deux_horloges(session: LiveSession) -> None:
    """Le `script_log` prouve que le Lua publiait, pas que Python lisait.

    Une boucle suspendue puis rattrapant son arriere produirait exactement le
    meme silence de commandes ; seule l'horloge murale les separe.
    """
    etape = session.step()
    assert etape.heartbeat.wall_clock > 0.0
    assert etape.heartbeat.state_sequence > 0
    assert etape.heartbeat.decision_due is True
