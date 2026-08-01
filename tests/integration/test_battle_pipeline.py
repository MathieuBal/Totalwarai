"""Chaine complete : bataille -> journal -> rapport -> memoire -> rechargement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from totalwar_ai.config import AppConfig
from totalwar_ai.memory.repository import MemoryRepository
from totalwar_ai.simulation.runner import run_battle
from totalwar_ai.simulation.scenarios import get_scenario


def test_bataille_produit_journal_rapport_et_memoire(config: AppConfig, tmp_path: Path) -> None:
    scenario = get_scenario("ranged_defense")
    with MemoryRepository(config.path("memory", "database_path")) as memory:
        result = run_battle(
            scenario, config=config, seed=7, battle_id="pipeline-1", repository=memory
        )

        assert result.log_path is not None and result.log_path.exists()
        lines = result.log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) > 10
        first = json.loads(lines[0])
        assert first["type"] == "battle_started"
        assert first["battle_id"] == "pipeline-1"

        assert result.report_path is not None and result.report_path.exists()
        report = result.report_path.read_text(encoding="utf-8")
        assert "# Rapport de bataille" in report
        assert "Detail de la recompense" in report
        assert "Reproductibilite" in report

        summary = memory.get_battle("pipeline-1")
        assert summary is not None
        assert summary.scenario == "ranged_defense"
        assert summary.seed == 7
        assert summary.protocol_version
        assert summary.reward_version
        assert len(memory.battle_transitions("pipeline-1")) == len(result.episode.transitions)


def test_memoire_rechargee_a_la_session_suivante(config: AppConfig) -> None:
    """Critere du MVP : « recharger sa memoire au lancement suivant »."""
    scenario = get_scenario("cavalry_flank_threat")
    database = config.path("memory", "database_path")

    with MemoryRepository(database) as first_session:
        run_battle(scenario, config=config, seed=5, battle_id="session-1", repository=first_session)

    with MemoryRepository(database) as second_session:
        assert second_session.stats()["battles"] == 1
        result = run_battle(
            scenario, config=config, seed=5, battle_id="session-2", repository=second_session
        )
        assert second_session.stats()["battles"] == 2
        # La bataille precedente, de composition identique, est retrouvee.
        similar = second_session.find_similar(result.summary.army_fingerprint)
        assert {battle.battle_id for battle in similar} == {"session-1", "session-2"}

    assert result.report_path is not None
    assert "Batailles comparables" in result.report_path.read_text(encoding="utf-8")


def test_transitions_contiennent_etat_action_et_recompense(config: AppConfig) -> None:
    result = run_battle(
        get_scenario("artillery_assault"), config=config, seed=3, generate_report=False
    )
    transitions = result.episode.transitions
    assert transitions
    assert all(transition.state and transition.next_state for transition in transitions)
    assert transitions[-1].done
    assert any(transition.action for transition in transitions)
    assert sum(transition.reward for transition in transitions) != 0.0


def test_bataille_sans_memoire_ne_cree_pas_de_base(config: AppConfig) -> None:
    run_battle(get_scenario("outnumbered"), config=config, seed=1, generate_report=False)
    assert not Path(config.path("memory", "database_path")).exists()


def test_metriques_de_bataille(config: AppConfig) -> None:
    result = run_battle(
        get_scenario("balanced_clash"), config=config, seed=11, generate_report=False
    )
    metrics = result.summary.metrics
    for key in (
        "transitions",
        "orders_per_minute",
        "blocked_ratio",
        "allied_units_lost",
        "enemy_units_lost",
    ):
        assert key in metrics
    assert metrics["orders_per_minute"] <= 90.0 + 1e-6  # limite de securite respectee


def test_toutes_les_actions_sont_acquittees(config: AppConfig) -> None:
    """Aucun ordre ne doit disparaitre en silence."""
    result = run_battle(
        get_scenario("ranged_defense"), config=config, seed=2, generate_report=False
    )
    assert result.summary.actions_rejected == 0
    assert result.summary.actions_sent == len(result.decisions)


@pytest.mark.parametrize("scenario_name", ["balanced_clash", "ranged_defense", "outnumbered"])
def test_chaque_scenario_se_termine(config: AppConfig, scenario_name: str) -> None:
    result = run_battle(get_scenario(scenario_name), config=config, seed=17, generate_report=False)
    assert result.episode.transitions[-1].done
    assert result.summary.duration > 0.0
