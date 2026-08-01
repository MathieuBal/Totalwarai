"""Etat global d'une bataille et requetes tactiques associees.

`BattleState` est immuable : chaque tick produit un nouvel instantane. Les
helpers de lecture (voisinage, menaces, isolement) vivent ici afin que l'agent,
le simulateur, les recompenses et les tests partagent exactement les memes
definitions.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from typing import Any

from totalwar_ai.domain.geometry import Vector3, centroid, is_in_rear_arc
from totalwar_ai.domain.serialization import (
    SchemaError,
    as_enum,
    as_float,
    as_int,
    as_str,
    require_mapping,
)
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState


class BattlePhase(str, Enum):
    """Phase tactique courante, telle qu'observee ou deduite."""

    DEPLOYMENT = "deployment"
    APPROACH = "approach"
    ENGAGEMENT = "engagement"
    PURSUIT = "pursuit"
    FINISHED = "finished"


class BattleOutcomeKind(str, Enum):
    """Issue d'une bataille."""

    VICTORY = "victory"
    DEFEAT = "defeat"
    DRAW = "draw"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BattleState:
    """Instantane complet et normalise d'une bataille."""

    battle_id: str
    sequence: int = 0
    game_time: float = 0.0
    phase: BattlePhase = BattlePhase.DEPLOYMENT
    units: tuple[UnitState, ...] = ()
    objectives: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    # --- acces aux unites ----------------------------------------------------

    @cached_property
    def _by_id(self) -> dict[str, UnitState]:
        return {unit.id: unit for unit in self.units}

    def unit(self, unit_id: str) -> UnitState | None:
        return self._by_id.get(unit_id)

    def side_units(self, side: Side, *, available_only: bool = False) -> list[UnitState]:
        units = [unit for unit in self.units if unit.side is side]
        if available_only:
            units = [unit for unit in units if unit.is_available]
        return units

    def allies(self, *, available_only: bool = True) -> list[UnitState]:
        return self.side_units(Side.ALLY, available_only=available_only)

    def enemies(self, *, available_only: bool = True, visible_only: bool = True) -> list[UnitState]:
        """Ennemis exploitables.

        `visible_only` applique la regle « pas de triche cachee » du README :
        l'agent ignore les unites marquees cachees.
        """
        units = self.side_units(Side.ENEMY, available_only=available_only)
        if visible_only:
            units = [unit for unit in units if not unit.is_hidden]
        return units

    def units_of_role(self, side: Side, *roles: UnitRole) -> list[UnitState]:
        wanted = set(roles)
        return [unit for unit in self.side_units(side) if unit.role in wanted]

    # --- mesures globales ----------------------------------------------------

    def centroid(self, side: Side) -> Vector3:
        return centroid(unit.position for unit in self.side_units(side, available_only=True))

    def strength(self, side: Side) -> float:
        """Somme des puissances residuelles d'un camp."""
        return sum(unit.effective_strength for unit in self.side_units(side))

    def power_ratio(self) -> float:
        """Rapport allie / ennemi. 1.0 = equilibre, > 1 = avantage allie.

        Renvoie une valeur plafonnee pour rester exploitable quand un camp est
        pratiquement aneanti.
        """
        allied = self.strength(Side.ALLY)
        enemy = self.strength(Side.ENEMY)
        if enemy <= 1e-6:
            return 10.0 if allied > 0 else 1.0
        return min(10.0, allied / enemy)

    def front_width(self, side: Side = Side.ALLY) -> float:
        """Largeur du front d'un camp (etalement maximal entre deux unites)."""
        positions = [unit.position for unit in self.side_units(side, available_only=True)]
        if len(positions) < 2:
            return 0.0
        return max(a.distance_2d(b) for a in positions for b in positions)

    # --- requetes tactiques --------------------------------------------------

    def nearest(
        self, origin: Vector3, candidates: Iterable[UnitState]
    ) -> tuple[UnitState, float] | None:
        """Unite la plus proche d'un point, avec sa distance."""
        best: tuple[UnitState, float] | None = None
        for unit in candidates:
            distance = origin.distance_2d(unit.position)
            if best is None or distance < best[1]:
                best = (unit, distance)
        return best

    def nearest_enemy(self, unit: UnitState) -> tuple[UnitState, float] | None:
        """Ennemi visible le plus proche : cible de secours de l'agent."""
        return self.nearest(unit.position, self.enemies())

    def units_within(
        self, origin: Vector3, radius: float, candidates: Iterable[UnitState]
    ) -> list[UnitState]:
        return [unit for unit in candidates if origin.distance_2d(unit.position) <= radius]

    def threats_to(self, unit: UnitState, radius: float) -> list[UnitState]:
        """Ennemis capables d'atteindre `unit` au corps a corps a court terme."""
        hostiles = self.enemies() if unit.is_ally else self.allies()
        return self.units_within(unit.position, radius, hostiles)

    def is_isolated(self, unit: UnitState, radius: float = 60.0) -> bool:
        """Unite sans allie a portee de soutien, alors que des ennemis rodent."""
        friends = self.allies() if unit.is_ally else self.enemies()
        nearby_friends = [
            other
            for other in friends
            if other.id != unit.id and unit.position.distance_2d(other.position) <= radius
        ]
        if nearby_friends:
            return False
        return bool(self.threats_to(unit, radius * 1.5))

    def is_flanked_by(self, unit: UnitState, attacker: UnitState, arc_degrees: float = 100.0) -> bool:
        """Vrai si `attacker` frappe `unit` par le flanc ou le dos."""
        return is_in_rear_arc(unit.position, unit.heading, attacker.position, arc_degrees)

    def routing_units(self, side: Side) -> list[UnitState]:
        return [unit for unit in self.side_units(side) if unit.is_routing and unit.is_alive]

    def lord(self, side: Side = Side.ALLY) -> UnitState | None:
        for unit in self.side_units(side):
            if unit.role is UnitRole.LORD:
                return unit
        return None

    # --- transformations -----------------------------------------------------

    def with_units(self, units: Sequence[UnitState], **changes: Any) -> BattleState:
        """Nouvel instantane derive de celui-ci."""
        return BattleState(
            battle_id=changes.get("battle_id", self.battle_id),
            sequence=changes.get("sequence", self.sequence),
            game_time=changes.get("game_time", self.game_time),
            phase=changes.get("phase", self.phase),
            units=tuple(units),
            objectives=changes.get("objectives", self.objectives),
            metadata=changes.get("metadata", dict(self.metadata)),
        )

    # --- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "battle_id": self.battle_id,
            "sequence": self.sequence,
            "game_time": self.game_time,
            "phase": self.phase.value,
            "units": [unit.to_dict() for unit in self.units],
            "objectives": list(self.objectives),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> BattleState:
        data = require_mapping(raw, "BattleState")
        raw_units = data.get("units", [])
        if isinstance(raw_units, str) or not isinstance(raw_units, Sequence):
            raise SchemaError("Le champ 'units' doit etre une liste")
        units = tuple(UnitState.from_dict(item) for item in raw_units)
        seen: set[str] = set()
        for unit in units:
            if unit.id in seen:
                raise SchemaError(f"Identifiant d'unite duplique : {unit.id}")
            seen.add(unit.id)
        objectives = data.get("objectives") or []
        if isinstance(objectives, str) or not isinstance(objectives, Sequence):
            raise SchemaError("Le champ 'objectives' doit etre une liste")
        return cls(
            battle_id=as_str(data, "battle_id"),
            sequence=as_int(data, "sequence", default=0),
            game_time=as_float(data, "game_time", default=0.0),
            phase=as_enum(data, "phase", BattlePhase, default=BattlePhase.DEPLOYMENT),
            units=units,
            objectives=tuple(str(item) for item in objectives),
            metadata=dict(data.get("metadata") or {}),
        )
