"""Regles de securite : le filet qui empeche les ordres manifestement absurdes.

Ce module est independant du planificateur. Il ne cherche pas a bien jouer, il
cherche a interdire ce qu'un joueur humain ne ferait jamais : envoyer
l'artillerie charger, laisser des archers en melee, sacrifier le seigneur, ou
noyer le jeu sous les ordres. L'arret d'urgence du README passe aussi par ici.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from totalwar_ai.agent.explainability import Decision
from totalwar_ai.domain.actions import ActionType, AgentAction
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import (
    PRECIOUS_ROLES,
    RANGED_ROLES,
    Side,
    UnitRole,
    UnitState,
)

#: Actions toujours autorisees pour une unite en danger : elles l'en sortent.
ESCAPE_ACTIONS: frozenset[ActionType] = frozenset({ActionType.RETREAT, ActionType.DISENGAGE})


@dataclass(frozen=True, slots=True)
class SafetySettings:
    """Seuils de securite, issus du bloc `safety` de la configuration."""

    max_orders_per_minute: int = 90
    protect_lord: bool = True
    prevent_ranged_melee: bool = True
    prevent_artillery_charge: bool = True
    ranged_threat_radius: float = 70.0
    min_local_power_ratio_for_charge: float = 0.6
    lord_retreat_health_ratio: float = 0.35
    max_pursuit_distance: float = 150.0
    local_power_radius: float = 60.0

    @classmethod
    def from_config(cls, safety: Mapping[str, Any]) -> SafetySettings:
        defaults = cls()
        return cls(
            max_orders_per_minute=int(
                safety.get("max_orders_per_minute", defaults.max_orders_per_minute)
            ),
            protect_lord=bool(safety.get("protect_lord", defaults.protect_lord)),
            prevent_ranged_melee=bool(
                safety.get("prevent_ranged_melee", defaults.prevent_ranged_melee)
            ),
            prevent_artillery_charge=bool(
                safety.get("prevent_artillery_charge", defaults.prevent_artillery_charge)
            ),
            ranged_threat_radius=float(
                safety.get("ranged_threat_radius", defaults.ranged_threat_radius)
            ),
            min_local_power_ratio_for_charge=float(
                safety.get(
                    "min_local_power_ratio_for_charge", defaults.min_local_power_ratio_for_charge
                )
            ),
            lord_retreat_health_ratio=float(
                safety.get("lord_retreat_health_ratio", defaults.lord_retreat_health_ratio)
            ),
            max_pursuit_distance=float(
                safety.get("max_pursuit_distance", defaults.max_pursuit_distance)
            ),
        )


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    """Refus d'une action, avec eventuellement l'ordre de substitution."""

    rule: str
    reason: str
    replacement: AgentAction | None = None


class SafetyRule(ABC):
    """Regle unitaire appliquee a chaque action proposee."""

    name: str = "regle"

    @abstractmethod
    def check(
        self, action: AgentAction, state: BattleState, rear: Vector3
    ) -> SafetyVerdict | None:
        """Renvoie un verdict si l'action doit etre refusee, sinon `None`."""

    # Aides communes aux regles concretes.

    @staticmethod
    def actors(action: AgentAction, state: BattleState) -> list[UnitState]:
        resolved = (state.unit(unit_id) for unit_id in action.actor_ids)
        return [unit for unit in resolved if unit is not None]


def retreat_action(units: Sequence[str], rear: Vector3, reason: str) -> AgentAction:
    """Ordre de repli standard, utilise par toutes les substitutions."""
    return AgentAction(
        type=ActionType.RETREAT,
        actor_ids=tuple(units),
        parameters={"destination": rear},
        reason=reason,
        confidence=0.95,
    )


class ArtilleryChargeRule(SafetyRule):
    """L'artillerie ne charge pas — jamais (regle 1 du Ticket 001)."""

    name = "artillerie_ne_charge_pas"

    def check(self, action: AgentAction, state: BattleState, rear: Vector3) -> SafetyVerdict | None:
        if not action.is_charge:
            return None
        guilty = [unit for unit in self.actors(action, state) if unit.role is UnitRole.ARTILLERY]
        if not guilty:
            return None
        names = ", ".join(unit.id for unit in guilty)
        return SafetyVerdict(
            rule=self.name,
            reason=f"l'artillerie ({names}) ne doit pas etre envoyee au contact",
            replacement=AgentAction(
                type=ActionType.HOLD_POSITION,
                actor_ids=tuple(unit.id for unit in guilty),
                reason="l'artillerie reste en batterie",
                confidence=0.95,
            ),
        )


