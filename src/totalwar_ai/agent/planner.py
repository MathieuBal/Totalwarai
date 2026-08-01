"""Planification tactique deterministe.

Le planificateur produit deux choses :

* un :class:`BattlePlan` — la posture generale et les groupes, recalcule a
  basse frequence ;
* des :class:`~totalwar_ai.agent.explainability.Decision` — les ordres concrets,
  recalcules a frequence moyenne.

Toute la doctrine du MVP tient ici. Les regles de securite
(:mod:`totalwar_ai.agent.safety_rules`) sont un filet independant applique
ensuite : le planificateur peut se tromper, la securite doit rattraper.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from totalwar_ai.agent.explainability import Decision, decide
from totalwar_ai.agent.grouping import GroupKind, GroupSet, TacticalGroup, build_groups
from totalwar_ai.domain.actions import ActionType, AgentAction, Formation
from totalwar_ai.domain.battle_state import BattlePhase, BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import (
    MOBILE_ROLES,
    RANGED_ROLES,
    Side,
    UnitRole,
    UnitState,
)

#: Interet tactique intrinseque d'une cible, dans [0, 1].
TARGET_PRIORITY: dict[UnitRole, float] = {
    UnitRole.ARTILLERY: 1.00,
    UnitRole.RANGED_INFANTRY: 0.85,
    UnitRole.HERO_CASTER: 0.80,
    UnitRole.LORD: 0.70,
    UnitRole.MONSTER: 0.65,
    UnitRole.SHOCK_CAVALRY: 0.60,
    UnitRole.CHARIOT: 0.58,
    UnitRole.LIGHT_CAVALRY: 0.55,
    UnitRole.FLYING_UNIT: 0.55,
    UnitRole.HERO_MELEE: 0.55,
    UnitRole.SUPPORT: 0.50,
    UnitRole.MELEE_INFANTRY: 0.45,
    UnitRole.SPEAR_INFANTRY: 0.45,
    UnitRole.UNKNOWN: 0.40,
}

#: Portee de tir supposee quand la source ne la fournit pas (metres).
DEFAULT_MISSILE_RANGE = 120.0


class Posture(str, Enum):
    """Intention generale de la bataille."""

    DEFEND = "defend"
    ADVANCE = "advance"
    ENVELOP = "envelop"
    DELAY = "delay"


@dataclass(frozen=True, slots=True)
class PlannerSettings:
    """Parametres de doctrine. Tous ajustables sans toucher au code."""

    line_spacing: float = 45.0
    missile_offset: float = 25.0
    artillery_offset: float = 55.0
    command_offset: float = 35.0
    reserve_offset: float = 60.0
    cavalry_wing_offset: float = 40.0
    engagement_distance: float = 55.0
    ranged_threat_radius: float = 70.0
    pursuit_power_ratio: float = 1.5
    reserve_units: int = 1
    min_line_for_reserve: int = 4
    reorient_angle_degrees: float = 60.0

    @classmethod
    def from_config(cls, agent: Mapping[str, Any], safety: Mapping[str, Any]) -> PlannerSettings:
        defaults = cls()
        return cls(
            line_spacing=float(agent.get("line_spacing", defaults.line_spacing)),
            missile_offset=float(agent.get("missile_offset", defaults.missile_offset)),
            artillery_offset=float(agent.get("artillery_offset", defaults.artillery_offset)),
            command_offset=float(agent.get("command_offset", defaults.command_offset)),
            reserve_offset=float(agent.get("reserve_offset", defaults.reserve_offset)),
            cavalry_wing_offset=float(
                agent.get("cavalry_wing_offset", defaults.cavalry_wing_offset)
            ),
            engagement_distance=float(
                agent.get("engagement_distance", defaults.engagement_distance)
            ),
            ranged_threat_radius=float(
                safety.get("ranged_threat_radius", defaults.ranged_threat_radius)
            ),
            pursuit_power_ratio=float(
                agent.get("pursuit_power_ratio", defaults.pursuit_power_ratio)
            ),
            reserve_units=int(agent.get("reserve_units", defaults.reserve_units)),
            min_line_for_reserve=int(
                agent.get("min_line_for_reserve", defaults.min_line_for_reserve)
            ),
        )


@dataclass(frozen=True)
class BattlePlan:
    """Plan general courant."""

    posture: Posture
    anchor: Vector3
    front_direction: Vector3
    groups: GroupSet
    rationale: str
    created_at: float = 0.0
    power_ratio: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "posture": self.posture.value,
            "anchor": self.anchor.to_dict(),
            "front_direction": self.front_direction.to_dict(),
            "groups": self.groups.to_dict(),
            "rationale": self.rationale,
            "created_at": self.created_at,
            "power_ratio": self.power_ratio,
        }


def lateral_of(front: Vector3) -> Vector3:
    """Axe perpendiculaire au front, au sol (sens : aile gauche -> aile droite)."""
    if front.length_2d() <= 1e-9:
        return Vector3(1.0, 0.0, 0.0)
    return Vector3(front.z, 0.0, -front.x)


def missile_range(unit: UnitState) -> float:
    """Portee de tir connue de l'unite, sinon valeur par defaut prudente."""
    value = unit.metadata.get("missile_range")
    if isinstance(value, int | float) and value > 0:
        return float(value)
    return DEFAULT_MISSILE_RANGE


