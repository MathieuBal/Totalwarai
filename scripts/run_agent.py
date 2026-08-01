#!/usr/bin/env python3
"""Fait tourner l'agent sur un pont, sans simulateur.

Aujourd'hui le seul pont disponible est le `MockBridge` : ce script montre la
boucle exacte que suivra l'integration au jeu — recevoir un etat, decider,
envoyer les actions, lire les accuses — et sert de banc d'essai pour le futur
adaptateur Lua.

    python scripts/run_agent.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.bridge.mock_bridge import MockBridge
from totalwar_ai.config import load_config
from totalwar_ai.domain.battle_state import BattlePhase, BattleState
from totalwar_ai.simulation.environment import SimulationEnvironment
from totalwar_ai.simulation.scenarios import get_scenario
from totalwar_ai.telemetry.battle_logger import configure_logging


def scripted_states(scenario_name: str, ticks: int) -> list[BattleState]:
    """Produit une suite d'etats a rejouer dans le pont factice."""
    scenario = get_scenario(scenario_name)
    environment = SimulationEnvironment(
        "mock-bridge", scenario.units, seed=scenario.seed, max_battle_seconds=float(ticks)
    )
    states = [environment.state()]
    for _ in range(ticks):
        step = environment.step()
        states.append(step.state)
        if step.finished:
            break
    return states


def main() -> int:
    configure_logging("INFO")
    agent = DeterministicTacticalAgent.from_config(load_config())
    bridge = MockBridge.from_states(scripted_states("balanced_clash", 40))

    while (state := bridge.receive_state()) is not None:
        turn = agent.decide(state)
        bridge.send_actions(turn.actions)
        for result in bridge.poll_results():
            if not result.accepted:
                print(f"action refusee : {result.action_id} — {result.error}")
        if turn.decisions:
            print(
                f"t={state.game_time:6.1f}s phase={state.phase.value:12} "
                f"{len(turn.decisions)} ordres, {len(turn.blocked)} bloques"
            )
        if state.phase is BattlePhase.FINISHED:
            break

    print(f"\nTotal : {len(bridge.sent_actions)} actions envoyees au pont.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