class RangedMeleeRule(SafetyRule):
    """Une unite de tir menacee au corps a corps se replie (regle 2 du Ticket 001)."""

    name = "tireurs_hors_melee"

    def __init__(self, threat_radius: float) -> None:
        self.threat_radius = threat_radius

    def check(self, action: AgentAction, state: BattleState, rear: Vector3) -> SafetyVerdict | None:
        if action.type in ESCAPE_ACTIONS:
            return None
        endangered = [
            unit
            for unit in self.actors(action, state)
            if unit.role in RANGED_ROLES
            and (unit.is_engaged or state.threats_to(unit, self.threat_radius))
        ]
        if not endangered:
            return None
        names = ", ".join(unit.id for unit in endangered)
        return SafetyVerdict(
            rule=self.name,
            reason=f"unite(s) de tir menacee(s) au corps a corps : {names}",
            replacement=retreat_action(
                [unit.id for unit in endangered], rear, "se degager de la melee"
            ),
        )


class SuicidalChargeRule(SafetyRule):
    """Refuse une charge ou le rapport de forces local est perdu d'avance."""

    name = "pas_de_charge_suicidaire"

    def __init__(self, min_ratio: float, radius: float) -> None:
        self.min_ratio = min_ratio
        self.radius = radius

    def check(self, action: AgentAction, state: BattleState, rear: Vector3) -> SafetyVerdict | None:
        if not action.is_charge:
            return None
        target_id = action.target_id
        target = state.unit(target_id) if target_id else None
        if target is None:
            return None
        attackers = self.actors(action, state)
        if not attackers:
            return None

        friendly = sum(
            unit.effective_strength
            for unit in state.units_within(target.position, self.radius, state.allies())
        )
        friendly += sum(
            unit.effective_strength
            for unit in attackers
            if unit.position.distance_2d(target.position) > self.radius
        )
        hostile = sum(
            unit.effective_strength
            for unit in state.units_within(target.position, self.radius, state.enemies())
        )
        if hostile <= 1e-6:
            return None
        ratio = friendly / hostile
        if ratio >= self.min_ratio:
            return None
        return SafetyVerdict(
            rule=self.name,
            reason=(
                f"rapport de forces local defavorable autour de {target.id} "
                f"({ratio:.2f} < {self.min_ratio:.2f})"
            ),
            replacement=retreat_action(
                [unit.id for unit in attackers], rear, "eviter un engagement perdu d'avance"
            ),
        )


class LordProtectionRule(SafetyRule):
    """Retire le seigneur et les heros du combat lorsqu'ils sont trop entames."""

    name = "protection_du_seigneur"

    def __init__(self, health_threshold: float) -> None:
        self.health_threshold = health_threshold

    def check(self, action: AgentAction, state: BattleState, rear: Vector3) -> SafetyVerdict | None:
        if action.type in ESCAPE_ACTIONS:
            return None
        fragile = [
            unit
            for unit in self.actors(action, state)
            if unit.role in PRECIOUS_ROLES and unit.health_ratio < self.health_threshold
        ]
        if not fragile:
            return None
        names = ", ".join(f"{unit.id} ({unit.health_ratio:.0%})" for unit in fragile)
        return SafetyVerdict(
            rule=self.name,
            reason=f"commandement trop entame pour rester au contact : {names}",
            replacement=retreat_action(
                [unit.id for unit in fragile], rear, "mettre le commandement a l'abri"
            ),
        )


class ExcessivePursuitRule(SafetyRule):
    """Interdit de courir apres les fuyards loin du reste de l'armee."""

    name = "poursuite_mesuree"

    def __init__(self, max_distance: float) -> None:
        self.max_distance = max_distance

    def check(self, action: AgentAction, state: BattleState, rear: Vector3) -> SafetyVerdict | None:
        if action.type is not ActionType.CHASE_ROUTING:
            return None
        target_id = action.target_id
        target = state.unit(target_id) if target_id else None
        if target is None:
            return None
        army_center = state.centroid(Side.ALLY)
        distance = army_center.distance_2d(target.position)
        if distance <= self.max_distance:
            return None
        return SafetyVerdict(
            rule=self.name,
            reason=(
                f"fuyard trop eloigne de l'armee ({distance:.0f} m > {self.max_distance:.0f} m)"
            ),
            replacement=AgentAction(
                type=ActionType.HOLD_POSITION,
                actor_ids=action.actor_ids,
                reason="rester a portee de soutien",
                confidence=0.9,
            ),
        )


@dataclass(frozen=True, slots=True)
class SafetyOutcome:
    """Resultat du filtrage : ce qui passe, ce qui est refuse."""

    allowed: tuple[Decision, ...] = ()
    blocked: tuple[Decision, ...] = ()

    @property
    def actions(self) -> tuple[AgentAction, ...]:
        return tuple(decision.action for decision in self.allowed)