@dataclass
class Planner:
    """Doctrine deterministe du MVP."""

    settings: PlannerSettings = field(default_factory=PlannerSettings)

    # --- plan general --------------------------------------------------------

    def build_plan(self, state: BattleState) -> BattlePlan:
        """Choisit la posture et recompose les groupes."""
        allies = state.allies()
        enemies = state.enemies()
        anchor = state.centroid(Side.ALLY)
        enemy_anchor = state.centroid(Side.ENEMY)
        front = anchor.direction_to(enemy_anchor)
        if front.length_2d() <= 1e-9:
            front = Vector3(0.0, 0.0, 1.0)

        ratio = state.power_ratio()
        missile_edge = _missile_edge(state)
        cavalry = [unit for unit in allies if unit.role in MOBILE_ROLES]

        if not enemies:
            posture, rationale = Posture.ADVANCE, "aucun ennemi visible : progression prudente"
        elif ratio >= 1.35 and cavalry:
            posture, rationale = (
                Posture.ENVELOP,
                f"superiorite ({ratio:.2f}) et cavalerie disponible : envelopper",
            )
        elif missile_edge >= 1.2 and ratio >= 0.8:
            posture, rationale = (
                Posture.DEFEND,
                "avantage de tir : laisser l'ennemi venir sous le feu",
            )
        elif ratio >= 1.05:
            posture, rationale = Posture.ADVANCE, f"leger avantage ({ratio:.2f}) : avancer"
        elif ratio >= 0.75:
            posture, rationale = Posture.DEFEND, f"forces comparables ({ratio:.2f}) : tenir"
        else:
            posture, rationale = (
                Posture.DELAY,
                f"inferiorite ({ratio:.2f}) : temporiser et user l'ennemi",
            )

        line_count = sum(
            1 for unit in allies if unit.role not in RANGED_ROLES and unit.role not in MOBILE_ROLES
        )
        reserve_size = (
            self.settings.reserve_units
            if posture in (Posture.DEFEND, Posture.DELAY)
            and line_count >= self.settings.min_line_for_reserve
            else 0
        )

        return BattlePlan(
            posture=posture,
            anchor=anchor,
            front_direction=front,
            groups=build_groups(state, reserve_size=reserve_size),
            rationale=rationale,
            created_at=state.game_time,
            power_ratio=ratio,
        )

    # --- selection de cible --------------------------------------------------

    def score_target(
        self,
        attacker: UnitState,
        candidate: UnitState,
        state: BattleState,
        *,
        assignments: Mapping[str, int] | None = None,
        for_missile: bool = False,
    ) -> float:
        """Note l'interet d'une cible pour un attaquant donne.

        Combine proximite, priorite de role, vulnerabilite et saturation. Le tir
        evite les melees en cours (risque de tirs allies) et les fuyards.
        """
        distance = attacker.position.distance_2d(candidate.position)
        if for_missile:
            # Le tir choisit surtout selon la valeur de la cible : la distance
            # ne coute qu'un peu de precision tant qu'on reste dans la portee.
            score = 0.9 / (1.0 + distance / 50.0) + TARGET_PRIORITY.get(candidate.role, 0.4)
        else:
            # Au corps a corps, chaque metre parcouru est du temps offert a
            # l'ennemi : la proximite prime largement sur la valeur de la cible.
            score = 2.0 / (1.0 + distance / 25.0) + TARGET_PRIORITY.get(candidate.role, 0.4)
        score += 0.30 * (1.0 - candidate.effective_strength)

        already = (assignments or {}).get(candidate.id, 0)
        score -= 0.20 * already

        if candidate.is_routing:
            score -= 0.60
        if for_missile:
            if distance > missile_range(attacker):
                return float("-inf")
            if candidate.is_engaged:
                score -= 0.60  # tir dans une melee : risque de pertes alliees
        return score

    def select_target(
        self,
        attacker: UnitState,
        state: BattleState,
        *,
        assignments: Mapping[str, int] | None = None,
        for_missile: bool = False,
        candidates: Sequence[UnitState] | None = None,
    ) -> UnitState | None:
        """Meilleure cible visible, ou repli sur l'ennemi visible le plus proche."""
        pool = list(candidates) if candidates is not None else state.enemies()
        if not pool:
            return None
        scored = [
            (
                self.score_target(
                    attacker, candidate, state, assignments=assignments, for_missile=for_missile
                ),
                candidate,
            )
            for candidate in pool
        ]
        scored = [(score, candidate) for score, candidate in scored if score != float("-inf")]
        if not scored:
            return None
        best_score = max(score for score, _ in scored)
        # Egalite : l'identifiant tranche pour rester deterministe.
        best = sorted(
            (candidate for score, candidate in scored if score >= best_score - 1e-9),
            key=lambda unit: unit.id,
        )
        return best[0]

    # --- ordres --------------------------------------------------------------

    def decisions_for(self, state: BattleState, plan: BattlePlan) -> list[Decision]:
        """Ordres a emettre pour l'etat courant."""
        if state.phase is BattlePhase.DEPLOYMENT:
            return self.deployment_decisions(state, plan)
        return self.tactical_decisions(state, plan)

    def deployment_decisions(self, state: BattleState, plan: BattlePlan) -> list[Decision]:
        """Placement initial : ligne devant, tir derriere, cavalerie sur les ailes."""
        decisions: list[Decision] = []
        front = plan.front_direction
        lateral = lateral_of(front)
        anchor = plan.anchor
        heading = math.atan2(front.x, front.z)
        groups = plan.groups

        line = groups.get(GroupKind.FRONT_LINE)
        if line:
            decisions.append(
                decide(
                    _move(
                        line,
                        anchor,
                        Formation.LINE,
                        heading=heading,
                        spacing=self.settings.line_spacing,
                    ),
                    "phase de deploiement, ligne de front a etablir",
                    "presenter un front continu face a l'ennemi",
                    confidence=0.9,
                )
            )

        for kind, offset, spacing in (
            (GroupKind.MISSILE, self.settings.missile_offset, self.settings.line_spacing * 0.85),
            (GroupKind.ARTILLERY, self.settings.artillery_offset, self.settings.line_spacing),
        ):
            group = groups.get(kind)
            if not group:
                continue
            decisions.append(
                decide(
                    _move(
                        group,
                        anchor - front.scaled(offset),
                        Formation.LINE,
                        heading=heading,
                        spacing=spacing,
                    ),
                    "phase de deploiement, unites de tir a abriter",
                    "tirer depuis l'arriere sans etre atteint au corps a corps",
                    confidence=0.88,
                )
            )

        cavalry = groups.get(GroupKind.CAVALRY)
        if cavalry:
            half_width = self._half_width(len(line.unit_ids))
            wing_offset = half_width + self.settings.cavalry_wing_offset
            wings = _split_wings(cavalry)
            for sign, wing in zip((-1.0, 1.0), wings, strict=False):
                if not wing:
                    continue
                destination = anchor + lateral.scaled(sign * wing_offset)
                decisions.append(
                    decide(
                        _move(
                            wing,
                            destination,
                            Formation.LOOSE,
                            heading=heading,
                            spacing=self.settings.line_spacing * 0.6,
                        ),
                        "phase de deploiement, ailes a couvrir",
                        "menacer les flancs et proteger nos tireurs",
                        confidence=0.8,
                    )
                )

        command = groups.get(GroupKind.COMMAND)
        if command:
            decisions.append(
                decide(
                    _move(
                        command,
                        anchor - front.scaled(self.settings.command_offset),
                        Formation.LOOSE,
                        heading=heading,
                        spacing=self.settings.line_spacing * 0.5,
                    ),
                    "phase de deploiement, commandement expose",
                    "soutenir la ligne sans exposer le seigneur",
                    confidence=0.85,
                )
            )

        reserve = groups.get(GroupKind.RESERVE)
        if reserve:
            decisions.append(
                decide(
                    AgentAction(
                        type=ActionType.FORM_RESERVE,
                        actor_ids=reserve.unit_ids,
                        parameters={
                            "rally_point": anchor - front.scaled(self.settings.reserve_offset)
                        },
                        confidence=0.75,
                    ),
                    "doctrine defensive : conserver une reserve",
                    "pouvoir colmater une percee ou exploiter une ouverture",
                    confidence=0.75,
                )
            )
        return decisions

    def tactical_decisions(self, state: BattleState, plan: BattlePlan) -> list[Decision]:
        """Ordres de combat, du plus urgent au plus opportuniste."""
        decisions: list[Decision] = []
        assignments: dict[str, int] = {}
        groups = plan.groups
        front = plan.front_direction
        anchor = plan.anchor
        rear = anchor - front.scaled(self.settings.reserve_offset)

        retreating = self._protect_ranged(state, groups, rear, decisions)
        self._fire_missiles(state, groups, retreating, assignments, decisions)
        self._command_front_line(state, plan, assignments, decisions)
        self._command_cavalry(state, plan, assignments, decisions)
        self._command_leaders(state, plan, decisions)
        self._command_reserve(state, plan, assignments, decisions)
        return decisions

    # --- sous-decisions ------------------------------------------------------

    def _protect_ranged(
        self,
        state: BattleState,
        groups: GroupSet,
        rear: Vector3,
        decisions: list[Decision],
    ) -> set[str]:
        """Replie les unites de tir menacees au corps a corps."""
        retreating: set[str] = set()
        radius = self.settings.ranged_threat_radius
        for kind in (GroupKind.MISSILE, GroupKind.ARTILLERY):
            for unit in groups.get(kind).available_units(state):
                threats = state.threats_to(unit, radius)
                if not threats:
                    continue
                nearest = min(threats, key=lambda enemy: unit.position.distance_2d(enemy.position))
                distance = unit.position.distance_2d(nearest.position)
                retreating.add(unit.id)
                decisions.append(
                    decide(
                        AgentAction(
                            type=ActionType.RETREAT,
                            actor_ids=(unit.id,),
                            parameters={"destination": rear, "threat_id": nearest.id},
                            confidence=0.9,
                        ),
                        f"{_role_label(nearest.role)} ennemie a moins de {distance:.0f} metres",
                        "eviter un engagement au corps a corps",
                        confidence=0.9,
                    )
                )
        return retreating

    def _fire_missiles(
        self,
        state: BattleState,
        groups: GroupSet,
        retreating: set[str],
        assignments: dict[str, int],
        decisions: list[Decision],
    ) -> None:
        """Concentre le tir des unites disponibles sur les meilleures cibles."""
        for kind in (GroupKind.ARTILLERY, GroupKind.MISSILE):
            shooters = [
                unit
                for unit in groups.get(kind).available_units(state)
                if unit.can_shoot and unit.id not in retreating
            ]
            for shooter in shooters:
                target = self.select_target(
                    shooter, state, assignments=assignments, for_missile=True
                )
                if target is None:
                    continue
                assignments[target.id] = assignments.get(target.id, 0) + 1
                distance = shooter.position.distance_2d(target.position)
                decisions.append(
                    decide(
                        AgentAction(
                            type=ActionType.FOCUS_FIRE,
                            actor_ids=(shooter.id,),
                            parameters={"target_id": target.id},
                            confidence=0.8,
                        ),
                        f"cible prioritaire {_role_label(target.role)} a {distance:.0f} metres",
                        "user la cible la plus dangereuse avant le contact",
                        confidence=0.8,
                    )
                )

    def _command_front_line(
        self,
        state: BattleState,
        plan: BattlePlan,
        assignments: dict[str, int],
        decisions: list[Decision],
    ) -> None:
        """Fait tenir, avancer ou engager la ligne selon la posture."""
        line = plan.groups.get(GroupKind.FRONT_LINE)
        units = line.available_units(state)
        if not units:
            return
        enemies = state.enemies()
        if not enemies:
            return
        heading = math.atan2(plan.front_direction.x, plan.front_direction.z)

        for unit in units:
            nearest = state.nearest(unit.position, enemies)
            if nearest is None:
                continue
            enemy, distance = nearest
            if unit.is_engaged or distance <= self.settings.engagement_distance:
                # Seuls les ennemis a portee de charge sont des cibles credibles :
                # courir a l'autre bout du champ pour une meilleure cible est un piege.
                reachable = state.units_within(
                    unit.position, self.settings.engagement_distance * 2.5, enemies
                )
                target = (
                    self.select_target(
                        unit, state, assignments=assignments, candidates=reachable or [enemy]
                    )
                    or enemy
                )
                assignments[target.id] = assignments.get(target.id, 0) + 1
                decisions.append(
                    decide(
                        AgentAction(
                            type=ActionType.ATTACK_TARGET,
                            actor_ids=(unit.id,),
                            parameters={"target_id": target.id},
                            confidence=0.75,
                        ),
                        f"ennemi {_role_label(target.role)} a {distance:.0f} metres du front",
                        "fixer l'ennemi sur notre ligne",
                        confidence=0.75,
                    )
                )
            elif plan.posture in (Posture.ADVANCE, Posture.ENVELOP):
                destination = unit.position.moved_towards(
                    enemy.position, max(0.0, distance - self.settings.engagement_distance * 0.5)
                )
                decisions.append(
                    decide(
                        _move_units(
                            (unit.id,), destination, Formation.LINE, heading=heading, spacing=0.0
                        ),
                        f"posture {plan.posture.value} : ennemi a {distance:.0f} metres",
                        "reduire la distance en gardant la cohesion",
                        confidence=0.7,
                    )
                )
            else:
                decisions.append(
                    decide(
                        AgentAction(
                            type=ActionType.HOLD_POSITION,
                            actor_ids=(unit.id,),
                            parameters={"heading": heading},
                            confidence=0.7,
                        ),
                        f"posture {plan.posture.value} : laisser l'ennemi venir",
                        "conserver la formation et economiser la fatigue",
                        confidence=0.7,
                    )
                )

    def _command_cavalry(
        self,
        state: BattleState,
        plan: BattlePlan,
        assignments: dict[str, int],
        decisions: list[Decision],
    ) -> None:
        """Flanque les tireurs adverses, protege les notres, poursuit avec mesure."""
        cavalry = plan.groups.get(GroupKind.CAVALRY).available_units(state)
        if not cavalry:
            return
        enemies = state.enemies()
        missile_group = plan.groups.get(GroupKind.MISSILE)
        our_missiles = missile_group.available_units(state)

        # Menace directe sur nos tireurs : la cavalerie intercepte en priorite.
        threatened = [
            unit
            for unit in our_missiles
            if state.threats_to(unit, self.settings.ranged_threat_radius)
        ]
        for index, rider in enumerate(cavalry):
            if threatened and index == 0:
                protege = threatened[0]
                decisions.append(
                    decide(
                        AgentAction(
                            type=ActionType.PROTECT,
                            actor_ids=(rider.id,),
                            parameters={"protected_ids": [protege.id]},
                            confidence=0.78,
                        ),
                        f"nos tireurs ({protege.id}) sont menaces",
                        "intercepter la menace avant le contact",
                        confidence=0.78,
                    )
                )
                continue

            juicy = [
                enemy
                for enemy in enemies
                if enemy.role in RANGED_ROLES and not enemy.is_routing and not enemy.is_engaged
            ]
            if juicy and plan.posture is not Posture.DELAY:
                target = self.select_target(
                    rider, state, assignments=assignments, candidates=juicy
                )
                if target is not None:
                    assignments[target.id] = assignments.get(target.id, 0) + 1
                    decisions.append(
                        decide(
                            AgentAction(
                                type=ActionType.FLANK,
                                actor_ids=(rider.id,),
                                parameters={"target_id": target.id, "side": _wing_side(index)},
                                confidence=0.72,
                            ),
                            f"{_role_label(target.role)} ennemie exposee",
                            "neutraliser le tir adverse par un contournement",
                            confidence=0.72,
                        )
                    )
                    continue

            routing = [enemy for enemy in enemies if enemy.is_routing]
            if routing and plan.power_ratio >= self.settings.pursuit_power_ratio:
                target = min(
                    routing, key=lambda enemy: rider.position.distance_2d(enemy.position)
                )
                decisions.append(
                    decide(
                        AgentAction(
                            type=ActionType.CHASE_ROUTING,
                            actor_ids=(rider.id,),
                            parameters={"target_id": target.id},
                            confidence=0.6,
                        ),
                        f"avantage net ({plan.power_ratio:.2f}) et ennemi en deroute",
                        "transformer la deroute en pertes definitives",
                        confidence=0.6,
                    )
                )
                continue

            decisions.append(
                decide(
                    AgentAction(
                        type=ActionType.HOLD_POSITION,
                        actor_ids=(rider.id,),
                        parameters={},
                        confidence=0.6,
                    ),
                    "aucune opportunite de flanquement sure",
                    "garder la cavalerie disponible",
                    confidence=0.6,
                )
            )

    def _command_leaders(
        self, state: BattleState, plan: BattlePlan, decisions: list[Decision]
    ) -> None:
        """Maintient le commandement en soutien, hors de la melee frontale."""
        command = plan.groups.get(GroupKind.COMMAND).available_units(state)
        if not command:
            return
        support_point = plan.anchor - plan.front_direction.scaled(self.settings.command_offset)
        for leader in command:
            if leader.position.distance_2d(support_point) <= self.settings.command_offset * 0.5:
                continue
            decisions.append(
                decide(
                    _move_units(
                        (leader.id,), support_point, Formation.LOOSE, heading=None, spacing=0.0
                    ),
                    "position de soutien a rejoindre",
                    "preserver le commandement tout en soutenant la ligne",
                    confidence=0.7,
                )
            )

    def _command_reserve(
        self,
        state: BattleState,
        plan: BattlePlan,
        assignments: dict[str, int],
        decisions: list[Decision],
    ) -> None:
        """Garde la reserve, sauf si la ligne est debordee."""
        reserve = plan.groups.get(GroupKind.RESERVE).available_units(state)
        if not reserve:
            return
        line = plan.groups.get(GroupKind.FRONT_LINE).available_units(state)
        engaged_enemies = [enemy for enemy in state.enemies() if enemy.is_engaged]
        outnumbered = len(engaged_enemies) > max(1, len(line))

        for unit in reserve:
            if outnumbered:
                target = self.select_target(unit, state, assignments=assignments)
                if target is not None:
                    assignments[target.id] = assignments.get(target.id, 0) + 1
                    decisions.append(
                        decide(
                            AgentAction(
                                type=ActionType.ATTACK_TARGET,
                                actor_ids=(unit.id,),
                                parameters={"target_id": target.id},
                                confidence=0.7,
                            ),
                            f"ligne debordee ({len(engaged_enemies)} ennemis engages)",
                            "engager la reserve pour retablir la ligne",
                            confidence=0.7,
                        )
                    )
                    continue
            decisions.append(
                decide(
                    AgentAction(
                        type=ActionType.FORM_RESERVE,
                        actor_ids=(unit.id,),
                        parameters={
                            "rally_point": plan.anchor
                            - plan.front_direction.scaled(self.settings.reserve_offset)
                        },
                        confidence=0.65,
                    ),
                    "la ligne tient, la doctrine exige une reserve",
                    "conserver une capacite d'intervention",
                    confidence=0.65,
                )
            )

    def _half_width(self, line_units: int) -> float:
        return max(1, line_units - 1) * self.settings.line_spacing / 2.0


