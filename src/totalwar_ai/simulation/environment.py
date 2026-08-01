"""Simulateur tactique minimal.

Terrain plat abstrait, pas de temps fixe, resolution approximative du tir, de la
melee, de la fatigue et du moral. Objectif : rendre les decisions de l'agent
consequentes et reproductibles, pas simuler fidelement le jeu.

Determinisme : toute source d'alea passe par `random.Random(seed)`. A graine
egale, deux executions produisent exactement la meme bataille.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from totalwar_ai.domain.actions import ActionResult, ActionStatus, ActionType, AgentAction
from totalwar_ai.domain.battle_state import BattleOutcomeKind, BattlePhase, BattleState
from totalwar_ai.domain.geometry import Vector3, centroid, is_in_rear_arc, spread_positions
from totalwar_ai.domain.serialization import clamp
from totalwar_ai.domain.unit_state import RANGED_ROLES, Side, UnitRole, UnitState
from totalwar_ai.simulation.unit_templates import (
    SimulationParameters,
    SimulationRules,
    UnitTemplate,
)
from totalwar_ai.telemetry.events import Event, EventType, unit_event

#: Duree de tir continu (secondes) pour epuiser toutes les munitions.
AMMO_DURATION = 45.0


class OrderKind(StrEnum):
    """Ordre effectivement porte par une unite du simulateur."""

    HOLD = "hold"
    MOVE = "move"
    ATTACK = "attack"
    FIRE = "fire"
    RETREAT = "retreat"
    PROTECT = "protect"


@dataclass(slots=True)
class Order:
    """Traduction d'une action haut niveau pour une unite precise."""

    kind: OrderKind = OrderKind.HOLD
    destination: Vector3 | None = None
    target_id: str | None = None
    heading: float | None = None
    ignore_enemies: bool = False
    source_action: str = ""


@dataclass(slots=True)
class SimUnit:
    """Unite interne du simulateur (mutable, contrairement a `UnitState`)."""

    id: str
    side: Side
    role: UnitRole
    template: UnitTemplate
    position: Vector3
    heading: float = 0.0
    hp: float = 0.0
    max_hp: float = 0.0
    morale: float = 50.0
    max_morale: float = 50.0
    fatigue: float = 0.0
    ammo: float = 0.0
    max_ammo: float = 0.0
    routing: bool = False
    dead: bool = False
    order: Order = field(default_factory=Order)
    engaged_with: set[str] = field(default_factory=set)
    charge_timer: float = 0.0
    tags: tuple[str, ...] = ()
    unit_key: str = ""
    name: str = ""

    @property
    def hp_ratio(self) -> float:
        return 0.0 if self.max_hp <= 0 else clamp(self.hp / self.max_hp)

    @property
    def entity_ratio(self) -> float:
        """Fraction d'effectifs encore debout, arrondie a l'entite pres."""
        if self.template.entities <= 0:
            return 0.0
        entities = math.ceil(self.hp_ratio * self.template.entities)
        return clamp(entities / self.template.entities)

    @property
    def ammo_ratio(self) -> float:
        return 0.0 if self.max_ammo <= 0 else clamp(self.ammo / self.max_ammo)

    @property
    def is_engaged(self) -> bool:
        return bool(self.engaged_with)

    @property
    def is_available(self) -> bool:
        return not self.dead and not self.routing

    def snapshot(self) -> UnitState:
        """Vue immuable normalisee, telle que l'agent la recevra."""
        return UnitState(
            id=self.id,
            side=self.side,
            role=self.role,
            position=self.position,
            heading=self.heading,
            health_ratio=self.hp_ratio,
            entity_ratio=self.entity_ratio,
            morale=self.morale,
            fatigue=clamp(self.fatigue),
            ammo_ratio=self.ammo_ratio,
            is_engaged=self.is_engaged,
            is_routing=self.routing,
            is_hidden=False,
            current_target_id=self.order.target_id,
            tags=self.tags,
            unit_key=self.unit_key,
            name=self.name,
            metadata={
                "missile_range": self.template.missile_range,
                "speed": self.template.speed,
                "value": self.template.value,
            },
        )


