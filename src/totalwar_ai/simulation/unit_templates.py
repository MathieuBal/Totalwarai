"""Gabarits d'unites et regles chiffrees du simulateur.

Ce module ne modelise pas *WARHAMMER III* : il fournit un modele grossier mais
coherent, suffisant pour que les decisions tactiques aient des consequences
mesurables. Toutes les valeurs viennent de `config/simulation.yaml`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

from totalwar_ai.config import load_named_config
from totalwar_ai.domain.unit_state import UnitRole


@dataclass(frozen=True, slots=True)
class UnitTemplate:
    """Caracteristiques d'un role dans le simulateur."""

    role: UnitRole = UnitRole.UNKNOWN
    entities: int = 100
    hp_per_entity: float = 4.0
    speed: float = 4.0
    melee_power: float = 22.0
    charge_bonus: float = 4.0
    defence: float = 0.30
    armour: float = 0.20
    missile_range: float = 0.0
    missile_power: float = 0.0
    accuracy: float = 0.0
    ammo: int = 0
    morale: float = 50.0
    value: float = 100.0

    @property
    def max_hp(self) -> float:
        """Reservoir de points de vie de l'unite entiere.

        Les unites a entite unique (seigneur, heros) ont peu d'entites mais
        beaucoup de vie chacune : sans cela, un seigneur fondrait en secondes.
        """
        return self.entities * self.hp_per_entity

    @property
    def is_ranged(self) -> bool:
        return self.missile_range > 0.0 and self.ammo > 0

    def with_values(self, role: UnitRole, raw: Mapping[str, Any]) -> UnitTemplate:
        """Copie surchargee par les valeurs d'un role."""
        known = {field.name for field in fields(self)} - {"role"}
        changes: dict[str, Any] = {}
        for key, value in raw.items():
            if key in known:
                changes[key] = int(value) if key in ("entities", "ammo") else float(value)
        return UnitTemplate(role=role, **{**_as_dict(self), **changes})


def _as_dict(template: UnitTemplate) -> dict[str, Any]:
    return {
        field.name: getattr(template, field.name)
        for field in fields(template)
        if field.name != "role"
    }


@dataclass(frozen=True, slots=True)
class SimulationRules:
    """Constantes de resolution du combat."""

    engagement_radius: float = 12.0
    charge_duration: float = 4.0
    flank_arc_degrees: float = 100.0
    flank_damage_bonus: float = 0.5
    morale_rout_threshold: float = 0.0
    morale_loss_per_strength: float = 90.0
    morale_loss_flanked: float = 4.0
    morale_loss_ally_routed: float = 6.0
    morale_loss_lord_dead: float = 20.0
    morale_recovery_per_second: float = 1.2
    fatigue_move_per_second: float = 0.010
    fatigue_melee_per_second: float = 0.022
    fatigue_recovery_per_second: float = 0.008
    fatigue_damage_penalty: float = 0.4
    damage_jitter: float = 0.15
    rout_speed_multiplier: float = 1.35

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> SimulationRules:
        if not raw:
            return cls()
        known = {field.name for field in fields(cls)}
        changes = {key: float(value) for key, value in raw.items() if key in known}
        return cls(**changes)


@dataclass(frozen=True, slots=True)
class SimulationParameters:
    """Regles + table de gabarits, prets a l'emploi."""

    rules: SimulationRules = field(default_factory=SimulationRules)
    templates: dict[UnitRole, UnitTemplate] = field(default_factory=dict)

    @classmethod
    def load(cls, raw: Mapping[str, Any] | None = None) -> SimulationParameters:
        """Charge `config/simulation.yaml` (ou le mapping fourni)."""
        data = dict(raw) if raw is not None else load_named_config("simulation")
        rules = SimulationRules.from_mapping(data.get("rules"))
        base = UnitTemplate()
        defaults_raw = data.get("defaults")
        if isinstance(defaults_raw, Mapping):
            base = base.with_values(UnitRole.UNKNOWN, defaults_raw)

        templates: dict[UnitRole, UnitTemplate] = {}
        roles_raw = data.get("roles")
        for role in UnitRole:
            entry = roles_raw.get(role.value) if isinstance(roles_raw, Mapping) else None
            if isinstance(entry, Mapping):
                templates[role] = base.with_values(role, entry)
            else:
                templates[role] = base.with_values(role, {})
        return cls(rules=rules, templates=templates)

    def template(self, role: UnitRole) -> UnitTemplate:
        return self.templates.get(role) or UnitTemplate(role=role)
