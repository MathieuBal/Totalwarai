"""Interface en ligne de commande.

`totalwar-ai <commande>` — quatre commandes couvrent le MVP :

* `scenarios` : lister les situations disponibles ;
* `simulate`  : jouer une ou plusieurs batailles ;
* `history`   : consulter la memoire persistante ;
* `report`    : reafficher le rapport d'une bataille.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from totalwar_ai import __version__
from totalwar_ai.agent.planner import Posture
from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.bridge.file_bridge import FileBridge, summarise
from totalwar_ai.bridge.paths import EXPECTED_PROBE_REVISION, BridgeDirectoryNotFoundError
from totalwar_ai.bridge.recording import BattleRecorder
from totalwar_ai.config import AppConfig, ConfigError, load_config
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.learning.checkpoints import CheckpointStore
from totalwar_ai.learning.evaluation import (
    DEFAULT_SEEDS,
    BenchmarkReport,
    compare,
    render_table,
    run_benchmark,
)
from totalwar_ai.memory.replay_buffer import ReplayBuffer
from totalwar_ai.memory.repository import MemoryRepository
from totalwar_ai.simulation.runner import run_battle
from totalwar_ai.simulation.scenarios import Scenario, ScenarioCatalog
from totalwar_ai.telemetry.battle_logger import configure_logging

if TYPE_CHECKING:
    from totalwar_ai.bridge.live import LiveStep
    from totalwar_ai.domain.battle_state import BattleState
    from totalwar_ai.learning.corpus import Corpus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="totalwar-ai",
        description="Agent tactique experimental pour Total War: WARHAMMER III (hors jeu).",
    )
    parser.add_argument("--version", action="version", version=f"totalwar-ai {__version__}")
    parser.add_argument("--config", help="fichier de configuration YAML a utiliser")
    parser.add_argument("--data-dir", help="repertoire de donnees (batailles, rapports, base)")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scenarios", help="lister les scenarios disponibles")

    simulate = subparsers.add_parser("simulate", help="jouer un scenario avec l'agent")
    simulate.add_argument("--scenario", default="balanced_clash", help="nom du scenario")
    simulate.add_argument(
        "--seed", type=int, default=None, help="graine (defaut : celle du scenario)"
    )
    simulate.add_argument("--episodes", type=int, default=1, help="nombre de batailles a jouer")
    simulate.add_argument("--all", action="store_true", help="jouer tous les scenarios")
    simulate.add_argument("--no-memory", action="store_true", help="ne rien enregistrer en memoire")
    simulate.add_argument(
        "--no-adapt",
        action="store_true",
        help="ignorer la doctrine apprise et jouer avec les reglages par defaut",
    )
    simulate.add_argument("--no-report", action="store_true", help="ne pas ecrire de rapport")
    simulate.add_argument(
        "--explain", action="store_true", help="afficher les decisions marquantes"
    )

    history = subparsers.add_parser("history", help="consulter la memoire persistante")
    history.add_argument("--limit", type=int, default=10, help="nombre de batailles a afficher")
    history.add_argument("--scenario", default=None, help="filtrer sur un scenario")

    report = subparsers.add_parser("report", help="afficher le rapport d'une bataille")
    report.add_argument("battle_id", help="identifiant complet ou prefixe")

    subparsers.add_parser("doctrine", help="afficher les doctrines apprises")

    learn = subparsers.add_parser(
        "learn", help="apprendre des batailles enregistrees en regardant jouer l'IA du jeu"
    )
    learn.add_argument(
        "--check",
        action="store_true",
        help="verifier ce que valent les batailles enregistrees, sans rien apprendre",
    )
    learn.add_argument(
        "--calibrate",
        action="store_true",
        help="etalonner l'instrument sur la doublure, dont la politique est connue",
    )
    learn.add_argument(
        "--targets",
        action="store_true",
        help="apprendre le ciblage des batailles enregistrees et le mesurer",
    )

    bench = subparsers.add_parser(
        "bench", help="rejouer le banc de scenarios et detecter les regressions"
    )
    bench.add_argument("--seeds", type=int, default=3, help="nombre de graines par scenario")
    bench.add_argument("--scenario", action="append", help="limiter a ce scenario (repetable)")
    bench.add_argument(
        "--save-baseline", action="store_true", help="enregistrer ce banc comme reference"
    )
    bench.add_argument("--label", default="", help="etiquette libre pour la reference")
    bench.add_argument(
        "--no-compare", action="store_true", help="ne pas comparer a la reference enregistree"
    )
    bench.add_argument(
        "--supervised",
        action="store_true",
        help="mesurer la supervision : doublure de l'IA du moteur seule, "
        "puis la meme doublure avec nos regles",
    )

    probe = subparsers.add_parser("probe", help="piloter la sonde d'integration au jeu (prototype)")
    probe.add_argument(
        "--bridge-dir",
        help="dossier d'installation du jeu, ou dossier d'echange "
        "(defaut : $TOTALWAR_AI_BRIDGE_DIR)",
    )
    probe.add_argument("--status", action="store_true", help="afficher l'etat du pont")
    probe.add_argument("--watch", type=float, metavar="SECONDES", help="attendre un etat du jeu")
    probe.add_argument(
        "--move", type=float, metavar="METRES", help="deplacer l'unite observee de N metres"
    )
    probe.add_argument(
        "--play",
        type=float,
        nargs="?",
        const=120.0,
        metavar="SECONDES",
        help="laisser l'agent piloter la bataille pendant N secondes (defaut 120)",
    )
    probe.add_argument(
        "--supervise",
        type=float,
        nargs="?",
        const=300.0,
        metavar="SECONDES",
        help="l'IA du jeu mene la bataille, nos regles corrigent ses angles morts (defaut 300 s)",
    )
    probe.add_argument(
        "--observe",
        type=float,
        nargs="?",
        const=300.0,
        metavar="SECONDES",
        help="l'IA du jeu joue seule, on enregistre sans intervenir : "
        "c'est la mesure de reference a laquelle comparer les autres modes "
        "(defaut 300 s)",
    )
    probe.add_argument(
        "--delegate",
        action="store_true",
        help="confier toute l'armee a l'IA de bataille du jeu "
        "(elle connait le terrain et les formations ; notre agent, non)",
    )
    probe.add_argument(
        "--reclaim",
        action="store_true",
        help="reprendre les unites confiees a l'IA du jeu",
    )
    probe.add_argument(
        "--posture",
        choices=[item.value for item in Posture],
        help="imposer une posture a l'agent pendant le pilotage "
        "(en escarmouche, l'adversaire attend : sans cela les deux armees "
        "ne s'affrontent jamais)",
    )
    probe.add_argument(
        "--no-record",
        action="store_true",
        help="ne pas enregistrer la bataille pilotee en memoire",
    )
    probe.add_argument("--abort", action="store_true", help="arret d'urgence : tout liberer")
    probe.add_argument("--reset", action="store_true", help="vider les flux avant de commencer")
    probe.add_argument(
        "--log",
        nargs="?",
        type=int,
        const=40,
        metavar="N",
        help="afficher les N dernieres lignes [totalwar_ai] du journal du jeu",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entree du CLI. Renvoie le code de sortie du processus."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config, data_dir=args.data_dir)
    except ConfigError as error:
        print(f"Configuration invalide : {error}", file=sys.stderr)
        return 2

    configure_logging(args.log_level or str(config.telemetry.get("level", "INFO")))

    if args.command == "scenarios":
        return _cmd_scenarios()
    if args.command == "simulate":
        return _cmd_simulate(args, config)
    if args.command == "history":
        return _cmd_history(args, config)
    if args.command == "learn":
        return _cmd_learn(args, config)
    if args.command == "doctrine":
        return _cmd_doctrine(config)
    if args.command == "bench":
        return _cmd_bench(args, config)
    if args.command == "probe":
        return _cmd_probe(args)
    # `required=True` sur les sous-commandes garantit qu'on ne passe jamais ici.
    return _cmd_report(args, config)


# --- commandes ---------------------------------------------------------------


def _cmd_scenarios() -> int:
    catalog = ScenarioCatalog()
    print("Scenarios disponibles :\n")
    for scenario in catalog.all():
        tags = f" [{', '.join(scenario.tags)}]" if scenario.tags else ""
        print(f"  {scenario.name:22} graine {scenario.seed:<4} {scenario.description}{tags}")
    return 0


def _cmd_simulate(args: argparse.Namespace, config: AppConfig) -> int:
    catalog = ScenarioCatalog()
    try:
        scenarios = catalog.all() if args.all else [catalog.get(args.scenario)]
    except KeyError as error:
        print(str(error), file=sys.stderr)
        return 2

    repository: MemoryRepository | None = None
    if not args.no_memory:
        repository = MemoryRepository(config.path("memory", "database_path"))
        buffer = ReplayBuffer(capacity=int(config.memory.get("replay_capacity", 250_000)))
        loaded = buffer.fill_from(repository, limit=5000)
        stats = repository.stats()
        print(
            f"Memoire : {stats['battles']} batailles connues, "
            f"{loaded} transitions rechargees "
            f"(taux de victoire {stats['win_rate']:.0%})\n"
        )

    agent = DeterministicTacticalAgent.from_config(config)
    try:
        for scenario in scenarios:
            for episode in range(max(1, args.episodes)):
                seed = args.seed if args.seed is not None else scenario.seed + episode
                result = run_battle(
                    scenario,
                    agent=agent,
                    config=config,
                    seed=seed,
                    repository=repository,
                    generate_report=not args.no_report,
                    adapt=False if args.no_adapt else None,
                )
                summary = result.summary
                print(
                    f"{scenario.name:22} graine {seed:<5} {summary.outcome.value:8} "
                    f"{summary.duration:6.1f}s  "
                    f"allies {summary.ally_remaining:5.0%} / "
                    f"ennemis {summary.enemy_remaining:5.0%}  "
                    f"recompense {summary.total_reward:+9.1f}"
                )
                print(
                    f"  ordres {summary.actions_sent}, bloques {summary.actions_blocked}, "
                    f"refuses {summary.actions_rejected}, "
                    f"transitions {len(result.episode.transitions)}"
                )
                if result.report_path:
                    print(f"  rapport : {result.report_path}")
                if result.log_path:
                    print(f"  journal : {result.log_path}")
                if not result.profile.is_empty:
                    print("  doctrine ajustee par l'historique :")
                    for reason in result.profile.rationale:
                        print(f"    - {reason}")
                if args.explain:
                    _print_explanations(result)
                print()
    finally:
        if repository is not None:
            repository.close()
    return 0


def _cmd_history(args: argparse.Namespace, config: AppConfig) -> int:
    path = config.path("memory", "database_path")
    if not Path(path).exists():
        print("Aucune memoire enregistree pour l'instant.")
        return 0
    with MemoryRepository(path) as repository:
        battles = repository.list_battles(limit=args.limit, scenario=args.scenario)
        stats = repository.stats()
        if not battles:
            print("Aucune bataille en memoire.")
            return 0
        print(
            f"{stats['battles']} batailles, taux de victoire {stats['win_rate']:.0%}, "
            f"{stats['transitions']} transitions (schema v{stats['schema_version']})\n"
        )
        for battle in battles:
            print(
                f"  {battle.battle_id[:8]}  {battle.scenario:22} {battle.outcome.value:8} "
                f"graine {battle.seed:<5} {battle.duration:6.1f}s  "
                f"allies {battle.ally_remaining:5.0%}  recompense {battle.total_reward:+9.1f}"
            )
    return 0


BASELINE_FILENAME = "benchmark-baseline.json"


def _cmd_probe(args: argparse.Namespace) -> int:
    """Pilote la sonde d'integration au jeu.

    Cette commande ne simule rien : elle parle au script Lua a travers le
    repertoire d'echange. Sans jeu lance, elle constate simplement qu'il ne se
    passe rien — ce qui est deja une information.
    """
    try:
        bridge = FileBridge.open(args.bridge_dir)
    except BridgeDirectoryNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 2

    print(f"Repertoire d'echange : {bridge.paths.directory}")

    if args.log:
        return _print_game_log(bridge, args.log)

    if args.reset:
        bridge.reset()
        print("Flux vides.")

    if args.abort:
        bridge.abort("arret demande depuis le CLI")
        print("Arret d'urgence publie : le script Lua doit tout liberer.")
        return 0

    # Les trois modes de pilotage attendent des etats du jeu. Une sentinelle
    # d'arret oubliee les fait tourner a vide, sans qu'aucun n'en dise rien.
    if args.supervise or args.observe or args.delegate or args.play:
        if _stop_sentinel_blocks(bridge):
            return 1
        # On ne pilote jamais sur une archive : seuls les etats publies a partir
        # de maintenant disent ou en est reellement la bataille.
        bridge.tail()

    if args.supervise:
        return _supervise(bridge, args.supervise, record=not args.no_record)
    if args.observe:
        return _supervise(bridge, args.observe, record=not args.no_record, supervised=False)
    if args.delegate:
        return _delegate(bridge)
    if args.reclaim:
        return _reclaim(bridge)

    if args.play:
        return _play(
            bridge,
            args.play,
            record=not args.no_record,
            posture=Posture(args.posture) if args.posture else None,
        )

    if args.status or not (args.watch or args.move):
        _print_probe_status(bridge)
        return 0

    state = None
    if args.watch or args.move:
        delai = args.watch or 30.0
        print(f"Attente d'un etat du jeu ({delai:.0f} s)...")
        state = bridge.wait_for_state(timeout=delai)
        if state is None:
            print(
                "Aucun etat recu. Verifier que la bataille est lancee, que le mod est actif, "
                "et que le dossier d'echange est bien celui du jeu.",
                file=sys.stderr,
            )
            return 1
        print(
            f"Unite {state.unit_id} ({state.unit_type or 'type inconnu'}) "
            f"en ({state.position.x:.1f}, {state.position.z:.1f}), "
            f"controlable={state.controllable}" + (f", phase {state.phase}" if state.phase else "")
        )
        if not state.orders_take_effect:
            print(
                f"Phase {state.phase} : le moteur accepte les ordres mais l'unite "
                "ne bougera pas avant le debut de la bataille.",
                file=sys.stderr,
            )

    if args.move and state is not None:
        destination = Vector3(state.position.x + args.move, state.position.y, state.position.z)
        commande = bridge.move_unit(state.unit_id, destination)
        print(f"Ordre {commande.sequence} publie : deplacement de {args.move:.0f} m.")
        ack = bridge.wait_for_ack(commande.sequence, timeout=30.0)
        if ack is None:
            print("Aucun accuse recu dans le delai imparti.", file=sys.stderr)
            return 1
        print(f"Accuse : {ack.status.value}" + (f" ({ack.error})" if ack.error else ""))
        if not ack.accepted:
            return 1
        return _confirm_movement(bridge, state.unit_id, state.position)

    return 0


def _print_outcome(recorder: BattleRecorder) -> None:
    """Issue et forces restantes, en une ligne.

    Une session interrompue reste `unknown`, et le dit : deviner une victoire
    parce que l'armee se portait bien au moment ou l'on a cesse de regarder
    fausserait la seule comparaison qui compte.
    """
    from totalwar_ai.domain.battle_state import BattleOutcomeKind

    fiche = recorder.summary()
    if fiche.outcome is BattleOutcomeKind.UNKNOWN:
        print(
            "  Issue inconnue : la bataille n'a pas ete suivie jusqu'a son terme. Non comparable."
        )
        return
    print(
        f"  Issue : {fiche.outcome.value} — {fiche.ally_remaining:.0%} de nos unites "
        f"debout, {fiche.enemy_remaining:.0%} des leurs, en {fiche.duration:.0f} s."
    )


def _battle_over(etape: LiveStep) -> bool:
    """La bataille est-elle terminee ?

    Le jeu publie un dernier etat en phase `Complete`, puis la sonde s'arrete.
    Continuer a tourner apres coup ne mesure plus rien : la duree demandee est
    un plafond, pas une consigne d'attente.
    """
    return etape.state is not None and etape.state.phase == "Complete"


def _supervise(
    bridge: FileBridge,
    duration: float,
    *,
    record: bool = True,
    supervised: bool = True,
) -> int:
    """L'IA du jeu mene la bataille ; nos regles corrigent ses angles morts.

    Elle connait le terrain, le pathfinding et les formations, que nous n'avons
    pas. Nos regles, elles, savent qu'une piece d'artillerie prise au corps a
    corps est perdue, qu'un tireur au contact ne sert plus a rien, et qu'un
    seigneur mourant coute la bataille.

    Chaque reprise est journalisee avec son motif : une session sans
    intervention doit se lire aussi clairement qu'une session mouvementee.

    **`supervised=False` retire toutes les regles** : l'IA du jeu joue seule et
    l'on se contente d'observer et d'enregistrer. C'est la mesure de reference,
    et elle passe volontairement par le meme code que la supervision — deux
    chemins differents ne produiraient pas deux batailles comparables.
    """
    from totalwar_ai.bridge.live import SupervisedSession
    from totalwar_ai.bridge.supervision import DEFAULT_RULES, Supervisor

    config = load_config()
    # L'agent decide **dans le vide**, en parallele de l'IA du moteur : rien ne
    # part vers le jeu, et chaque tour devient un couple etiquete « elle a fait
    # ceci, nous aurions fait cela ». C'est la matiere premiere de
    # l'apprentissage par observation, obtenue sans jouer une bataille de plus.
    #
    # Les regles sont evaluees a part, pour savoir enfin a quelle frequence
    # chacune se declencherait en vraie bataille.
    session = SupervisedSession(
        bridge=bridge,
        supervisor=Supervisor(rules=DEFAULT_RULES if supervised else ()),
        shadow_agent=DeterministicTacticalAgent.from_config(config),
        shadow_rules=Supervisor(rules=DEFAULT_RULES),
    )
    recorder = BattleRecorder(
        directory=config.path("telemetry", "battles_dir") if record else None,
        record_units=bool(config.telemetry.get("record_units", True)),
    )

    print("Attente d'un etat du jeu (30 s)...")
    state = None
    for _ in range(60):
        state = bridge.latest_battle_state()
        if state is not None:
            break
        time.sleep(0.5)
    if state is None:
        print("Aucun etat recu : la bataille est-elle lancee ?", file=sys.stderr)
        return 1

    demandees = [unite.unit_id for unite in state.allies if unite.controllable and unite.alive]
    confiees = session.delegate_all(state)
    if not confiees:
        print("Aucune unite confiee : le jeu a refuse la delegation.", file=sys.stderr)
        return 1
    # Le compte annonce est celui du jeu. En annoncer un autre s'est produit en
    # bataille : dix-huit demandees, six confiees, et la difference passee sous
    # silence pendant toute la session.
    print(f"{len(confiees)} unite(s) confiees a l'IA du jeu.")
    if len(confiees) < len(demandees):
        manquantes = sorted(set(demandees) - set(confiees))
        print(
            f"  {len(manquantes)} unite(s) refusees par le jeu et laissees de cote : "
            + ", ".join(manquantes)
        )
    quoi = "Supervision" if supervised else "Observation (aucune regle : l'IA du jeu joue seule)"
    print(f"{quoi} pour {duration:.0f} s. Ctrl+C pour tout arreter et rendre la main.")
    if recorder.path is not None:
        print(f"Enregistrement : {recorder.path}")
    print()

    fin = time.monotonic() + duration
    interrompu = False
    terminee = False
    try:
        while time.monotonic() < fin:
            etape = session.step()
            recorder.observe(etape)
            if etape.interventions or etape.returned or etape.skipped or etape.refused:
                print(f"  {etape.summary()}")
                for intervention in etape.interventions:
                    lignes = intervention.explain().splitlines()
                    print(f"      ! {lignes[0]} — {lignes[-1]}")
                for unit_id, motif in etape.refused:
                    print(f"      x {unit_id} hors de portee du jeu : {motif}")
            if _battle_over(etape):
                print("  Bataille terminee.")
                terminee = True
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nInterruption : liberation de toutes les unites.")
        session.stop()
        interrompu = True
    finally:
        recorder.close()

    print(
        f"\n{recorder.turns} tour(s), {recorder.orders_sent} intervention(s) sur "
        f"{len(confiees)} unite(s) confiees."
    )
    # Le resultat au moment ou on le regarde, pas trois commandes plus tard :
    # c'est le seul chiffre qui permette de comparer deux modes de pilotage.
    _print_outcome(recorder)
    if not supervised:
        print("  Aucune regle n'etait active : c'est la mesure de reference.")
    if not interrompu and not terminee:
        print("Les unites restent confiees a l'IA du jeu.")
        print("  `totalwar-ai probe --reclaim` pour reprendre la main.")
    if record and recorder.turns:
        MemoryRepository(config.path("memory", "database_path")).save_episode(recorder.episode())
        print(f"Bataille enregistree : {recorder.battle_id}")
    return 130 if interrompu else 0


def _delegate(bridge: FileBridge) -> int:
    """Confie toute l'armee observee a l'IA de bataille du jeu.

    **Plus engageant qu'un ordre de deplacement** : les unites restent a l'IA du
    jeu jusqu'a `--reclaim`, sans restitution automatique. Le fichier d'arret et
    la fin de bataille les reprennent aussi.
    """
    print("Attente d'un etat du jeu (30 s)...")
    state = bridge.latest_battle_state()
    for _ in range(60):
        if state is not None:
            break
        time.sleep(0.5)
        state = bridge.latest_battle_state()
    if state is None:
        print("Aucun etat recu : la bataille est-elle lancee ?", file=sys.stderr)
        return 1

    unit_ids = [unite.unit_id for unite in state.allies if unite.controllable and unite.alive]
    if not unit_ids:
        print("Aucune unite controlable a confier.", file=sys.stderr)
        return 1

    commande = bridge.delegate(unit_ids)
    accuse = bridge.wait_for_ack(commande.sequence, timeout=15.0)
    if accuse is None:
        print("Aucun accuse recu.", file=sys.stderr)
        return 1

    # Le compte annonce est celui du jeu, jamais le notre. Annoncer dix-huit
    # unites confiees quand le Lua n'en avait pris que six s'est produit en
    # bataille, et la difference est restee invisible toute la session.
    confiees = [unit_id for unit_id in unit_ids if unit_id not in accuse.refused_ids]
    print(f"{len(confiees)} unite(s) confiees a l'IA du jeu (ordre {commande.sequence}).")
    if accuse.refused_ids:
        print(
            f"  {len(accuse.refused_ids)} refusee(s) par le jeu : "
            + ", ".join(sorted(accuse.refused_ids))
        )
    print(f"Accuse : {accuse.status.value}" + (f" ({accuse.error})" if accuse.error else ""))
    if accuse.accepted:
        print("`totalwar-ai probe --reclaim` pour reprendre la main.")
    return 0 if accuse.accepted else 1


def _reclaim(bridge: FileBridge) -> int:
    """Reprend les unites confiees a l'IA du jeu."""
    commande = bridge.reclaim()
    accuse = bridge.wait_for_ack(commande.sequence, timeout=15.0)
    if accuse is None:
        print(
            "Aucun accuse recu. En cas de doute, `probe --abort` libere tout "
            "par une voie independante.",
            file=sys.stderr,
        )
        return 1
    print(f"Reprise : {accuse.status.value} — {accuse.detail or ''}")
    return 0


