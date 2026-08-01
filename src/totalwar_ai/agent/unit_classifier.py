"""Classification des unites du jeu vers la taxonomie interne de roles.

Aucune unite de *WARHAMMER III* n'est codee en dur : les regles proviennent de
`config/unit_roles.yaml`. Une mise a jour du jeu ou l'ajout d'une faction se
traite en editant le YAML, pas le code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from totalwar_ai.config import load_named_config
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.serialization import SchemaError
from totalwar_ai.domain.unit_state import UnitRole, UnitState


@dataclass(frozen=True, slots=True)
class ClassificationRule:
    """Regle declarative « si les etiquettes correspondent, alors ce role »."""

    role: UnitRole
    tags_all: frozenset[str] = frozenset()
    tags_any: frozenset[str] = frozenset()
    tags_none: frozenset[str] = frozenset()
    key_contains: tuple[str, ...] = ()

    def matches(self, tags: frozenset[str], key: str) -> bool:
        """Tous les matcheurs renseignes doivent etre satisfaits."""
        if not (self.tags_all or self.tags_any or self.tags_none or self.key_contains):
            return False
        if self.tags_all and not self.tags_all.issubset(tags):
            return False
        if self.tags_any and tags.isdisjoint(self.tags_any):
            return False
        if self.tags_none and not tags.isdisjoint(self.tags_none):
            return False
        if self.key_contains and not any(fragment in key for fragment in self.key_contains):
            return False
        return True

    @classmethod
    def from_dict(cls, raw: Any) -> ClassificationRule:
        if not isinstance(raw, Mapping):
            raise SchemaError("Une regle de classification doit etre un mapping")
        role_name = raw.get("role")
        if not isinstance(role_name, str):
            raise SchemaError("Une regle de classification doit declarer un 'role'")
        try:
            role = UnitRole(role_name)
        except ValueError:
            raise SchemaError(f"Role inconnu dans unit_roles.yaml : {role_name!r}") from None
        when = raw.get("when") or {}
        if not isinstance(when, Mapping):
            raise SchemaError(f"Le bloc 'when' de la regle {role_name} doit etre un mapping")
        return cls(
            role=role,
            tags_all=_as_lower_set(when.get("tags_all")),
            tags_any=_as_lower_set(when.get("tags_any")),
            tags_none=_as_lower_set(when.get("tags_none")),
            key_contains=tuple(sorted(_as_lower_set(when.get("key_contains")))),
        )


def _as_lower_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value.lower()})
    if not isinstance(value, Sequence):
        raise SchemaError("Un matcheur doit etre une chaine ou une liste de chaines")
    return frozenset(str(item).lower() for item in value)


@dataclass(frozen=True, slots=True)
class UnitClassifier:
    """Applique les regles dans l'ordre : la premiere correspondance gagne."""

    rules: tuple[ClassificationRule, ...] = ()
    default_role: UnitRole = UnitRole.UNKNOWN

    @classmethod
    def from_config(cls, data: Mapping[str, Any] | None = None) -> UnitClassifier:
        """Construit le classifieur depuis `config/unit_roles.yaml` (ou un mapping fourni)."""
        raw = dict(data) if data is not None else load_named_config("unit_roles")
        rules_raw = raw.get("rules") or []
        if isinstance(rules_raw, str) or not isinstance(rules_raw, Sequence):
            raise SchemaError("Le champ 'rules' de unit_roles.yaml doit etre une liste")
        default_name = raw.get("default_role", UnitRole.UNKNOWN.value)
        try:
            default_role = UnitRole(str(default_name))
        except ValueError:
            raise SchemaError(f"default_role inconnu : {default_name!r}") from None
        return cls(
            rules=tuple(ClassificationRule.from_dict(item) for item in rules_raw),
            default_role=default_role,
        )

    def classify(self, unit: UnitState) -> UnitRole:
        """Role deduit d'une unite.

        Un role deja renseigne par la source est considere comme fiable et
        conserve : le classifieur ne sert qu'a combler les trous.
        """
        if unit.role is not UnitRole.UNKNOWN:
            return unit.role
        tags = frozenset(tag.lower() for tag in unit.tags)
        key = (unit.unit_key or unit.name or unit.id).lower()
        for rule in self.rules:
            if rule.matches(tags, key):
                return rule.role
        return self.default_role

    def classify_units(self, units: Sequence[UnitState]) -> list[UnitState]:
        """Renvoie les unites avec leur role complete."""
        classified: list[UnitState] = []
        for unit in units:
            role = self.classify(unit)
            if role is unit.role:
                classified.append(unit)
            else:
                classified.append(_with_role(unit, role))
        return classified

    def classify_state(self, state: BattleState) -> BattleState:
        """Instantane identique, roles inconnus resolus."""
        units = self.classify_units(state.units)
        if all(new is old for new, old in zip(units, state.units, strict=True)):
            return state
        return state.with_units(units)


def _with_role(unit: UnitState, role: UnitRole) -> UnitState:
    return UnitState(
        id=unit.id,
        side=unit.side,
        role=role,
        position=unit.position,
        heading=unit.heading,
        health_ratio=unit.health_ratio,
        entity_ratio=unit.entity_ratio,
        morale=unit.morale,
        fatigue=unit.fatigue,
        ammo_ratio=unit.ammo_ratio,
        is_engaged=unit.is_engaged,
        is_routing=unit.is_routing,
        is_hidden=unit.is_hidden,
        current_target_id=unit.current_target_id,
        tags=unit.tags,
        unit_key=unit.unit_key,
        name=unit.name,
        metadata=dict(unit.metadata),
    )
