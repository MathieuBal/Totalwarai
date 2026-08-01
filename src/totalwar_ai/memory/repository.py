"""Memoire persistante sur SQLite.

C'est ce qui permet a l'agent de « se souvenir » d'une session a l'autre :
resumes de batailles, transitions d'experience et evenements. Le schema est
versionne (`PRAGMA user_version`) pour pouvoir evoluer sans perdre l'historique.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any

from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.memory.models import BattleSummary, Episode, Transition
from totalwar_ai.telemetry.events import Event

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS battles (
    battle_id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL DEFAULT '',
    seed INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL DEFAULT 'unknown',
    duration REAL NOT NULL DEFAULT 0.0,
    ally_remaining REAL NOT NULL DEFAULT 0.0,
    enemy_remaining REAL NOT NULL DEFAULT 0.0,
    total_reward REAL NOT NULL DEFAULT 0.0,
    actions_sent INTEGER NOT NULL DEFAULT 0,
    actions_blocked INTEGER NOT NULL DEFAULT 0,
    actions_rejected INTEGER NOT NULL DEFAULT 0,
    agent_mode TEXT NOT NULL DEFAULT '',
    protocol_version TEXT NOT NULL DEFAULT '',
    reward_version TEXT NOT NULL DEFAULT '',
    army_fingerprint TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0.0,
    metrics TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id TEXT NOT NULL REFERENCES battles(battle_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    game_time REAL NOT NULL,
    state TEXT NOT NULL,
    action TEXT,
    reward REAL NOT NULL DEFAULT 0.0,
    next_state TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id TEXT NOT NULL REFERENCES battles(battle_id) ON DELETE CASCADE,
    game_time REAL NOT NULL DEFAULT 0.0,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_transitions_battle ON transitions(battle_id, sequence);
CREATE INDEX IF NOT EXISTS idx_events_battle ON events(battle_id, game_time);
CREATE INDEX IF NOT EXISTS idx_battles_fingerprint ON battles(army_fingerprint);
"""