@dataclass(frozen=True, slots=True)
class UnitSpec:
    """Description d'une unite dans un scenario."""

    id: str
    side: Side
    role: UnitRole
    position: Vector3
    heading: float = 0.0
    tags: tuple[str, ...] = ()
    unit_key: str = ""
    name: str = ""
    #: Etat initial : permet de decrire une bataille deja engagee (unite
    #: entamee, unite deja en deroute) plutot que toujours partir a neuf.
    initial_health: float = 1.0
    initial_morale_ratio: float = 1.0
    initial_routing: bool = False


@dataclass(frozen=True, slots=True)
class StepResult:
    """Resultat d'un pas de simulation."""

    state: BattleState
    events: tuple[Event, ...] = ()
    finished: bool = False
    outcome: BattleOutcomeKind = BattleOutcomeKind.UNKNOWN


class SimulationEnvironment:
    """Bataille simulee sur terrain plat."""

    def __init__(
        self,
        battle_id: str,
        specs: Sequence[UnitSpec],
        *,
        seed: int = 0,
        parameters: SimulationParameters | None = None,
        tick_seconds: float = 0.5,
        max_battle_seconds: float = 900.0,
        field_radius: float = 400.0,
    ) -> None:
        self.battle_id = battle_id
        self.parameters = parameters or SimulationParameters.load()
        self.rules: SimulationRules = self.parameters.rules
        self.tick_seconds = tick_seconds
        self.max_battle_seconds = max_battle_seconds
        self.field_radius = field_radius
        self.rng = random.Random(seed)
        self.seed = seed
        self.game_time = 0.0
        self.sequence = 0
        self.phase = BattlePhase.DEPLOYMENT
        self.finished = False
        self.outcome = BattleOutcomeKind.UNKNOWN
        self.units: dict[str, SimUnit] = {}
        for spec in specs:
            self.units[spec.id] = self._build_unit(spec)
        self.initial_strength = {
            Side.ALLY: self._side_strength(Side.ALLY),
            Side.ENEMY: self._side_strength(Side.ENEMY),
        }

    # --- construction --------------------------------------------------------

    def _build_unit(self, spec: UnitSpec) -> SimUnit:
        template = self.parameters.template(spec.role)
        max_hp = template.max_hp
        return SimUnit(
            id=spec.id,
            side=spec.side,
            role=spec.role,
            template=template,
            position=spec.position,
            heading=spec.heading,
            hp=max_hp * clamp(spec.initial_health),
            max_hp=max_hp,
            morale=template.morale * clamp(spec.initial_morale_ratio),
            max_morale=template.morale,
            ammo=float(template.ammo),
            max_ammo=float(template.ammo),
            routing=spec.initial_routing,
            tags=spec.tags,
            unit_key=spec.unit_key or spec.id,
            name=spec.name or spec.id,
        )

    # --- lecture -------------------------------------------------------------

    def state(self) -> BattleState:
        """Instantane courant, tel que transmis a l'agent."""
        return BattleState(
            battle_id=self.battle_id,
            sequence=self.sequence,
            game_time=self.game_time,
            phase=self.phase,
            units=tuple(unit.snapshot() for unit in self.units.values() if not unit.dead),
            metadata={"seed": self.seed, "tick_seconds": self.tick_seconds},
        )

    def side_units(self, side: Side, *, available_only: bool = False) -> list[SimUnit]:
        units = [unit for unit in self.units.values() if unit.side is side and not unit.dead]
        if available_only:
            units = [unit for unit in units if unit.is_available]
        return units

    def _side_strength(self, side: Side) -> float:
        return sum(unit.hp for unit in self.side_units(side))

    # --- application des actions --------------------------------------------

    def apply_actions(self, actions: Sequence[AgentAction]) -> list[ActionResult]:
        """Traduit les actions haut niveau en ordres, et accuse reception.

        Un refus n'est jamais silencieux : chaque action recoit un statut, avec
        le motif exact quand elle est rejetee.
        """
        results: list[ActionResult] = []
        for action in actions:
            error = self._apply_action(action)
            results.append(
                ActionResult(
                    action_id=action.action_id,
                    status=ActionStatus.ACCEPTED if error is None else ActionStatus.REJECTED,
                    error=error,
                )
            )
        return results

    def _apply_action(self, action: AgentAction) -> str | None:
        actors = [self.units[uid] for uid in action.actor_ids if uid in self.units]
        actors = [unit for unit in actors if unit.is_available and unit.side is Side.ALLY]
        if not actors:
            return "aucune unite alliee commandable dans cette action"

        parameters = action.parameters
        if action.type is ActionType.MOVE_GROUP:
            destination = _vector(parameters.get("destination"))
            if destination is None:
                return "destination manquante"
            heading = _float(parameters.get("heading"))
            spacing = _float(parameters.get("spacing")) or 0.0
            for unit, slot in zip(
                actors, _slots(actors, destination, heading, spacing), strict=True
            ):
                unit.order = Order(
                    kind=OrderKind.MOVE,
                    destination=slot,
                    heading=heading,
                    source_action=action.action_id,
                )
            return None

        if action.type is ActionType.HOLD_POSITION:
            heading = _float(parameters.get("heading"))
            for unit in actors:
                unit.order = Order(
                    kind=OrderKind.HOLD, heading=heading, source_action=action.action_id
                )
            return None

        if action.type in (ActionType.ATTACK_TARGET, ActionType.CHASE_ROUTING):
            target = self.units.get(action.target_id or "")
            if target is None or target.dead:
                return "cible inconnue ou detruite"
            for unit in actors:
                unit.order = Order(
                    kind=OrderKind.ATTACK, target_id=target.id, source_action=action.action_id
                )
            return None

        if action.type is ActionType.FLANK:
            target = self.units.get(action.target_id or "")
            if target is None or target.dead:
                return "cible inconnue ou detruite"
            for unit in actors:
                unit.order = Order(
                    kind=OrderKind.ATTACK,
                    target_id=target.id,
                    destination=_flank_point(target, self.rules.engagement_radius),
                    source_action=action.action_id,
                )
            return None

        if action.type is ActionType.FOCUS_FIRE:
            target = self.units.get(action.target_id or "")
            if target is None or target.dead:
                return "cible inconnue ou detruite"
            shooters = [unit for unit in actors if unit.template.is_ranged]
            if not shooters:
                return "aucune unite capable de tirer"
            for unit in shooters:
                unit.order = Order(
                    kind=OrderKind.FIRE, target_id=target.id, source_action=action.action_id
                )
            return None

        if action.type in (ActionType.RETREAT, ActionType.DISENGAGE):
            destination = _vector(parameters.get("destination"))
            for unit in actors:
                fallback = destination or self._disengage_point(unit)
                unit.order = Order(
                    kind=OrderKind.RETREAT,
                    destination=fallback,
                    ignore_enemies=True,
                    source_action=action.action_id,
                )
                unit.engaged_with.clear()
            return None

        if action.type is ActionType.PROTECT:
            protected_ids = parameters.get("protected_ids") or []
            protege = next(
                (
                    self.units[uid]
                    for uid in protected_ids
                    if uid in self.units and not self.units[uid].dead
                ),
                None,
            )
            if protege is None:
                return "unite a proteger inconnue"
            for unit in actors:
                unit.order = Order(
                    kind=OrderKind.PROTECT,
                    target_id=protege.id,
                    source_action=action.action_id,
                )
            return None

        if action.type is ActionType.FORM_RESERVE:
            rally = _vector(parameters.get("rally_point"))
            for unit in actors:
                unit.order = Order(
                    kind=OrderKind.MOVE if rally else OrderKind.HOLD,
                    destination=rally,
                    ignore_enemies=True,
                    source_action=action.action_id,
                )
            return None

        if action.type is ActionType.REORIENT_FRONT:
            heading = _float(parameters.get("heading"))
            if heading is None:
                return "cap manquant"
            for unit in actors:
                unit.heading = heading
                unit.order = Order(
                    kind=OrderKind.HOLD, heading=heading, source_action=action.action_id
                )
            return None

        # Toutes les valeurs d'ActionType sont traitees ci-dessus ; ce retour ne
        # sert que si une action est ajoutee au protocole sans etre implementee ici.
        return f"action non supportee par le simulateur : {action.type.value}"  # type: ignore[unreachable]

    # --- pas de simulation ---------------------------------------------------

    def step(self) -> StepResult:
        """Avance d'un tick : IA adverse, deplacements, tir, melee, moral."""
        if self.finished:
            return StepResult(state=self.state(), finished=True, outcome=self.outcome)

        events: list[Event] = []
        delta = self.tick_seconds
        self.game_time += delta
        self.sequence += 1

        self._enemy_policy()
        self._update_engagements(events)
        self._move_units(delta)
        self._resolve_missiles(delta, events)
        self._resolve_melee(delta, events)
        self._update_condition(delta, events)
        self._cleanup(events)
        self._update_phase()
        self._check_end(events)

        return StepResult(
            state=self.state(),
            events=tuple(events),
            finished=self.finished,
            outcome=self.outcome,
        )

    # --- IA adverse ----------------------------------------------------------

    def _enemy_policy(self) -> None:
        """Adversaire scripte : avance, tire, et fond sur nos tireurs a cheval."""
        allies = self.side_units(Side.ALLY, available_only=True)
        if not allies:
            return
        for unit in self.side_units(Side.ENEMY, available_only=True):
            if unit.is_engaged:
                continue
            preferred: list[SimUnit] = allies
            if unit.role in (UnitRole.LIGHT_CAVALRY, UnitRole.SHOCK_CAVALRY, UnitRole.FLYING_UNIT):
                exposed = [ally for ally in allies if ally.role in RANGED_ROLES]
                if exposed:
                    preferred = exposed
            target = min(preferred, key=lambda ally: unit.position.distance_2d(ally.position))
            distance = unit.position.distance_2d(target.position)
            if (
                unit.template.is_ranged
                and unit.ammo > 0
                and distance <= unit.template.missile_range
            ):
                unit.order = Order(kind=OrderKind.FIRE, target_id=target.id)
            else:
                unit.order = Order(kind=OrderKind.ATTACK, target_id=target.id)

    # --- resolution ----------------------------------------------------------

    def _update_engagements(self, events: list[Event]) -> None:
        """Met a jour qui est au contact de qui."""
        radius = self.rules.engagement_radius
        living = [unit for unit in self.units.values() if not unit.dead]
        for unit in living:
            unit.engaged_with = set()
        for index, unit in enumerate(living):
            if unit.routing or unit.order.ignore_enemies:
                continue
            for other in living[index + 1 :]:
                if other.side is unit.side or other.routing or other.order.ignore_enemies:
                    continue
                if unit.position.distance_2d(other.position) <= radius:
                    for combatant in (unit, other):
                        if combatant.engaged_with:
                            continue
                        events.append(self._unit_event(EventType.UNIT_ENGAGED, combatant))
                        # Le bonus de charge revient a qui arrive au contact.
                        if combatant.order.kind is OrderKind.ATTACK:
                            combatant.charge_timer = self.rules.charge_duration
                    unit.engaged_with.add(other.id)
                    other.engaged_with.add(unit.id)

    def _move_units(self, delta: float) -> None:
        """Deplace chaque unite selon son ordre courant."""
        for unit in self.units.values():
            if unit.dead:
                continue
            if unit.routing:
                self._move_routing(unit, delta)
                continue
            if unit.is_engaged and not unit.order.ignore_enemies:
                self._face(unit, self._nearest_engaged(unit))
                continue

            destination = self._destination_for(unit)
            if destination is None:
                if unit.order.heading is not None:
                    unit.heading = unit.order.heading
                continue

            speed = unit.template.speed * (1.0 - 0.3 * clamp(unit.fatigue))
            step = speed * delta
            if unit.position.distance_2d(destination) <= step:
                unit.position = Vector3(destination.x, unit.position.y, destination.z)
                if unit.order.heading is not None:
                    unit.heading = unit.order.heading
            else:
                unit.heading = unit.position.heading_to(destination)
                unit.position = unit.position.moved_towards(destination, step)
            unit.fatigue += self.rules.fatigue_move_per_second * delta

    def _destination_for(self, unit: SimUnit) -> Vector3 | None:
        order = unit.order
        if order.kind in (OrderKind.MOVE, OrderKind.RETREAT):
            return order.destination
        if order.kind is OrderKind.ATTACK:
            target = self.units.get(order.target_id or "")
            if target is None or target.dead:
                return None
            if order.destination is not None and unit.position.distance_2d(order.destination) > 5.0:
                return order.destination  # point de contournement avant le contact
            return target.position
        if order.kind is OrderKind.PROTECT:
            protege = self.units.get(order.target_id or "")
            if protege is None or protege.dead:
                return None
            threat = self._nearest_enemy(protege)
            if threat is None:
                return protege.position
            return centroid([protege.position, threat.position])
        return None

    def _move_routing(self, unit: SimUnit, delta: float) -> None:
        """Une unite en deroute fuit droit vers son bord de carte."""
        enemy_center = centroid(
            other.position for other in self.side_units(unit.side.opposite, available_only=True)
        )
        away = enemy_center.direction_to(unit.position)
        if away.length_2d() <= 1e-9:
            away = Vector3(0.0, 0.0, -1.0)
        speed = unit.template.speed * self.rules.rout_speed_multiplier
        unit.position = unit.position + away.scaled(speed * delta)
        unit.heading = math.atan2(away.x, away.z)

    def _resolve_missiles(self, delta: float, events: list[Event]) -> None:
        """Tir : portee, munitions, precision, penalite d'armure."""
        for unit in self.units.values():
            if unit.dead or unit.routing or not unit.template.is_ranged or unit.ammo <= 0:
                continue
            if unit.is_engaged or unit.order.kind is OrderKind.RETREAT:
                continue
            target = self.units.get(unit.order.target_id or "")
            if target is None or target.dead or target.side is unit.side:
                target = self._nearest_enemy(unit)
            if target is None:
                continue
            distance = unit.position.distance_2d(target.position)
            if distance > unit.template.missile_range:
                continue

            falloff = 1.0 - 0.35 * (distance / max(1.0, unit.template.missile_range))
            damage = (
                unit.template.missile_power
                * unit.template.accuracy
                * unit.hp_ratio
                * falloff
                * (1.0 - 0.6 * target.template.armour)
                * self._jitter()
                * delta
            )
            self._deal_damage(target, damage, events, attacker=unit)
            unit.ammo = max(0.0, unit.ammo - delta * unit.max_ammo / AMMO_DURATION)
            unit.heading = unit.position.heading_to(target.position)

    def _resolve_melee(self, delta: float, events: list[Event]) -> None:
        """Melee : puissance, charge, defense, armure, fatigue et flanc."""
        for unit in self.units.values():
            if unit.dead or unit.routing or not unit.engaged_with:
                continue
            defenders = [
                self.units[uid]
                for uid in unit.engaged_with
                if uid in self.units and not self.units[uid].dead
            ]
            if not defenders:
                continue
            share = 1.0 / len(defenders)
            unit.charge_timer = max(0.0, unit.charge_timer - delta)
            for defender in defenders:
                flanking = is_in_rear_arc(
                    defender.position, defender.heading, unit.position, self.rules.flank_arc_degrees
                )
                power = unit.template.melee_power
                if unit.charge_timer > 0.0:
                    power += unit.template.charge_bonus
                damage = (
                    power
                    * unit.hp_ratio
                    * share
                    * (1.0 - defender.template.defence)
                    * (1.0 - 0.5 * defender.template.armour)
                    * (1.0 - self.rules.fatigue_damage_penalty * clamp(unit.fatigue))
                    * (1.0 + (self.rules.flank_damage_bonus if flanking else 0.0))
                    * self._jitter()
                    * delta
                )
                self._deal_damage(defender, damage, events, attacker=unit, flanking=flanking)
            unit.fatigue += self.rules.fatigue_melee_per_second * delta

    def _deal_damage(
        self,
        target: SimUnit,
        damage: float,
        events: list[Event],
        *,
        attacker: SimUnit,
        flanking: bool = False,
    ) -> None:
        """Applique des degats et la perte de moral correspondante."""
        if damage <= 0.0 or target.dead:
            return
        before_ratio = target.hp_ratio
        target.hp = max(0.0, target.hp - damage)
        lost = before_ratio - target.hp_ratio
        target.morale -= self.rules.morale_loss_per_strength * lost
        if flanking:
            target.morale -= self.rules.morale_loss_flanked * self.tick_seconds
            if attacker.side is Side.ALLY and not _already_flagged(events, attacker.id):
                events.append(
                    self._unit_event(EventType.FLANK_ATTACK, attacker, target_id=target.id)
                )

    def _update_condition(self, delta: float, events: list[Event]) -> None:
        """Fatigue, recuperation de moral et deroutes."""
        for unit in self.units.values():
            if unit.dead:
                continue
            if not unit.is_engaged and unit.order.kind in (OrderKind.HOLD, OrderKind.FIRE):
                unit.fatigue = max(
                    0.0, unit.fatigue - self.rules.fatigue_recovery_per_second * delta
                )
                unit.morale = min(
                    unit.max_morale, unit.morale + self.rules.morale_recovery_per_second * delta
                )
            unit.fatigue = clamp(unit.fatigue)

            if not unit.routing and unit.morale <= self.rules.morale_rout_threshold:
                unit.routing = True
                unit.engaged_with.clear()
                unit.order = Order(kind=OrderKind.RETREAT, ignore_enemies=True)
                events.append(self._unit_event(EventType.UNIT_ROUTED, unit))
                for ally in self.side_units(unit.side, available_only=True):
                    ally.morale -= self.rules.morale_loss_ally_routed

    def _cleanup(self, events: list[Event]) -> None:
        """Retire les unites detruites ou sorties du champ de bataille."""
        center = Vector3(0.0, 0.0, 0.0)
        for unit in self.units.values():
            if unit.dead:
                continue
            fled = unit.routing and unit.position.distance_2d(center) > self.field_radius
            if unit.hp <= 0.0 or fled:
                unit.dead = True
                unit.engaged_with.clear()
                events.append(self._unit_event(EventType.UNIT_DESTROYED, unit, fled=fled))
                if unit.role is UnitRole.LORD:
                    for ally in self.side_units(unit.side, available_only=True):
                        ally.morale -= self.rules.morale_loss_lord_dead

    def _update_phase(self) -> None:
        """Deduit la phase tactique de la situation."""
        if self.phase is BattlePhase.DEPLOYMENT:
            self.phase = BattlePhase.APPROACH
        enemies = self.side_units(Side.ENEMY, available_only=True)
        engaged = any(unit.is_engaged for unit in self.units.values() if not unit.dead)
        routing = [unit for unit in self.side_units(Side.ENEMY) if unit.routing]
        if enemies and routing and len(routing) >= max(1, len(enemies)):
            self.phase = BattlePhase.PURSUIT
        elif engaged:
            self.phase = BattlePhase.ENGAGEMENT
        elif self.phase is not BattlePhase.PURSUIT:
            self.phase = BattlePhase.APPROACH

    def _check_end(self, events: list[Event]) -> None:
        """Termine la bataille et fixe l'issue."""
        allies = self.side_units(Side.ALLY, available_only=True)
        enemies = self.side_units(Side.ENEMY, available_only=True)
        timeout = self.game_time >= self.max_battle_seconds

        if allies and not enemies:
            self.outcome = BattleOutcomeKind.VICTORY
        elif enemies and not allies:
            self.outcome = BattleOutcomeKind.DEFEAT
        elif not allies and not enemies:
            self.outcome = BattleOutcomeKind.DRAW
        elif timeout:
            ally_share = self.remaining_share(Side.ALLY)
            enemy_share = self.remaining_share(Side.ENEMY)
            if ally_share > enemy_share * 1.2:
                self.outcome = BattleOutcomeKind.VICTORY
            elif enemy_share > ally_share * 1.2:
                self.outcome = BattleOutcomeKind.DEFEAT
            else:
                self.outcome = BattleOutcomeKind.DRAW
        else:
            return

        self.finished = True
        self.phase = BattlePhase.FINISHED
        events.append(
            Event(
                type=EventType.BATTLE_FINISHED,
                battle_id=self.battle_id,
                game_time=self.game_time,
                payload={
                    "outcome": self.outcome.value,
                    "ally_remaining": self.remaining_share(Side.ALLY),
                    "enemy_remaining": self.remaining_share(Side.ENEMY),
                    "timeout": timeout,
                },
            )
        )

    def remaining_share(self, side: Side) -> float:
        initial = self.initial_strength.get(side, 0.0)
        if initial <= 0.0:
            return 0.0
        return self._side_strength(side) / initial

    # --- utilitaires ---------------------------------------------------------

    def _jitter(self) -> float:
        amplitude = self.rules.damage_jitter
        return 1.0 + self.rng.uniform(-amplitude, amplitude)

    def _nearest_enemy(self, unit: SimUnit) -> SimUnit | None:
        hostiles = self.side_units(unit.side.opposite, available_only=True)
        if not hostiles:
            return None
        return min(hostiles, key=lambda other: unit.position.distance_2d(other.position))

    def _nearest_engaged(self, unit: SimUnit) -> SimUnit | None:
        candidates = [self.units[uid] for uid in unit.engaged_with if uid in self.units]
        if not candidates:
            return None
        return min(candidates, key=lambda other: unit.position.distance_2d(other.position))

    def _disengage_point(self, unit: SimUnit) -> Vector3:
        """Point de repli par defaut : 60 m a l'oppose de la menace la plus proche."""
        threat = self._nearest_engaged(unit) or self._nearest_enemy(unit)
        if threat is None:
            return unit.position
        away = threat.position.direction_to(unit.position)
        if away.length_2d() <= 1e-9:
            away = Vector3(0.0, 0.0, -1.0)
        return unit.position + away.scaled(60.0)

    def _face(self, unit: SimUnit, other: SimUnit | None) -> None:
        if other is not None:
            unit.heading = unit.position.heading_to(other.position)

    def _unit_event(self, event_type: EventType, unit: SimUnit, **extra: Any) -> Event:
        return unit_event(
            event_type,
            self.battle_id,
            self.game_time,
            unit_id=unit.id,
            side=unit.side,
            role=unit.role,
            **extra,
        )