def _play(
    bridge: FileBridge,
    duration: float,
    *,
    record: bool = True,
    posture: Posture | None = None,
) -> int:
    """Laisse l'agent piloter la bataille, en rendant compte a chaque tour.

    **Ce que l'operateur doit savoir avant de lancer.** L'agent ne prend que les
    unites qu'il decide de deplacer, et le jeu les rend au bout de cinq
    secondes : reprendre la main a la souris est toujours possible, sans rien
    arreter. `Ctrl+C` coupe la boucle et libere tout.

    La bataille est **enregistree** dans le meme format que les batailles
    simulees. C'est la condition pour departager un jour le simulateur et le
    jeu, dont les verdicts divergent (voir `docs/decisions/0005`).
    """
    from totalwar_ai.bridge.live import LiveSession

    config = load_config()
    agent = DeterministicTacticalAgent.from_config(config)
    if posture is not None:
        agent.planner.forced_posture = posture
    session = LiveSession(bridge=bridge, agent=agent)
    recorder = BattleRecorder(
        directory=config.path("telemetry", "battles_dir") if record else None,
        record_units=bool(config.telemetry.get("record_units", True)),
    )

    print(f"Pilotage pour {duration:.0f} s. Ctrl+C pour tout arreter et rendre la main.")
    if posture is not None:
        print(f"Posture imposee : {posture.value} — ce n'est pas un choix de l'agent.")
    if recorder.path is not None:
        print(f"Enregistrement : {recorder.path}")
    print()

    fin = time.monotonic() + duration
    interrompu = False
    try:
        while time.monotonic() < fin:
            etape = session.step()
            recorder.observe(etape)
            print(f"  {etape.summary()}")
            for explication in etape.decisions:
                print(f"      + {explication.splitlines()[0]}")
            for refus in etape.blocked:
                print(f"      - {refus.splitlines()[0]}")
            if _battle_over(etape):
                print("  Bataille terminee.")
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nInterruption : liberation de toutes les unites.")
        session.stop()
        interrompu = True
    finally:
        recorder.close()

    _print_play_summary(recorder, config, record=record)
    return 130 if interrompu else 0


