"""Boucle de bataille complete.

Point de jonction du MVP : scenario -> simulateur -> agent -> securite ->
telemetrie -> recompense -> memoire -> rapport. C'est la fonction appelee par le
CLI et par les tests de scenario.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from totalwar_ai.agent.doctrine import baseline_values
from totalwar_ai.agent.explainability import Decision
from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.bridge.protocol import PROTOCOL_VERSION
from totalwar_ai.config import AppConfig, load_config
from totalwar_ai.domain.battle_state import BattleOutcomeKind, BattleState
from totalwar_ai.domain.unit_state import RANGED_ROLES, Side
from totalwar_ai.learning.adaptation import DEFAULT_MIN_SAMPLES, DoctrineProfile, derive_profile
from totalwar_ai.learning.checkpoints import CheckpointStore
from totalwar_ai.learning.rewards import RewardBreakdown, RewardCalculator
from totalwar_ai.memory.models import BattleSummary, Episode, Transition
from totalwar_ai.memory.repository import MemoryRepository
from totalwar_ai.simulation.environment import SimulationEnvironment
from totalwar_ai.simulation.scenarios import Scenario
from totalwar_ai.simulation.unit_templates import SimulationParameters
from totalwar_ai.telemetry.battle_logger import BattleLogger
from totalwar_ai.telemetry.events import Event, EventType
from totalwar_ai.telemetry.report import ReportContext, write_report


@dataclass(frozen=True, slots=True)
class BattleResult:
    """Ce que retourne une bataille simulee."""

    episode: Episode
    outcome: BattleOutcomeKind
    reward: RewardBreakdown
    decisions: tuple[Decision, ...] = ()
    blocked: tuple[Decision, ...] = ()
    report_path: Path | None = None
    log_path: Path | None = None
    states: tuple[BattleState, ...] = field(default=(), repr=False)
    profile: DoctrineProfile = field(default_factory=DoctrineProfile)
    strength_series: tuple[tuple[float, float, float], ...] = field(default=(), repr=False)

    @property
    def summary(self) -> BattleSummary:
        return self.episode.summary

    @property
    def battle_id(self) -> str:
        return self.episode.battle_id


def run_battle(
    scenario: Scenario,
    *,
    agent: DeterministicTacticalAgent | None = None,
    config: AppConfig | None = None,
    seed: int | None = None,
    battle_id: str | None = None,
    repository: MemoryRepository | None = None,
    generate_report: bool | None = None,
    keep_states: bool = False,
    adapt: bool | None = None,
) -> BattleResult:
    """Joue un scenario de bout en bout.

    `repository` est optionnel : sans lui, la bataille se deroule normalement
    mais rien n'est memorise (mode `--no-memory` du CLI), et aucune doctrine
    apprise n'est appliquee — l'agent reste alors strictement deterministe.

    `adapt` force ou interdit l'application de la doctrine apprise ; par defaut
    elle suit `memory.apply_learned_doctrine`.
    """
    resolved_config = config or load_config()
    telemetry = resolved_config.telemetry
    memory_cfg = resolved_config.memory
    simulation_cfg = resolved_config.simulation

    resolved_seed = scenario.seed if seed is None else seed
    resolved_id = battle_id or str(uuid.uuid4())
    tactical_agent = agent or DeterministicTacticalAgent.from_config(resolved_config)
    tactical_agent.reset(resolved_id)
    rewards = RewardCalculator()

    fingerprint = scenario.fingerprint()
    history: list[BattleSummary] = []
    if repository is not None:
        history = repository.find_similar(
            fingerprint, limit=int(memory_cfg.get("history_depth", 10))
        )
    profile = _prepare_doctrine(
        tactical_agent,
        fingerprint,
        history,
        config=resolved_config,
        adapt=adapt,
    )

    environment = SimulationEnvironment(
        battle_id=resolved_id,
        specs=scenario.units,
        seed=resolved_seed,
        parameters=SimulationParameters.load(),
        tick_seconds=float(simulation_cfg.get("tick_seconds", 0.5)),
        max_battle_seconds=min(
            float(simulation_cfg.get("max_battle_seconds", 900.0)),
            scenario.max_battle_seconds,
        ),
        field_radius=float(simulation_cfg.get("field_width", 400.0)),
    )

    logger = BattleLogger(
        resolved_id,
        directory=resolved_config.path("telemetry", "battles_dir"),
        write_jsonl=bool(telemetry.get("write_jsonl", True)),
    )

    all_decisions: list[Decision] = []
    all_blocked: list[Decision] = []
    transitions: list[Transition] = []
    states: list[BattleState] = []
    strength_series: list[tuple[float, float, float]] = []
    total_reward = RewardBreakdown()
    actions_rejected = 0

    try:
        state = environment.state()
        logger.battle_started(
            state,
            scenario=scenario.name,
            seed=resolved_seed,
            protocol_version=PROTOCOL_VERSION,
            reward_version=rewards.version,
        )
        if keep_states:
            states.append(state)

        while not environment.finished:
            logger.state_received(state)
            turn = tactical_agent.decide(state)
            if turn.plan is not None:
                logger.plan_selected(state.game_time, turn.plan.to_dict())
            logger.decisions(state.game_time, turn.decisions, turn.blocked)

            results = environment.apply_actions(turn.actions)
            for result in results:
                if not result.accepted:
                    actions_rejected += 1
                    logger.action_rejected(state.game_time, result.action_id, result.error)

            step = environment.step()
            logger.emit_many(step.events)
            events = list(step.events) + _safety_events(resolved_id, state.game_time, turn.blocked)

            reward = rewards.step_reward(state, step.state, events)
            total_reward = total_reward.merged(reward)
            if reward.components:
                logger.reward_assigned(step.state.game_time, reward.total, reward.components)

            transitions.append(
                Transition.build(
                    state,
                    step.state,
                    actions=[action.to_dict() for action in turn.actions],
                    reward=reward.total,
                    done=step.finished,
                    metadata={
                        "posture": turn.plan.posture.value if turn.plan else None,
                        "blocked": len(turn.blocked),
                        "suppressed": turn.suppressed,
                    },
                )
            )
            all_decisions.extend(turn.decisions)
            all_blocked.extend(turn.blocked)
            strength_series.append(
                (
                    step.state.game_time,
                    environment.remaining_share(Side.ALLY),
                    environment.remaining_share(Side.ENEMY),
                )
            )
            state = step.state
            if keep_states:
                states.append(state)

        terminal = rewards.terminal_reward(environment.outcome)
        total_reward = total_reward.merged(terminal)

        summary = BattleSummary(
            battle_id=resolved_id,
            scenario=scenario.name,
            seed=resolved_seed,
            outcome=environment.outcome,
            duration=environment.game_time,
            ally_remaining=environment.remaining_share(Side.ALLY),
            enemy_remaining=environment.remaining_share(Side.ENEMY),
            total_reward=total_reward.total,
            actions_sent=len(all_decisions),
            actions_blocked=len(all_blocked),
            actions_rejected=actions_rejected,
            agent_mode=str(resolved_config.agent.get("mode", "deterministic")),
            protocol_version=PROTOCOL_VERSION,
            reward_version=rewards.version,
            army_fingerprint=scenario.fingerprint(),
            metrics=_metrics(
                transitions=len(transitions),
                decisions=all_decisions,
                blocked=all_blocked,
                events=logger.events,
                duration=environment.game_time,
            ),
        )
        episode = Episode(summary=summary, transitions=transitions, events=list(logger.events))

        if repository is not None:
            repository.save_episode(
                episode, keep_raw=bool(memory_cfg.get("keep_raw_battles", True))
            )
            logger.episode_saved(
                environment.game_time,
                transitions=len(transitions),
                database=str(resolved_config.path("memory", "database_path")),
            )

        report_path: Path | None = None
        should_report = (
            generate_report
            if generate_report is not None
            else bool(telemetry.get("generate_report", True))
        )
        if should_report:
            report_path = write_report(
                ReportContext(
                    summary=summary,
                    events=list(logger.events),
                    decisions=all_decisions,
                    blocked=all_blocked,
                    reward=total_reward,
                    history=history[:3],
                    profile=profile,
                    strength_series=tuple(strength_series),
                ),
                resolved_config.path("telemetry", "reports_dir"),
            )

        return BattleResult(
            episode=episode,
            outcome=environment.outcome,
            reward=total_reward,
            decisions=tuple(all_decisions),
            blocked=tuple(all_blocked),
            report_path=report_path,
            log_path=logger.path,
            states=tuple(states),
            profile=profile,
            strength_series=tuple(strength_series),
        )
    finally:
        logger.close()


def _prepare_doctrine(
    tactical_agent: DeterministicTacticalAgent,
    fingerprint: str,
    history: Sequence[BattleSummary],
    *,
    config: AppConfig,
    adapt: bool | None,
) -> DoctrineProfile:
    """Deduit, enregistre et applique la doctrine tiree de l'historique.

    Sans historique, le profil est vide et l'agent garde ses reglages par defaut :
    c'est le comportement de reference, et celui de tous les tests deterministes.
    """
    memory_cfg = config.memory
    agent_cfg = config.agent
    profile = DoctrineProfile(fingerprint=fingerprint)
    if not history or not bool(agent_cfg.get("allow_learning", True)):
        return profile

    profile = derive_profile(
        fingerprint,
        history,
        baseline=baseline_values(tactical_agent.planner.settings),
        min_samples=int(memory_cfg.get("min_battles_for_adaptation", DEFAULT_MIN_SAMPLES)),
    )

    store = CheckpointStore(config.path("memory", "models_dir"))
    store.save(profile)

    should_apply = (
        adapt if adapt is not None else bool(memory_cfg.get("apply_learned_doctrine", True))
    )
    if should_apply and not profile.is_empty:
        tactical_agent.apply_doctrine(profile)
    return profile


def _safety_events(battle_id: str, game_time: float, blocked: tuple[Decision, ...]) -> list[Event]:
    """Chaque blocage de securite est un fait penalise par le bareme."""
    return [
        Event(
            type=EventType.ACTION_BLOCKED_BY_SAFETY,
            battle_id=battle_id,
            game_time=game_time,
            payload={"rule": decision.blocked_by, "type": decision.action.type.value},
        )
        for decision in blocked
    ]


def _metrics(
    *,
    transitions: int,
    decisions: list[Decision],
    blocked: list[Decision],
    events: list[Event],
    duration: float,
) -> dict[str, Any]:
    """Metriques de suivi listees dans le README (evaluation et regressions)."""
    minutes = max(duration / 60.0, 1e-6)
    destroyed_allies = sum(
        1
        for event in events
        if event.type is EventType.UNIT_DESTROYED and event.payload.get("side") == Side.ALLY.value
    )
    destroyed_enemies = sum(
        1
        for event in events
        if event.type is EventType.UNIT_DESTROYED and event.payload.get("side") == Side.ENEMY.value
    )
    proposed = len(decisions) + len(blocked)
    return {
        "transitions": transitions,
        "orders_per_minute": round(len(decisions) / minutes, 2),
        "blocked_ratio": round(len(blocked) / proposed, 3) if proposed else 0.0,
        "allied_units_lost": destroyed_allies,
        "enemy_units_lost": destroyed_enemies,
        "flank_attacks": sum(1 for event in events if event.type is EventType.FLANK_ATTACK),
        "allied_routs": sum(
            1
            for event in events
            if event.type is EventType.UNIT_ROUTED and event.payload.get("side") == Side.ALLY.value
        ),
        # Combien de fois nos tireurs ont ete pris au corps a corps : c'est le
        # signal qui pousse l'adaptation a les replier plus tot.
        "allied_ranged_engaged": sum(
            1
            for event in events
            if event.type is EventType.UNIT_ENGAGED
            and event.payload.get("side") == Side.ALLY.value
            and event.role in RANGED_ROLES
        ),
    }