# --- constructeurs d'actions -------------------------------------------------


def _move(
    group: TacticalGroup,
    destination: Vector3,
    formation: Formation,
    *,
    heading: float | None,
    spacing: float,
) -> AgentAction:
    return _move_units(group.unit_ids, destination, formation, heading=heading, spacing=spacing)


def _move_units(
    unit_ids: Sequence[str],
    destination: Vector3,
    formation: Formation,
    *,
    heading: float | None,
    spacing: float,
) -> AgentAction:
    parameters: dict[str, Any] = {"destination": destination, "formation": formation}
    if heading is not None:
        parameters["heading"] = heading
    if spacing > 0.0:
        parameters["spacing"] = spacing
    return AgentAction(type=ActionType.MOVE_GROUP, actor_ids=tuple(unit_ids), parameters=parameters)


def _split_wings(group: TacticalGroup) -> tuple[TacticalGroup, TacticalGroup]:
    """Coupe un groupe en aile gauche et aile droite."""
    ids = list(group.unit_ids)
    middle = len(ids) // 2 + len(ids) % 2
    return (
        TacticalGroup(kind=group.kind, unit_ids=tuple(ids[:middle])),
        TacticalGroup(kind=group.kind, unit_ids=tuple(ids[middle:])),
    )


def _wing_side(index: int) -> str:
    return "left" if index % 2 == 0 else "right"