def _print_play_summary(recorder: BattleRecorder, config: AppConfig, *, record: bool) -> None:
    """Bilan d'une session de pilotage, et enregistrement en memoire."""
    resume = recorder.summary()
    print(
        f"\n{recorder.turns} tour(s), {resume.actions_sent} ordre(s) emis, "
        f"{resume.actions_blocked} refuse(s) par la securite, "
        f"{recorder.actions_lost} action(s) perdue(s)."
    )
    if recorder.turns == 0:
        print(
            "Aucun etat recu : verifier que la bataille est lancee et que le mod est actif.",
            file=sys.stderr,
        )
        return
    print(
        f"Forces restantes : {resume.ally_remaining:.0%} contre "
        f"{resume.enemy_remaining:.0%} — issue {resume.outcome.value}."
    )
    if resume.outcome.value == "unknown":
        print(
            "L'issue reste inconnue : le jeu ne l'annonce qu'en phase `Complete`. "
            "La deviner depuis les forces restantes fausserait la memoire."
        )
    if not record:
        return

    repository = MemoryRepository(config.path("memory", "database_path"))
    repository.save_episode(recorder.episode())
    print(f"Bataille enregistree : {resume.battle_id}")
    print("  `totalwar-ai history --scenario live` pour la retrouver.")