@dataclass
class SafetyEngine:
    """Applique toutes les regles, plus l'arret d'urgence et la limite d'ordres."""

    settings: SafetySettings = field(default_factory=SafetySettings)
    rules: tuple[SafetyRule, ...] = ()
    emergency_stop: bool = False
    _order_times: deque[float] = field(default_factory=deque, repr=False)

    @classmethod
    def from_settings(cls, settings: SafetySettings) -> SafetyEngine:
        """Compose le jeu de regles actif a partir des interrupteurs de configuration."""
        rules: list[SafetyRule] = []
        if settings.prevent_artillery_charge:
            rules.append(ArtilleryChargeRule())
        if settings.prevent_ranged_melee:
            rules.append(RangedMeleeRule(settings.ranged_threat_radius))
        rules.append(
            SuicidalChargeRule(
                settings.min_local_power_ratio_for_charge, settings.local_power_radius
            )
        )
        if settings.protect_lord:
            rules.append(LordProtectionRule(settings.lord_retreat_health_ratio))
        rules.append(ExcessivePursuitRule(settings.max_pursuit_distance))
        return cls(settings=settings, rules=tuple(rules))

    @classmethod
    def from_config(cls, safety: Mapping[str, Any]) -> SafetyEngine:
        return cls.from_settings(SafetySettings.from_config(safety))

    # --- arret d'urgence -----------------------------------------------------

    def trigger_emergency_stop(self) -> None:
        """Rend immediatement le controle au joueur : plus aucun ordre ne sort."""
        self.emergency_stop = True

    def release_emergency_stop(self) -> None:
        self.emergency_stop = False

    def reset(self) -> None:
        self._order_times.clear()
        self.emergency_stop = False

    # --- filtrage ------------------------------------------------------------

    def filter(
        self,
        decisions: Sequence[Decision],
        state: BattleState,
        *,
        rear: Vector3 | None = None,
    ) -> SafetyOutcome:
        """Filtre les decisions proposees par le planificateur.

        Une action refusee peut etre remplacee par un ordre correctif ; la
        substitution est elle-meme soumise a la limite d'ordres, jamais aux
        regles (elle est par construction defensive).
        """
        if self.emergency_stop:
            blocked = tuple(
                _blocked(decision, "arret_d_urgence", "arret d'urgence actif") for decision in decisions
            )
            return SafetyOutcome(allowed=(), blocked=blocked)

        fallback_rear = rear if rear is not None else _default_rear(state)
        allowed: list[Decision] = []
        blocked: list[Decision] = []

        for decision in decisions:
            verdict = self._first_verdict(decision.action, state, fallback_rear)
            if verdict is None:
                if self._accept_order(state.game_time):
                    allowed.append(decision)
                else:
                    blocked.append(
                        _blocked(
                            decision,
                            "limite_d_ordres",
                            f"limite de {self.settings.max_orders_per_minute} ordres par minute",
                        )
                    )
                continue

            blocked.append(
                Decision(
                    action=decision.action,
                    cause=decision.cause,
                    objective=decision.objective,
                    blocked_by=verdict.rule,
                    replacement=verdict.replacement,
                )
            )
            if verdict.replacement is not None and self._accept_order(state.game_time):
                allowed.append(
                    Decision(
                        action=verdict.replacement,
                        cause=verdict.reason,
                        objective="respecter une regle de securite",
                    )
                )

        return SafetyOutcome(allowed=tuple(allowed), blocked=tuple(blocked))

    def _first_verdict(
        self, action: AgentAction, state: BattleState, rear: Vector3
    ) -> SafetyVerdict | None:
        for rule in self.rules:
            verdict = rule.check(action, state, rear)
            if verdict is not None:
                return verdict
        return None

    def _accept_order(self, game_time: float) -> bool:
        """Limite le debit d'ordres sur une fenetre glissante d'une minute de jeu."""
        window_start = game_time - 60.0
        while self._order_times and self._order_times[0] < window_start:
            self._order_times.popleft()
        if len(self._order_times) >= self.settings.max_orders_per_minute:
            return False
        self._order_times.append(game_time)
        return True


def _blocked(decision: Decision, rule: str, reason: str) -> Decision:
    return Decision(
        action=decision.action,
        cause=reason,
        objective=decision.objective,
        blocked_by=rule,
    )


def _default_rear(state: BattleState) -> Vector3:
    """Point de repli par defaut : derriere le centre de gravite allie."""
    ally_center = state.centroid(Side.ALLY)
    enemy_center = state.centroid(Side.ENEMY)
    away = enemy_center.direction_to(ally_center)
    if away.length_2d() <= 1e-9:
        away = Vector3(0.0, 0.0, -1.0)
    return ally_center + away.scaled(80.0)