def _missile_edge(state: BattleState) -> float:
    """Rapport de puissance de tir allie / ennemi (10.0 si l'ennemi n'en a pas)."""
    ours = sum(
        unit.effective_strength for unit in state.allies() if unit.role in RANGED_ROLES
    )
    theirs = sum(
        unit.effective_strength for unit in state.enemies() if unit.role in RANGED_ROLES
    )
    if theirs <= 1e-6:
        return 10.0 if ours > 0 else 1.0
    return ours / theirs


def _role_label(role: UnitRole) -> str:
    labels = {
        UnitRole.LORD: "seigneur",
        UnitRole.HERO_MELEE: "heros",
        UnitRole.HERO_CASTER: "sorcier",
        UnitRole.MELEE_INFANTRY: "infanterie",
        UnitRole.SPEAR_INFANTRY: "infanterie de lances",
        UnitRole.RANGED_INFANTRY: "unite de tir",
        UnitRole.ARTILLERY: "artillerie",
        UnitRole.LIGHT_CAVALRY: "cavalerie legere",
        UnitRole.SHOCK_CAVALRY: "cavalerie de choc",
        UnitRole.CHARIOT: "char",
        UnitRole.MONSTER: "monstre",
        UnitRole.FLYING_UNIT: "unite volante",
        UnitRole.SUPPORT: "unite de soutien",
        UnitRole.UNKNOWN: "unite",
    }
    return labels.get(role, "unite")