def _confirm_movement(
    bridge: FileBridge,
    unit_id: str,
    origin: Vector3,
    *,
    timeout: float = 25.0,
    threshold: float = 2.0,
    still_for: int = 3,
) -> int:
    """Mesure le deplacement **total** de l'unite apres un ordre.

    Un accuse dit que l'ordre a ete lance, pas qu'il a produit un deplacement.
    Vingt metres sur une carte de bataille passent inapercus a l'oeil nu : sans
    cette mesure, il faut croire la camera plutot que le jeu.

    On attend que l'unite se soit **arretee** — `still_for` etats consecutifs
    sans bouger — avant de conclure. Rendre le premier mouvement detecte
    annoncerait « 2,7 m » pour un trajet de 150 m, ce qui est pire que se taire.
    """
    print("Verification du deplacement...")
    deadline = time.monotonic() + timeout
    dernier = origin
    immobile = 0
    while time.monotonic() < deadline:
        for state in bridge.read_states():
            if state.unit_id != unit_id:
                continue
            pas = dernier.distance_2d(state.position)
            dernier = state.position
            immobile = immobile + 1 if pas < 0.5 else 0
        parcouru = origin.distance_2d(dernier)
        if parcouru >= threshold and immobile >= still_for:
            print(f"Deplacement constate : {parcouru:.1f} m.")
            return 0
        time.sleep(0.5)

    parcouru = origin.distance_2d(dernier)
    if parcouru >= threshold:
        # L'unite bougeait encore quand le delai a expire : c'est un succes,
        # simplement pas encore termine.
        print(f"Deplacement constate : {parcouru:.1f} m (unite encore en mouvement).")
        return 0
    print(
        f"Aucun deplacement constate ({parcouru:.1f} m en {timeout:.0f} s). "
        "L'ordre a ete accepte mais n'a rien produit : unite dans un groupe "
        "verrouille, ordre annule par le joueur, ou controle rendu trop tot.",
        file=sys.stderr,
    )
    return 1


