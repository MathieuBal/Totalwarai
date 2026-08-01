"""L'historique influence-t-il vraiment la bataille suivante ?

C'est le livrable de la Phase 4 du README. Un test qui verifierait seulement
que le profil est calcule ne prouverait rien : on verifie ici que les ordres
emis changent, et qu'on peut revenir au comportement de reference.
"""

from __future__ import annotations

from pathlib import Path

from totalwar_ai.agent.doctrine import apply_to_planner
from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.config import AppConfig
from totalwar_ai.learning.checkpoints import CheckpointStore
from totalwar_ai.memory.repository import MemoryRepository
from totalwar_ai.simulation.runner import run_battle
from totalwar_ai.simulation.scenarios import get_scenario

SCENARIO = "outnumbered"


def _play(config: AppConfig, memory: MemoryRepository | None, seed: int, battle_id: str, **kwargs):  # type: ignore[no-untyped-def]
    return run_battle(
        get_scenario(SCENARIO),
        config=config,
        seed=seed,
        battle_id=battle_id,
        repository=memory,
        generate_report=False,
        **kwargs,
    )


def test_la_doctrine_s_ajuste_apres_plusieurs_batailles(config: AppConfig) -> None:
    with MemoryRepository(config.path("memory", "database_path")) as memory:
        first = _play(config, memory, 1, "adapt-1")
        assert first.profile.is_empty  # aucun historique au depart

        second = _play(config, memory, 2, "adapt-2")
        assert second.profile.is_empty  # une seule bataille : pas assez

        third = _play(config, memory, 3, "adapt-3")
        assert not third.profile.is_empty
        assert third.profile.stats.sample_size >= 2
        assert third.profile.rationale


def test_l_adaptation_change_reellement_les_ordres(config: AppConfig) -> None:
    """Meme scenario, meme graine : seul l'historique differe."""
    with MemoryRepository(config.path("memory", "database_path")) as memory:
        _play(config, memory, 1, "hist-1")
        _play(config, memory, 2, "hist-2")

        adapte = _play(config, memory, 7, "avec-doctrine")
        reference = _play(config, memory, 7, "sans-doctrine", adapt=False)

    assert not adapte.profile.is_empty
    assert adapte.summary.actions_sent != reference.summary.actions_sent or (
        adapte.summary.total_reward != reference.summary.total_reward
    )


def test_no_adapt_restaure_le_comportement_de_reference(config: AppConfig) -> None:
    """Sans memoire et avec `adapt=False`, on doit retrouver exactement la meme bataille."""
    sans_memoire = _play(config, None, 5, "ref-1")
    with MemoryRepository(config.path("memory", "database_path")) as memory:
        _play(config, memory, 1, "bruit-1")
        _play(config, memory, 2, "bruit-2")
        avec_memoire_sans_adaptation = _play(config, memory, 5, "ref-2", adapt=False)

    assert avec_memoire_sans_adaptation.summary.total_reward == sans_memoire.summary.total_reward
    assert avec_memoire_sans_adaptation.summary.duration == sans_memoire.summary.duration


def test_la_doctrine_est_enregistree_sur_disque(config: AppConfig) -> None:
    with MemoryRepository(config.path("memory", "database_path")) as memory:
        _play(config, memory, 1, "ckpt-1")
        _play(config, memory, 2, "ckpt-2")
        result = _play(config, memory, 3, "ckpt-3")

    store = CheckpointStore(config.path("memory", "models_dir"))
    restored = store.load(result.summary.army_fingerprint)
    assert restored is not None
    assert restored.adjustments == result.profile.adjustments
    assert list(store.all_profiles())


def test_la_doctrine_survit_au_redemarrage(config: AppConfig) -> None:
    """Une nouvelle session doit repartir de la doctrine deja apprise."""
    database = config.path("memory", "database_path")
    with MemoryRepository(database) as first_session:
        _play(config, first_session, 1, "session-1")
        _play(config, first_session, 2, "session-2")

    # Nouvelle session : nouvel agent, nouvelle connexion.
    with MemoryRepository(database) as second_session:
        result = _play(config, second_session, 4, "session-3")

    assert not result.profile.is_empty
    agent = DeterministicTacticalAgent.from_config(config)
    ajuste = apply_to_planner(agent.planner.settings, result.profile)
    assert ajuste != agent.planner.settings


def test_le_rapport_explique_l_adaptation(config: AppConfig) -> None:
    with MemoryRepository(config.path("memory", "database_path")) as memory:
        _play(config, memory, 1, "rapport-1")
        _play(config, memory, 2, "rapport-2")
        result = run_battle(
            get_scenario(SCENARIO),
            config=config,
            seed=3,
            battle_id="rapport-3",
            repository=memory,
        )

    assert result.report_path is not None
    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "## Ce que la memoire a change" in report
    assert "## Deroulement" in report
    for reason in result.profile.rationale:
        assert reason in report


def test_apprentissage_desactive_ne_produit_aucune_doctrine(tmp_path: Path) -> None:
    from totalwar_ai.config import load_config

    config = load_config(data_dir=tmp_path, overrides={"agent": {"allow_learning": False}})
    with MemoryRepository(config.path("memory", "database_path")) as memory:
        _play(config, memory, 1, "off-1")
        _play(config, memory, 2, "off-2")
        result = _play(config, memory, 3, "off-3")

    assert result.profile.is_empty
    assert result.profile.stats.sample_size == 0
