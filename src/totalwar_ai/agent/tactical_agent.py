"""Agent tactique deterministe.

Assemble les briques : classification -> plan -> ordres -> securite. Il gere
aussi les trois frequences de decision decrites dans le README (surveillance
critique, tactique locale, plan general) et evite de re-emettre un ordre deja
actif.

C'est le mode de secours permanent du projet : quoi qu'apporte l'apprentissage
plus tard, cet agent doit rester utilisable seul.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from totalwar_ai.agent.explainability import Decision
from totalwar_ai.agent.grouping import GroupKind
from totalwar_ai.agent.planner import BattlePlan, Planner, PlannerSettings
from totalwar_ai.agent.safety_rules import SafetyEngine, SafetySettings
from totalwar_ai.agent.unit_classifier import UnitClassifier
from totalwar_ai.config import AppConfig, load_config
from totalwar_ai.domain.actions import AgentAction
from totalwar_ai.domain.battle_state import BattlePhase, BattleState
from totalwar_ai.domain.unit_state import PRECIOUS_ROLES, RANGED_ROLES


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """Ce que l'agent a decide pour un etat donne."""

    sequence: int
    game_time: float
    plan: BattlePlan | None = None
    decisions: tuple[Decision, ...] = ()
    blocked: tuple[Decision, ...] = ()
    suppressed: int = 0
    skipped_reason: str | None = None

    @property
    def actions(self) -> tuple[AgentAction, ...]:
        return tuple(decision.action for decision in self.decisions)

    @property
    def is_idle(self) -> bool:
        return not self.decisions and not self.blocked

    def explanations(self) -> list[str]:
        return [decision.explain() for decision in (*self.decisions, *self.blocked)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "game_time": self.game_time,
            "plan": self.plan.to_dict() if self.plan else None,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "blocked": [decision.to_dict() for decision in self.blocked],
            "suppressed": self.suppressed,
            "skipped_reason": self.skipped_reason,
        }


def default_safety_engine() -> SafetyEngine:
    """Moteur de securite avec l'ensemble des regles par defaut activees."""
    return SafetyEngine.from_settings(SafetySettings())


