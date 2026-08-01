"""Memoire persistante : schema, enregistrement, recherche et rejeu."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.memory.models import BattleSummary, Episode, Transition
from totalwar_ai.memory.replay_buffer import ReplayBuffer
from totalwar_ai.memory.repository import SCHEMA_VERSION, MemoryRepository
from totalwar_ai.telemetry.events import Event, EventType


def _episode(
    battle_id: str, *, fingerprint: str = "ally:x|enemy:y", transitions: int = 3
) -> Episode:
    summary = BattleSummary(
        battle_id=battle_id,
        scenario="balanced_clash",
        seed=11,
        outcome=BattleOutcomeKind.VICTORY,
        duration=120.0,
        ally_remaining=0.7,
        enemy_remaining=0.1,
        total_reward=1234.5,
        actions_sent=42,
        army_fingerprint=fingerprint,
        created_at=100.0,
        metrics={"transitions": transitions},
    )
    return Episode(
        summary=summary,
        transitions=[
            Transition(
                battle_id=battle_id,
                sequence=index,
                game_time=float(index),
                state={"battle_id": battle_id, "units": [{"side": "ally"}]},
                action={"actions": []},
                reward=float(index),
                next_state={"battle_id": battle_id, "units": [{"side": "enemy"}]},
                done=index == transitions - 1,
            )
            for index in range(transitions)
        ],
        events=[Event(type=EventType.BATTLE_STARTED, battle_id=battle_id, game_time=0.0)],
    )


@pytest.fixture
def repository(tmp_path: Path) -> MemoryRepository:
    with MemoryRepository(tmp_path / "memory.sqlite3") as memory:
        yield memory


def test_schema_versionne(repository: MemoryRepository) -> None:
    assert repository.schema_version == SCHEMA_VERSION


def test_sauvegarde_puis_relecture(repository: MemoryRepository) -> None:
    repository.save_episode(_episode("b1"))
    summary = repository.get_battle("b1")
    assert summary is not None
    assert summary.outcome is BattleOutcomeKind.VICTORY
    assert summary.total_reward == pytest.approx(1234.5)
    assert len(repository.battle_transitions("b1")) == 3
    assert repository.battle_events("b1")[0]["type"] == "battle_started"


def test_rechargement_dans_une_nouvelle_session(tmp_path: Path) -> None:
    """Le critere du MVP : la memoire survit au redemarrage."""
    path = tmp_path / "memory.sqlite3"
    with MemoryRepository(path) as first:
        first.save_episode(_episode("b1"))
    with MemoryRepository(path) as second:
        assert second.get_battle("b1") is not None
        assert second.stats()["battles"] == 1


def test_enregistrement_idempotent(repository: MemoryRepository) -> None:
    repository.save_episode(_episode("b1"))
    repository.save_episode(_episode("b1"))
    assert repository.stats()["battles"] == 1
    assert len(repository.battle_transitions("b1")) == 3


def test_recherche_par_composition(repository: MemoryRepository) -> None:
    repository.save_episode(_episode("b1", fingerprint="A"))
    repository.save_episode(_episode("b2", fingerprint="B"))
    similar = repository.find_similar("A")
    assert [battle.battle_id for battle in similar] == ["b1"]


def test_statistiques(repository: MemoryRepository) -> None:
    repository.save_episode(_episode("b1"))
    repository.save_episode(_episode("b2"))
    stats = repository.stats()
    assert stats["battles"] == 2
    assert stats["victories"] == 2
    assert stats["win_rate"] == pytest.approx(1.0)
    assert stats["transitions"] == 6


def test_mode_compact_allege_les_etats(repository: MemoryRepository) -> None:
    repository.save_episode(_episode("b1"), keep_raw=False)
    stored = repository.battle_transitions("b1")[0]
    assert "units" not in stored.state
    assert stored.state["ally_count"] == 1


def test_replay_buffer_capacite_bornee() -> None:
    buffer = ReplayBuffer(capacity=2)
    for index in range(5):
        buffer.add(
            Transition(
                battle_id="b",
                sequence=index,
                game_time=float(index),
                state={},
                action=None,
                reward=0.0,
                next_state={},
            )
        )
    assert len(buffer) == 2
    assert buffer.is_full
    assert [item.sequence for item in buffer] == [3, 4]


def test_replay_buffer_echantillon_reproductible() -> None:
    buffer = ReplayBuffer(capacity=10)
    buffer.extend(
        Transition(
            battle_id="b",
            sequence=index,
            game_time=0.0,
            state={},
            action=None,
            reward=float(index),
            next_state={},
        )
        for index in range(10)
    )
    first = buffer.sample(3, random.Random(42))
    second = buffer.sample(3, random.Random(42))
    assert [item.sequence for item in first] == [item.sequence for item in second]


def test_replay_buffer_rechargement(repository: MemoryRepository) -> None:
    repository.save_episode(_episode("b1", transitions=4))
    buffer = ReplayBuffer(capacity=100)
    assert buffer.fill_from(repository) == 4
    assert len(buffer) == 4


def test_capacite_invalide() -> None:
    with pytest.raises(ValueError, match="strictement positive"):
        ReplayBuffer(capacity=0)