def _print_game_log(bridge: FileBridge, limit: int) -> int:
    """Affiche ce que la sonde Lua a dit dans le journal du jeu.

    Tant que l'echange par fichiers n'est pas etabli, c'est la seule fenetre sur
    ce qui se passe cote jeu — et le canal de repli s'il ne l'est jamais.
    """
    journal = bridge.paths.latest_script_log()
    if journal is None:
        print(
            f"Aucun journal `script_log_*.txt` dans {bridge.paths.game_directory}.\n"
            "Verifier que le chemin designe bien le dossier d'installation du jeu, "
            "et que le journal de script est active.",
            file=sys.stderr,
        )
        return 1

    print(f"Journal : {journal.name} ({journal.stat().st_size} octets)\n")
    lignes = [
        ligne.rstrip()
        for ligne in journal.read_text(encoding="utf-8", errors="replace").splitlines()
        if "totalwar_ai" in ligne
    ]
    if not lignes:
        print(
            "Aucune ligne [totalwar_ai] : le script n'a pas ete charge.\n"
            "Verifier l'arborescence du pack (script/_lib/mod/totalwar_ai_probe.lua), "
            "l'activation du mod, et que le pack a bien ete reconstruit apres la "
            "derniere modification du script."
        )
        return 1

    # Les etats sont repetitifs par nature : quatre-vingts lignes d'affilee
    # peuvent n'etre que la meme unite immobile. On les met de cote pour que le
    # reste — diagnostics, recensement, erreurs, accuses — reste lisible.
    routine = [ligne for ligne in lignes if _est_routinier(ligne)]
    notable = [ligne for ligne in lignes if not _est_routinier(ligne)]

    for ligne in notable[-limit:]:
        print(f"  {ligne.strip()}")
    if routine:
        print(f"\n  ... et {len(routine)} ligne(s) d'etat, masquees ici.")
        print(f"  Derniere : {routine[-1].strip()}")

    _signaler_version(lignes)
    return 0


