"""Interface en ligne de commande.

`totalwar-ai <commande>` — quatre commandes couvrent le MVP :

* `scenarios` : lister les situations disponibles ;
* `simulate`  : jouer une ou plusieurs batailles ;
* `history`   : consulter la memoire persistante ;
* `report`    : reafficher le rapport d'une bataille.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from totalwar_ai import __version__
from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.config import AppConfig, ConfigError, load_config
from totalwar_ai.memory.replay_buffer import ReplayBuffer
from totalwar_ai.memory.repository import MemoryRepository
from totalwar_ai.simulation.runner import run_battle
from totalwar_ai.simulation.scenarios import ScenarioCatalog
from totalwar_ai.telemetry.battle_logger import configure_logging


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
    simulate.add_argument("--no-report", action="store_true", help="ne pas ecrire de rapport")
    simulate.add_argument(
        "--explain", action="store_true", help="afficher les decisions marquantes"
    )

    history = subparsers.add_parser("history", help="consulter la memoire persistante")
    history.add_argument("--limit", type=int, default=10, help="nombre de batailles a afficher")
    history.add_argument("--scenario", default=None, help="filtrer sur un scenario")

    report = subparsers.add_parser("report", help="afficher le rapport d'une bataille")
    report.add_argument("battle_id", help="identifiant complet ou prefixe")
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