class MemoryRepository:
    """Acces a la base de memoire.

    Utilisable comme gestionnaire de contexte :

    >>> with MemoryRepository(path) as memory:  # doctest: +SKIP
    ...     memory.save_episode(episode)
    """

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    # --- cycle de vie --------------------------------------------------------

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> MemoryRepository:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection:
            yield self._connection

    def _migrate(self) -> None:
        """Cree ou met a niveau le schema."""
        current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if current >= SCHEMA_VERSION:
            return
        with self._connection:
            if current < 1:
                self._connection.executescript(_SCHEMA_V1)
            self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @property
    def schema_version(self) -> int:
        return int(self._connection.execute("PRAGMA user_version").fetchone()[0])

    # --- ecriture ------------------------------------------------------------

    def save_episode(self, episode: Episode, *, keep_raw: bool = True) -> None:
        """Enregistre un episode complet (ecrase une bataille de meme identifiant)."""
        summary = episode.summary
        transitions = (
            episode.transitions if keep_raw else [item.compact() for item in episode.transitions]
        )
        with self._transaction() as connection:
            connection.execute("DELETE FROM battles WHERE battle_id = ?", (summary.battle_id,))
            connection.execute(
                """
                INSERT INTO battles (
                    battle_id, scenario, seed, outcome, duration, ally_remaining,
                    enemy_remaining, total_reward, actions_sent, actions_blocked,
                    actions_rejected, agent_mode, protocol_version, reward_version,
                    army_fingerprint, created_at, metrics
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    summary.battle_id,
                    summary.scenario,
                    summary.seed,
                    summary.outcome.value,
                    summary.duration,
                    summary.ally_remaining,
                    summary.enemy_remaining,
                    summary.total_reward,
                    summary.actions_sent,
                    summary.actions_blocked,
                    summary.actions_rejected,
                    summary.agent_mode,
                    summary.protocol_version,
                    summary.reward_version,
                    summary.army_fingerprint,
                    summary.created_at,
                    json.dumps(summary.metrics, ensure_ascii=False),
                ),
            )
            connection.executemany(
                """
                INSERT INTO transitions (
                    battle_id, sequence, game_time, state, action,
                    reward, next_state, done, metadata
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        transition.battle_id,
                        transition.sequence,
                        transition.game_time,
                        json.dumps(transition.state, ensure_ascii=False),
                        json.dumps(transition.action, ensure_ascii=False)
                        if transition.action
                        else None,
                        transition.reward,
                        json.dumps(transition.next_state, ensure_ascii=False),
                        int(transition.done),
                        json.dumps(transition.metadata, ensure_ascii=False),
                    )
                    for transition in transitions
                ],
            )
            connection.executemany(
                "INSERT INTO events (battle_id, game_time, type, payload) VALUES (?,?,?,?)",
                [
                    (
                        event.battle_id,
                        event.game_time,
                        event.type.value,
                        json.dumps(event.to_dict()["payload"], ensure_ascii=False),
                    )
                    for event in episode.events
                ],
            )

    # --- lecture -------------------------------------------------------------

    def list_battles(self, limit: int = 20, *, scenario: str | None = None) -> list[BattleSummary]:
        """Batailles les plus recentes d'abord."""
        query = "SELECT * FROM battles"
        params: list[Any] = []
        if scenario:
            query += " WHERE scenario = ?"
            params.append(scenario)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._connection.execute(query, params).fetchall()
        return [_summary_from_row(row) for row in rows]

    def get_battle(self, battle_id: str) -> BattleSummary | None:
        row = self._connection.execute(
            "SELECT * FROM battles WHERE battle_id = ?", (battle_id,)
        ).fetchone()
        return _summary_from_row(row) if row else None

    def battle_transitions(self, battle_id: str, limit: int | None = None) -> list[Transition]:
        query = "SELECT * FROM transitions WHERE battle_id = ? ORDER BY sequence"
        params: list[Any] = [battle_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._connection.execute(query, params).fetchall()
        return [_transition_from_row(row) for row in rows]

    def battle_events(self, battle_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT game_time, type, payload FROM events WHERE battle_id = ? ORDER BY id",
            (battle_id,),
        ).fetchall()
        return [
            {
                "game_time": row["game_time"],
                "type": row["type"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def find_similar(self, army_fingerprint: str, limit: int = 5) -> list[BattleSummary]:
        """Batailles ayant oppose des compositions identiques.

        Base de l'adaptation : avant de rejouer une situation, l'agent peut
        savoir comment elle s'est terminee la derniere fois.
        """
        rows = self._connection.execute(
            """
            SELECT * FROM battles
            WHERE army_fingerprint = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (army_fingerprint, limit),
        ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def iter_transitions(self, limit: int | None = None) -> Iterator[Transition]:
        """Parcourt les transitions les plus recentes (alimentation du replay buffer)."""
        query = "SELECT * FROM transitions ORDER BY id DESC"
        params: Sequence[Any] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        for row in self._connection.execute(query, params):
            yield _transition_from_row(row)

    def stats(self) -> dict[str, Any]:
        """Statistiques globales affichees par le CLI."""
        row = self._connection.execute(
            """
            SELECT
                COUNT(*) AS battles,
                COALESCE(SUM(outcome = 'victory'), 0) AS victories,
                COALESCE(SUM(outcome = 'defeat'), 0) AS defeats,
                COALESCE(SUM(outcome = 'draw'), 0) AS draws,
                COALESCE(AVG(total_reward), 0.0) AS average_reward,
                COALESCE(AVG(ally_remaining), 0.0) AS average_ally_remaining
            FROM battles
            """
        ).fetchone()
        transitions = self._connection.execute("SELECT COUNT(*) FROM transitions").fetchone()[0]
        battles = int(row["battles"])
        return {
            "battles": battles,
            "victories": int(row["victories"]),
            "defeats": int(row["defeats"]),
            "draws": int(row["draws"]),
            "win_rate": (int(row["victories"]) / battles) if battles else 0.0,
            "average_reward": float(row["average_reward"]),
            "average_ally_remaining": float(row["average_ally_remaining"]),
            "transitions": int(transitions),
            "schema_version": self.schema_version,
        }


def _summary_from_row(row: sqlite3.Row) -> BattleSummary:
    return BattleSummary(
        battle_id=row["battle_id"],
        scenario=row["scenario"],
        seed=int(row["seed"]),
        outcome=BattleOutcomeKind(row["outcome"]),
        duration=float(row["duration"]),
        ally_remaining=float(row["ally_remaining"]),
        enemy_remaining=float(row["enemy_remaining"]),
        total_reward=float(row["total_reward"]),
        actions_sent=int(row["actions_sent"]),
        actions_blocked=int(row["actions_blocked"]),
        actions_rejected=int(row["actions_rejected"]),
        agent_mode=row["agent_mode"],
        protocol_version=row["protocol_version"],
        reward_version=row["reward_version"],
        army_fingerprint=row["army_fingerprint"],
        created_at=float(row["created_at"]),
        metrics=json.loads(row["metrics"]),
    )


def _transition_from_row(row: sqlite3.Row) -> Transition:
    return Transition(
        battle_id=row["battle_id"],
        sequence=int(row["sequence"]),
        game_time=float(row["game_time"]),
        state=json.loads(row["state"]),
        action=json.loads(row["action"]) if row["action"] else None,
        reward=float(row["reward"]),
        next_state=json.loads(row["next_state"]),
        done=bool(row["done"]),
        metadata=json.loads(row["metadata"]),
    )


def save_events(repository: MemoryRepository, battle_id: str, events: Iterable[Event]) -> None:
    """Ajoute des evenements a une bataille deja enregistree."""
    with repository._transaction() as connection:
        connection.executemany(
            "INSERT INTO events (battle_id, game_time, type, payload) VALUES (?,?,?,?)",
            [
                (
                    battle_id,
                    event.game_time,
                    event.type.value,
                    json.dumps(event.to_dict()["payload"], ensure_ascii=False),
                )
                for event in events
            ],
        )