def _signaler_version(lignes: Sequence[str]) -> None:
    """Dit si le pack embarque bien la derniere version du script.

    C'est le diagnostic le plus frequent de ce projet — quatre essais en
    bataille perdus faute d'un pack reconstruit — et rien ne le disait.
    """
    revision = _revision_du_journal(lignes)
    if revision is None:
        print(
            "\nCe journal n'annonce aucune revision de script : le pack embarque "
            f"une version anterieure. Reconstruire avec la revision {EXPECTED_PROBE_REVISION}."
        )
        return
    if revision < EXPECTED_PROBE_REVISION:
        print(
            f"\nPack en revision {revision}, alors que le depot est en "
            f"{EXPECTED_PROBE_REVISION} : reconstruire le pack."
        )
        return
    if revision > EXPECTED_PROBE_REVISION:
        print(
            f"\nPack en revision {revision}, plus recente que ce Python "
            f"({EXPECTED_PROBE_REVISION}) : mettre a jour le paquet Python."
        )
        return
    print(f"\nPack a jour (revision {revision}).")


def _revision_du_journal(lignes: Sequence[str]) -> int | None:
    """Revision annoncee par la sonde, en ne gardant que la plus recente."""
    trouvees = [
        int(found.group(1))
        for ligne in lignes
        if (found := re.search(r"revision (\d+)\)", ligne)) is not None
    ]
    return trouvees[-1] if trouvees else None


def _est_routinier(ligne: str) -> bool:
    """Ligne d'etat periodique, sans valeur pour le diagnostic."""
    return "] STATE " in ligne or "] BATTLE " in ligne


def _stop_sentinel_blocks(bridge: FileBridge) -> bool:
    """Signale une sentinelle d'arret laissee par une session precedente.

    Elle est definitive du cote du jeu : le Lua libere tout et **cesse de lire**
    jusqu'a la bataille suivante. Constate en bataille, deux sessions lancees
    apres un Ctrl+C ont tourne cinq minutes sans recevoir un seul etat, en
    annoncant pourtant dix-huit unites confiees.

    Elle n'est pas retiree ici : lever un arret d'urgence est une decision du
    joueur, pas un effet de bord d'une commande de pilotage.
    """
    if not bridge.stop_requested:
        return False
    print(
        "Arret d'urgence encore actif : la sonde a tout libere et ne lit plus de "
        "commandes.\n"
        "  Relancer une bataille, puis `totalwar-ai probe --reset` pour lever la "
        "sentinelle.",
        file=sys.stderr,
    )
    return True


def _print_probe_status(bridge: FileBridge) -> None:
    """Etat des quatre fichiers du pont, sans rien interpreter."""
    for label, path in (
        ("etats", bridge.paths.state),
        ("commande", bridge.paths.command),
        ("accuses", bridge.paths.ack),
        ("arret", bridge.paths.stop),
    ):
        if path.exists():
            taille = path.stat().st_size
            print(f"  {label:9} present   ({taille} octets)  {path.name}")
        else:
            print(f"  {label:9} absent               {path.name}")

    etats = bridge.read_states()
    if etats:
        print(f"\n{summarise(etats)}")
    acks = bridge.read_acks()
    if acks:
        dernier = acks[-1]
        print(f"Dernier accuse : sequence {dernier.sequence}, statut {dernier.status.value}")