@dataclass
class DeterministicTacticalAgent:
    """Agent a base de regles, sans aucun modele appris."""

    planner: Planner = field(default_factory=Planner)
    safety: SafetyEngine = field(default_factory=default_safety_engine)
    classifier: UnitClassifier = field(default_factory=UnitClassifier.from_config)
    decision_interval: float = 2.0
    strategic_interval: float = 10.0
    confidence_threshold: float = 0.55

    battle_id: str | None = None
    plan: BattlePlan | None = None
    _last_decision_time: float | None = None
    _active_signatures: dict[str, tuple[Any, ...]] = field(default_factory=dict, repr=False)
    _blocked_signatures: set[tuple[Any, ...]] = field(default_factory=set, repr=False)

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> DeterministicTacticalAgent:
        """Construit l'agent depuis la configuration applicative."""
        resolved = config or load_config()
        agent_cfg = resolved.agent
        safety_cfg = resolved.safety
        return cls(
            planner=Planner(settings=PlannerSettings.from_config(agent_cfg, safety_cfg)),
            safety=SafetyEngine.from_config(safety_cfg),
            classifier=UnitClassifier.from_config(),
            decision_interval=float(agent_cfg.get("decision_interval_seconds", 2.0)),
            strategic_interval=float(agent_cfg.get("strategic_interval_seconds", 10.0)),
            confidence_threshold=float(agent_cfg.get("confidence_threshold", 0.55)),
        )

    # --- cycle de vie --------------------------------------------------------

    def reset(self, battle_id: str | None = None) -> None:
        """Prepare l'agent pour une nouvelle bataille."""
        self.battle_id = battle_id
        self.plan = None
        self._last_decision_time = None
        self._active_signatures.clear()
        self._blocked_signatures.clear()
        self.safety.reset()

    def trigger_emergency_stop(self) -> None:
        """Arret d'urgence : le joueur reprend la main immediatement."""
        self.safety.trigger_emergency_stop()

    def release_emergency_stop(self) -> None:
        self.safety.release_emergency_stop()

    @property
    def emergency_stopped(self) -> bool:
        return self.safety.emergency_stop

    # --- decision ------------------------------------------------------------

    def decide(self, raw_state: BattleState) -> AgentTurn:
        """Produit les ordres pour l'etat courant."""
        state = self.classifier.classify_state(raw_state)
        if self.battle_id != state.battle_id:
            self.reset(state.battle_id)

        if state.phase is BattlePhase.FINISHED:
            return AgentTurn(
                sequence=state.sequence,
                game_time=state.game_time,
                plan=self.plan,
                skipped_reason="bataille terminee",
            )

        if not self._should_decide(state):
            return AgentTurn(
                sequence=state.sequence,
                game_time=state.game_time,
                plan=self.plan,
                skipped_reason="cadence tactique non atteinte",
            )

        self._refresh_plan(state)
        plan = self.plan
        assert plan is not None  # garanti par _refresh_plan

        proposals = [
            decision
            for decision in self.planner.decisions_for(state, plan)
            if decision.confidence >= self.confidence_threshold
        ]
        rear = plan.anchor - plan.front_direction.scaled(self.planner.settings.reserve_offset)
        outcome = self.safety.filter(proposals, state, rear=rear)

        # Ordre volontaire : securite -> anti-repetition -> limite de debit.
        # Le budget d'ordres par minute ne doit jamais etre consomme par des
        # ordres redondants ou par des actions deja refusees.
        kept, suppressed = self._drop_duplicates(outcome.allowed)
        throttled = self.safety.throttle(kept, state.game_time)
        blocked, repeated = self._drop_repeated_blocks(outcome.blocked)

        self._last_decision_time = state.game_time
        return AgentTurn(
            sequence=state.sequence,
            game_time=state.game_time,
            plan=plan,
            decisions=throttled.allowed,
            blocked=(*blocked, *throttled.blocked),
            suppressed=suppressed + repeated,
        )

    # --- cadence -------------------------------------------------------------

    def _should_decide(self, state: BattleState) -> bool:
        """Trois frequences : deploiement, urgence, cadence tactique."""
        if state.phase is BattlePhase.DEPLOYMENT:
            return True
        if self._last_decision_time is None:
            return True
        if state.game_time - self._last_decision_time >= self.decision_interval:
            return True
        return self._has_critical_situation(state)

    def _has_critical_situation(self, state: BattleState) -> bool:
        """Surveillance haute frequence : tireurs menaces, commandement en peril."""
        radius = self.planner.settings.ranged_threat_radius
        for unit in state.allies():
            if unit.role in RANGED_ROLES and (unit.is_engaged or state.threats_to(unit, radius)):
                return True
            if unit.role in PRECIOUS_ROLES and unit.health_ratio < 0.4 and unit.is_engaged:
                return True
        return False

    def _refresh_plan(self, state: BattleState) -> None:
        """Recalcule le plan general a basse frequence, ou si la situation a change."""
        plan = self.plan
        needs_refresh = (
            plan is None
            or state.phase is BattlePhase.DEPLOYMENT
            or state.game_time - plan.created_at >= self.strategic_interval
            or self._plan_is_stale(plan, state)
        )
        if needs_refresh:
            self.plan = self.planner.build_plan(state)

    def _plan_is_stale(self, plan: BattlePlan, state: BattleState) -> bool:
        """Un plan devient caduc quand ses groupes ont perdu des unites."""
        for group in plan.groups.non_empty():
            if group.kind is GroupKind.RESERVE:
                continue
            if len(group.available_units(state)) != len(group.unit_ids):
                return True
        return False

    # --- anti-repetition -----------------------------------------------------

    def _drop_duplicates(self, decisions: Sequence[Decision]) -> tuple[list[Decision], int]:
        """Supprime les ordres identiques a ceux deja actifs.

        Re-envoyer sans cesse le meme ordre est penalise (`unnecessary_order_change`)
        et brouille la lecture des journaux.
        """
        kept: list[Decision] = []
        suppressed = 0
        for decision in decisions:
            signature = decision.action.signature()
            key = ",".join(sorted(decision.action.actor_ids))
            if self._active_signatures.get(key) == signature:
                suppressed += 1
                continue
            self._active_signatures[key] = signature
            kept.append(decision)
        return kept, suppressed

    def _drop_repeated_blocks(self, decisions: Sequence[Decision]) -> tuple[list[Decision], int]:
        """Ne signale un refus qu'une fois tant que la situation ne change pas.

        Le planificateur reproposera la meme action a chaque cycle ; la journaliser
        des centaines de fois noierait le rapport sans rien apprendre de plus.
        """
        kept: list[Decision] = []
        repeated = 0
        for decision in decisions:
            signature = (decision.blocked_by, decision.action.signature())
            if signature in self._blocked_signatures:
                repeated += 1
                continue
            self._blocked_signatures.add(signature)
            kept.append(decision)
        return kept, repeated