# --- fonctions libres --------------------------------------------------------


def _slots(
    units: Sequence[SimUnit], destination: Vector3, heading: float | None, spacing: float
) -> list[Vector3]:
    """Repartit les unites d'un groupe autour du point demande.

    C'est ici que la « formation » d'une action haut niveau devient des
    positions concretes ; l'agent n'a jamais a les calculer.
    """
    if len(units) <= 1 or spacing <= 0.0:
        return [destination for _ in units]
    facing = Vector3(math.sin(heading), 0.0, math.cos(heading)) if heading is not None else None
    axis = Vector3(facing.z, 0.0, -facing.x) if facing is not None else Vector3(1.0, 0.0, 0.0)
    ordered = sorted(units, key=lambda unit: unit.position.x * axis.x + unit.position.z * axis.z)
    positions = spread_positions(destination, axis, len(units), spacing)
    mapping = {unit.id: position for unit, position in zip(ordered, positions, strict=True)}
    return [mapping[unit.id] for unit in units]


def _flank_point(target: SimUnit, radius: float) -> Vector3:
    """Point de contournement place derriere la cible."""
    behind = Vector3(-math.sin(target.heading), 0.0, -math.cos(target.heading))
    return target.position + behind.scaled(radius * 2.5)


def _vector(raw: Any) -> Vector3 | None:
    if raw is None:
        return None
    return Vector3.from_dict(raw)


def _float(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def _already_flagged(events: Sequence[Event], unit_id: str) -> bool:
    """Un flanquement par unite et par tick suffit pour la recompense."""
    return any(
        event.type is EventType.FLANK_ATTACK and event.payload.get("unit_id") == unit_id
        for event in events
    )