def _bench_supervised(
    scenarios: Sequence[Scenario], seeds: tuple[int, ...], config: AppConfig
) -> int:
    """La doublure de l'IA du moteur, seule puis supervisee.

    Repond a la seule question qui vaille pour la supervision : **nos regles
    ameliorent-elles une bataille que l'IA menait deja ?** Le CLI ne compare pas
    a une reference enregistree mais aux deux moities du meme banc, jouees a
    graines identiques — la difference est donc imputable aux regles et a rien
    d'autre.
    """
    from totalwar_ai.bridge.supervision import Supervisor
    from totalwar_ai.simulation.runner import run_supervised_battle

    print(
        f"Banc supervise : {len(scenarios)} scenarios x {len(seeds)} graines {seeds}\n"
        "  ATTENTION : la doublure n'est pas l'IA du jeu. Elle ignore le terrain,\n"
        "  les formations et le pathfinding. C'est un filtre rapide, pas un juge —\n"
        "  un gain constate ici reste a confirmer en bataille reelle.\n"
    )

    seule = run_benchmark(
        scenarios,
        seeds=seeds,
        config=config,
        label="doublure seule",
        battle_runner=lambda scenario, **kw: run_supervised_battle(scenario, **kw),
    )
    supervisee = run_benchmark(
        scenarios,
        seeds=seeds,
        config=config,
        label="doublure supervisee",
        # Un superviseur neuf par bataille : il retient les unites reprises, et
        # le partager ferait deteindre une bataille sur la suivante.
        battle_runner=lambda scenario, **kw: run_supervised_battle(
            scenario, supervisor=Supervisor(), **kw
        ),
    )

    print("--- l'IA du moteur seule (reference) ---")
    print(render_table(seule))
    print("\n--- la meme, avec nos regles ---")
    print(render_table(supervisee))

    verdict = compare(seule, supervisee)
    print("\n--- verdict ---")
    print(verdict.summary_line())
    for ecart in verdict.regressions:
        print(f"  - {ecart.scenario} {ecart.metric} : {ecart.before:.0%} -> {ecart.after:.0%}")
    for ecart in verdict.improvements:
        print(f"  + {ecart.scenario} {ecart.metric} : {ecart.before:.0%} -> {ecart.after:.0%}")
    if verdict.acceptable and not verdict.improvements:
        print("  Nos regles ne changent rien de mesurable sur ce banc.")
    return 0 if verdict.acceptable else 1


def _cmd_learn(args: argparse.Namespace, config: AppConfig) -> int:
    """Apprendre de l'IA du moteur, ou verifier de quoi on dispose pour le faire.

    `--check` d'abord, toujours : constituer un corpus demande des dizaines de
    parties, et une bataille trouee ne se voit pas a l'oeil nu dans un fichier
    de deux mega-octets.
    """
    from totalwar_ai.learning.corpus import Corpus

    if args.calibrate:
        return _learn_calibrate()

    corpus = Corpus.load(Path(config.path("telemetry", "battles_dir")))
    print(corpus.render())

    if args.targets:
        return _learn_targets(corpus)
    if not args.check:
        print(
            "\n`--check` dit ce que valent les batailles enregistrees, "
            "`--targets` apprend le ciblage, `--calibrate` etalonne l'instrument."
        )
    return 0 if corpus.usable or not corpus.battles else 1


def _learn_targets(corpus: Corpus) -> int:
    """Apprend le ciblage et la formation des batailles reelles exploitables.

    N'apprend **que** des batailles exploitables : une bataille trouee ferait
    entrer des changements de cible imaginaires — l'unite a change d'adversaire
    entre deux etats parce qu'il en manque un au milieu, pas parce qu'elle l'a
    voulu.
    """
    from totalwar_ai.learning.geometry import learn_formation
    from totalwar_ai.learning.observation import infer
    from totalwar_ai.learning.replay import iter_states, read_states
    from totalwar_ai.learning.targeting import evaluate, learn

    if not corpus.usable:
        print(
            "\nAucune bataille exploitable : rien a apprendre.\n"
            "  `totalwar-ai probe --observe 2400` pendant une escarmouche en cree une."
        )
        return 1

    # Deux passes sur le disque plutot qu'un corpus entier en memoire : trente
    # batailles a deux hertz font des centaines de milliers d'etats d'unite, et
    # relire un fichier coute bien moins cher que de les garder tous ouverts.
    observations = []
    for battle in corpus.usable:
        etats = read_states(battle.path)
        if len(etats) >= 2:
            observations += infer(etats).observations

    print(f"\n--- ciblage appris sur {len(corpus.usable)} bataille(s) ---\n")
    print(learn(observations).render())
    print()
    print(evaluate(observations).explain())

    print("\n--- formation observee ---\n")
    print(
        learn_formation(
            itertools.chain.from_iterable(iter_states(battle.path) for battle in corpus.usable)
        ).render()
    )
    return 0


