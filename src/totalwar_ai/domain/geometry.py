"""Primitives geometriques.

Toutes les distances du projet passent par ce module. Convention reprise du jeu :
``x`` et ``z`` decrivent le plan du terrain, ``y`` est l'altitude (toujours 0.0
dans le simulateur, mais conservee pour rester compatible avec les donnees reelles).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Vector3:
    """Point ou vecteur dans l'espace du terrain."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __add__(self, other: Vector3) -> Vector3:
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vector3) -> Vector3:
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def scaled(self, factor: float) -> Vector3:
        return Vector3(self.x * factor, self.y * factor, self.z * factor)

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def length_2d(self) -> float:
        """Norme en ignorant l'altitude."""
        return math.hypot(self.x, self.z)

    def distance_to(self, other: Vector3) -> float:
        return (self - other).length()

    def distance_2d(self, other: Vector3) -> float:
        """Distance au sol : c'est la mesure utilisee par la tactique."""
        return math.hypot(self.x - other.x, self.z - other.z)

    def direction_to(self, other: Vector3) -> Vector3:
        """Vecteur unitaire au sol pointant vers `other` (nul si confondus)."""
        dx = other.x - self.x
        dz = other.z - self.z
        distance = math.hypot(dx, dz)
        if distance <= 1e-9:
            return Vector3(0.0, 0.0, 0.0)
        return Vector3(dx / distance, 0.0, dz / distance)

    def heading_to(self, other: Vector3) -> float:
        """Cap (radians) a prendre pour faire face a `other`."""
        return math.atan2(other.x - self.x, other.z - self.z)

    def moved_towards(self, other: Vector3, distance: float) -> Vector3:
        """Avance de `distance` metres vers `other`, sans le depasser."""
        gap = self.distance_2d(other)
        if gap <= 1e-9 or distance >= gap:
            return Vector3(other.x, self.y, other.z)
        return self + self.direction_to(other).scaled(distance)

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_dict(cls, data: Any) -> Vector3:
        # Import local : `serialization` importe `geometry`, on evite le cycle.
        from totalwar_ai.domain.serialization import SchemaError, as_float

        if isinstance(data, Vector3):
            # Les parametres d'action transportent des vecteurs vivants en memoire.
            return data
        if isinstance(data, Sequence) and not isinstance(data, str | bytes):
            if len(data) != 3:
                raise SchemaError("Un vecteur sous forme de liste doit contenir 3 nombres")
            return cls(float(data[0]), float(data[1]), float(data[2]))
        if not isinstance(data, dict):
            raise SchemaError(f"Vecteur invalide : {type(data).__name__}")
        return cls(
            x=as_float(data, "x", default=0.0),
            y=as_float(data, "y", default=0.0),
            z=as_float(data, "z", default=0.0),
        )


ORIGIN = Vector3(0.0, 0.0, 0.0)


def centroid(points: Iterable[Vector3]) -> Vector3:
    """Barycentre d'un ensemble de points (origine si l'ensemble est vide)."""
    total = Vector3(0.0, 0.0, 0.0)
    count = 0
    for point in points:
        total = total + point
        count += 1
    if count == 0:
        return ORIGIN
    return total.scaled(1.0 / count)


def angle_between(first: Vector3, second: Vector3) -> float:
    """Angle non oriente (radians) entre deux vecteurs, au sol."""
    a = first.length_2d()
    b = second.length_2d()
    if a <= 1e-9 or b <= 1e-9:
        return 0.0
    dot = (first.x * second.x + first.z * second.z) / (a * b)
    return math.acos(max(-1.0, min(1.0, dot)))


def heading_vector(heading: float) -> Vector3:
    """Vecteur unitaire correspondant a un cap en radians."""
    return Vector3(math.sin(heading), 0.0, math.cos(heading))


def is_in_rear_arc(position: Vector3, heading: float, other: Vector3, arc_degrees: float) -> bool:
    """Indique si `other` se trouve dans l'arc arriere de l'unite regardee.

    Sert a detecter les attaques de flanc et de dos, cote agent comme cote simulateur.
    """
    to_other = position.direction_to(other)
    if to_other.length_2d() <= 1e-9:
        return False
    facing = heading_vector(heading)
    rear = facing.scaled(-1.0)
    return angle_between(rear, to_other) <= math.radians(arc_degrees) / 2.0


def spread_positions(center: Vector3, direction: Vector3, count: int, spacing: float) -> list[Vector3]:
    """Repartit `count` positions le long d'une ligne centree sur `center`.

    `direction` donne l'axe de la ligne (typiquement perpendiculaire au front).
    Utilise pour le deploiement et les reformations.
    """
    if count <= 0:
        return []
    axis = direction if direction.length_2d() > 1e-9 else Vector3(1.0, 0.0, 0.0)
    unit = axis.scaled(1.0 / axis.length_2d())
    offset = (count - 1) / 2.0
    return [center + unit.scaled((index - offset) * spacing) for index in range(count)]
