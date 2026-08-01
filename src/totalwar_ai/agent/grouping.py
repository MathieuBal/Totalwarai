"""Constitution des groupes tactiques.

L'agent ne raisonne pas unite par unite mais par groupe : ligne de front,
tireurs, artillerie, cavalerie, commandement et reserve. Les groupes sont
recalcules a chaque plan, car les pertes changent la composition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3, centroid
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState


class GroupKind(StrEnum):
    """Fonction tactique d'un groupe."""

    FRONT_LINE = "front_line"
    MISSILE = "missile"
    ARTILLERY = "artillery"
    CAVALRY = "cavalry"
    COMMAND = "command"
    RESERVE = "reserve"


#: Role -> groupe par defaut. La reserve, elle, est decidee par le planificateur.
ROLE_TO_GROUP: dict[UnitRole, GroupKind] = {
    UnitRole.LORD: GroupKind.COMMAND,
    UnitRole.HERO_MELEE: GroupKind.COMMAND,
    UnitRole.HERO_CASTER: GroupKind.COMMAND,
    UnitRole.MELEE_INFANTRY: GroupKind.FRONT_LINE,
    UnitRole.SPEAR_INFANTRY: GroupKind.FRONT_LINE,
    UnitRole.MONSTER: GroupKind.FRONT_LINE,
    UnitRole.SUPPORT: GroupKind.FRONT_LINE,
    UnitRole.UNKNOWN: GroupKind.FRONT_LINE,
    UnitRole.RANGED_INFANTRY: GroupKind.MISSILE,
    UnitRole.ARTILLERY: GroupKind.ARTILLERY,
    UnitRole.LIGHT_CAVALRY: GroupKind.CAVALRY,
    UnitRole.SHOCK_CAVALRY: GroupKind.CAVALRY,
    UnitRole.CHARIOT: GroupKind.CAVALRY,
    UnitRole.FLYING_UNIT: GroupKind.CAVALRY,
}


@dataclass(frozen=True, slots=True)
class TacticalGroup:
    """Ensemble nomme d'unites partageant une mission."""

    kind: GroupKind
    unit_ids: tuple[str, ...]

    @property
    def id(self) -> str:
        return self.kind.value

    def __bool__(self) -> bool:
        return bool(self.unit_ids)

    def units(self, state: BattleState) -> list[UnitState]:
        """Unites encore presentes dans l'etat donne (les mortes disparaissent)."""
        resolved = (state.unit(unit_id) for unit_id in self.unit_ids)
        return [unit for unit in resolved if unit is not None and unit.is_alive]

    def available_units(self, state: BattleState) -> list[UnitState]:
        return [unit for unit in self.units(state) if unit.is_available]

    def centroid(self, state: BattleState) -> Vector3:
        return centroid(unit.position for unit in self.available_units(state))

    def strength(self, state: BattleState) -> float:
        return sum(unit.effective_strength for unit in self.units(state))


@dataclass(frozen=True, slots=True)
class GroupSet:
    """Collection de groupes indexee par fonction."""

    groups: tuple[TacticalGroup, ...] = ()

    def get(self, kind: GroupKind) -> TacticalGroup:
        for group in self.groups:
            if group.kind is kind:
                return group
        return TacticalGroup(kind=kind, unit_ids=())

    def non_empty(self) -> list[TacticalGroup]:
        return [group for group in self.groups if group.unit_ids]

    def group_of(self, unit_id: str) -> TacticalGroup | None:
        for group in self.groups:
            if unit_id in group.unit_ids:
                return group
        return None

    def to_dict(self) -> dict[str, list[str]]:
        return {group.kind.value: list(group.unit_ids) for group in self.non_empty()}


def build_groups(state: BattleState, *, side: Side = Side.ALLY, reserve_size: int = 0) -> GroupSet:
    """Repartit les unites d'un camp en groupes tactiques.

    `reserve_size` retire du front les unites les plus fraiches pour constituer
    une reserve : c'est la doctrine, pas la composition, qui en decide.
    """
    buckets: dict[GroupKind, list[str]] = {kind: [] for kind in GroupKind}
    for unit in state.side_units(side, available_only=True):
        kind = ROLE_TO_GROUP.get(unit.role, GroupKind.FRONT_LINE)
        buckets[kind].append(unit.id)

    if reserve_size > 0 and len(buckets[GroupKind.FRONT_LINE]) > reserve_size:
        line_units = [
            unit
            for unit in state.side_units(side, available_only=True)
            if unit.id in set(buckets[GroupKind.FRONT_LINE])
        ]
        # Les unites les plus intactes font la meilleure reserve.
        line_units.sort(key=lambda unit: unit.effective_strength, reverse=True)
        reserved = {unit.id for unit in line_units[:reserve_size]}
        buckets[GroupKind.RESERVE].extend(sorted(reserved))
        buckets[GroupKind.FRONT_LINE] = [
            unit_id for unit_id in buckets[GroupKind.FRONT_LINE] if unit_id not in reserved
        ]

    return GroupSet(
        groups=tuple(
            TacticalGroup(kind=kind, unit_ids=tuple(unit_ids))
            for kind, unit_ids in buckets.items()
            if unit_ids
        )
    )