def _learn_calibrate() -> int:
    """Etalonne l'inference et le ciblage sur la doublure, politique connue.

    **Aucune bataille n'est jouee.** La doublure de `simulation/native_ai.py`
    envoie sa cavalerie sur les tireurs et son infanterie sur le plus proche :
    on sait donc ce que l'instrument doit trouver. S'il echoue ici, il ne dira
    rien de bon sur l'IA du jeu.

    Cette commande existe pour que les chiffres publies dans l'ADR 0007 se
    reproduisent d'une seule ligne, au lieu d'etre repris de memoire.
    """
    from totalwar_ai.learning.observation import TARGETED_MOVES, infer
    from totalwar_ai.learning.targeting import evaluate, learn
    from totalwar_ai.simulation.environment import SimulationEnvironment
    from totalwar_ai.simulation.scenarios import SCENARIOS

    lots: list[list[BattleState]] = []
    for nom, fabrique in SCENARIOS.items():
        scenario = fabrique()
        env = SimulationEnvironment(nom, scenario.units, seed=7, ally_autopilot=True)
        etats = [env.state()]
        for _ in range(400):
            if env.finished:
                break
            etats.append(env.step().state)
        lots.append(etats)

    print(f"--- etalonnage sur {len(lots)} scenarios menes par la doublure ---\n")
    print("ambiguite des decisions ciblees :")
    for continuite in (False, True):
        cibles = ambigues = 0
        for etats in lots:
            resultat = infer(etats, use_continuity=continuite)
            vises = [item for item in resultat.observations if item.move in TARGETED_MOVES]
            cibles += len(vises)
            ambigues += sum(1 for item in vises if item.ambiguous)
        etiquette = "avec continuite" if continuite else "sans continuite"
        part = ambigues / cibles if cibles else 0.0
        print(f"  {etiquette:<18} {cibles:5d} decisions  {part:5.1%} ambigues")

    observations = [item for etats in lots for item in infer(etats).observations]
    print(f"\n{learn(observations).render()}\n")
    print(evaluate(observations).explain())

    # La formation ne s'etalonne pas contre la doublure : elle n'en a aucune, et
    # ce qu'on lit ici est surtout le deploiement que nous avons ecrit nous-memes
    # dans les scenarios. C'est un controle de bon fonctionnement, pas une mesure.
    from totalwar_ai.learning.geometry import learn_formation

    print("\n--- formation (controle de fonctionnement, non etalonne) ---\n")
    print(learn_formation([etat for etats in lots for etat in etats]).render())
    return 0


def _cmd_bench(args: argparse.Namespace, config: AppConfig) -> int:
    """Rejoue le banc, l'affiche, et le compare a la reference enregistree.

    Renvoie 1 en cas de regression : la commande est utilisable telle quelle
    comme garde-fou avant de pousser un changement de doctrine.
    """
    catalog = ScenarioCatalog()
    try:
        scenarios = (
            [catalog.get(name) for name in args.scenario] if args.scenario else catalog.all()
        )
    except KeyError as error:
        print(str(error), file=sys.stderr)
        return 2

    seeds = tuple(DEFAULT_SEEDS[: args.seeds]) or DEFAULT_SEEDS
    if args.seeds > len(DEFAULT_SEEDS):
        # Graines supplementaires deterministes, pour ne pas dependre du hasard.
        seeds = tuple(DEFAULT_SEEDS) + tuple(
            101 + index for index in range(args.seeds - len(DEFAULT_SEEDS))
        )

    if args.supervised:
        return _bench_supervised(scenarios, seeds, config)

    print(f"Banc : {len(scenarios)} scenarios x {len(seeds)} graines {seeds}\n")
    report = run_benchmark(scenarios, seeds=seeds, config=config, label=args.label)
    print(render_table(report))

    baseline_path = Path(config.path("memory", "models_dir")) / BASELINE_FILENAME
    if args.save_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nReference enregistree : {baseline_path}")
        return 0

    if args.no_compare or not baseline_path.exists():
        if not args.no_compare:
            print(
                "\nAucune reference enregistree. "
                "Utiliser `totalwar-ai bench --save-baseline` pour en creer une."
            )
        return 0

    baseline = BenchmarkReport.from_dict(json.loads(baseline_path.read_text(encoding="utf-8")))
    comparison = compare(baseline, report)
    print(f"\nComparaison a la reference : {comparison.summary_line()}")
    for change in comparison.improvements:
        print(f"  + {change.describe()}")
    for change in comparison.regressions:
        print(f"  ! {change.describe()}")
    for name in comparison.missing_scenarios:
        print(f"  ? scenario absent du banc courant : {name}")
    return 0 if comparison.acceptable else 1


def _cmd_doctrine(config: AppConfig) -> int:
    """Affiche les doctrines apprises, composition par composition."""
    store = CheckpointStore(config.path("memory", "models_dir"))
    profiles = list(store.all_profiles())
    if not profiles:
        print("Aucune doctrine apprise pour l'instant.")
        return 0
    for profile in profiles:
        stats = profile.stats
        print(f"Composition : {profile.fingerprint}")
        print(
            f"  {stats.sample_size} bataille(s), taux de victoire {stats.win_rate:.0%}, "
            f"forces restantes {stats.average_ally_remaining:.0%} en moyenne"
        )
        if profile.is_empty:
            print("  aucun ajustement retenu")
        else:
            for name, value in sorted(profile.adjustments.items()):
                print(f"  {name} = {value:g}")
            for reason in profile.rationale:
                print(f"    - {reason}")
        print()
    return 0


def _cmd_report(args: argparse.Namespace, config: AppConfig) -> int:
    directory = Path(config.path("telemetry", "reports_dir"))
    matches = sorted(directory.glob(f"{args.battle_id}*.md")) if directory.exists() else []
    if not matches:
        print(f"Aucun rapport trouve pour {args.battle_id!r} dans {directory}", file=sys.stderr)
        return 1
    print(matches[0].read_text(encoding="utf-8"))
    return 0


def _print_explanations(result: object, limit: int = 5) -> None:
    """Affiche les premieres decisions expliquees d'une bataille."""
    decisions = getattr(result, "decisions", ())
    for decision in list(decisions)[:limit]:
        print("  ---")
        for line in decision.explain().splitlines():
            print(f"  {line}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
