"""Representation normalisee d'une unite.

Le cœur de l'agent ne doit jamais dependre des centaines de champs propres au
jeu : le mod Lua (ou le simulateur) produit ce format stable, et lui seul.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from totalwar_ai.domain.geometry import ORIGIN, Vector3
from totalwar_ai.domain.serialization import (
    SchemaError,
    as_bool,
    as_enum,
    as_optional_str,
    as_ratio,
    as_str,
    as_str_list,
    require,
    require_mapping,
)


class Side(StrEnum):
    """Camp d'une unite du point de vue de l'agent."""

    ALLY = "ally"
    ENEMY = "enemy"

    @property
    def opposite(self) -> Side:
        return Side.ENEMY if self is Side.ALLY else Side.ALLY


class UnitRole(StrEnum):
    """Taxonomie interne des roles (voir README).

    Volontairement generique : aucune unite precise du jeu n'y figure. La
    correspondance unite du jeu -> role est declaree dans `config/unit_roles.yaml`.
    """

    LORD = "lord"
    HERO_MELEE = "hero_melee"
    HERO_CASTER = "hero_caster"
    MELEE_INFANTRY = "melee_infantry"
    SPEAR_INFANTRY = "spear_infantry"
    RANGED_INFANTRY = "ranged_infantry"
    ARTILLERY = "artillery"
    LIGHT_CAVALRY = "light_cavalry"
    SHOCK_CAVALRY = "shock_cavalry"
    CHARIOT = "chariot"
    MONSTER = "monster"
    FLYING_UNIT = "flying_unit"
    SUPPORT = "support"
    UNKNOWN = "unknown"


#: Roles dont la valeur tactique repose sur le tir : a proteger de la melee.
RANGED_ROLES: frozenset[UnitRole] = frozenset(
    {UnitRole.RANGED_INFANTRY, UnitRole.ARTILLERY, UnitRole.HERO_CASTER}
)

#: Roles montes ou volants : mobiles, faits pour le flanquement et la poursuite.
MOBILE_ROLES: frozenset[UnitRole] = frozenset(
    {UnitRole.LIGHT_CAVALRY, UnitRole.SHOCK_CAVALRY, UnitRole.CHARIOT, UnitRole.FLYING_UNIT}
)

#: Roles qui tiennent la ligne de front.
LINE_ROLES: frozenset[UnitRole] = frozenset(
    {UnitRole.MELEE_INFANTRY, UnitRole.SPEAR_INFANTRY, UnitRole.MONSTER}
)

#: Roles a preserver en priorite (perte tres couteuse).
PRECIOUS_ROLES: frozenset[UnitRole] = frozenset(
    {UnitRole.LORD, UnitRole.HERO_MELEE, UnitRole.HERO_CASTER}
)


@dataclass(frozen=True, slots=True)
class UnitState:
    """Etat observable d'une unite a un instant donne.

    Les champs reprennent exactement le schema documente dans le README ; seuls
    `unit_key` et `name` sont ajoutes pour permettre la classification par
    identifiant lorsque les etiquettes sont absentes.
    """

    id: str
    side: Side
    role: UnitRole = UnitRole.UNKNOWN
    position: Vector3 = ORIGIN
    heading: float = 0.0
    health_ratio: float = 1.0
    entity_ratio: float = 1.0
    morale: float = 50.0
    fatigue: float = 0.0
    ammo_ratio: float = 0.0
    is_engaged: bool = False
    is_routing: bool = False
    is_hidden: bool = False
    current_target_id: str | None = None
    tags: tuple[str, ...] = ()
    unit_key: str = ""
    name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    # --- proprietes derivees -------------------------------------------------

    @property
    def is_ally(self) -> bool:
        return self.side is Side.ALLY

    @property
    def is_ranged(self) -> bool:
        """Unite dont la valeur vient du tir (y compris artillerie)."""
        return self.role in RANGED_ROLES

    @property
    def is_mobile(self) -> bool:
        return self.role in MOBILE_ROLES

    @property
    def is_precious(self) -> bool:
        return self.role in PRECIOUS_ROLES

    @property
    def is_alive(self) -> bool:
        """Une unite en deroute reste vivante : elle n'est simplement plus fiable."""
        return self.entity_ratio > 0.0 and self.health_ratio > 0.0

    @property
    def is_available(self) -> bool:
        """Unite reellement commandable : vivante, non en deroute."""
        return self.is_alive and not self.is_routing

    @property
    def can_shoot(self) -> bool:
        return (
            self.is_ranged and self.ammo_ratio > 0.0 and not self.is_engaged and self.is_available
        )

    @property
    def effective_strength(self) -> float:
        """Fraction de puissance restante, dans [0, 1].

        Combine effectifs et sante moyenne, puis penalise la fatigue et la deroute.
        C'est la mesure utilisee par les rapports de puissance et les recompenses.
        """
        if not self.is_alive:
            return 0.0
        base = self.entity_ratio * (0.5 + 0.5 * self.health_ratio)
        base *= 1.0 - 0.25 * self.fatigue
        if self.is_routing:
            base *= 0.1
        return max(0.0, base)

    # --- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "side": self.side.value,
            "role": self.role.value,
            "position": self.position.to_dict(),
            "heading": self.heading,
            "health_ratio": self.health_ratio,
            "entity_ratio": self.entity_ratio,
            "morale": self.morale,
            "fatigue": self.fatigue,
            "ammo_ratio": self.ammo_ratio,
            "is_engaged": self.is_engaged,
            "is_routing": self.is_routing,
            "is_hidden": self.is_hidden,
            "current_target_id": self.current_target_id,
            "tags": list(self.tags),
        }
        if self.unit_key:
            data["unit_key"] = self.unit_key
        if self.name:
            data["name"] = self.name
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data

    @classmethod
    def from_dict(cls, raw: Any) -> UnitState:
        data = require_mapping(raw, "UnitState")
        unit_id = require(data, "id")
        if not isinstance(unit_id, str) or not unit_id:
            raise SchemaError("Le champ 'id' d'une unite doit etre une chaine non vide")
        return cls(
            id=unit_id,
            side=as_enum(data, "side", Side),
            role=as_enum(data, "role", UnitRole, default=UnitRole.UNKNOWN),
            position=Vector3.from_dict(data.get("position", {})),
            heading=float(data.get("heading", 0.0) or 0.0),
            health_ratio=as_ratio(data, "health_ratio", default=1.0),
            entity_ratio=as_ratio(data, "entity_ratio", default=1.0),
            morale=float(data.get("morale", 50.0) or 0.0),
            fatigue=as_ratio(data, "fatigue", default=0.0),
            ammo_ratio=as_ratio(data, "ammo_ratio", default=0.0),
            is_engaged=as_bool(data, "is_engaged", default=False),
            is_routing=as_bool(data, "is_routing", default=False),
            is_hidden=as_bool(data, "is_hidden", default=False),
            current_target_id=as_optional_str(data, "current_target_id"),
            tags=tuple(as_str_list(data, "tags", default=[])),
            unit_key=as_str(data, "unit_key", default=""),
            name=as_str(data, "name", default=""),
            metadata=dict(data.get("metadata") or {}),
        )
