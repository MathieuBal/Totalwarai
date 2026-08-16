"""Constitution des groupes tactiques.

L'agent ne raisonne pas unite par unite mais par groupe : ligne de front,
tireurs, artillerie, cavalerie, commandement et reserve. Les groupes sont
recalcules a chaque plan, car les pertes changent la composition.
"""

from __future__ import annotations

from collections.abc import Collection
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


def build_groups(
    state: BattleState,
    *,
    side: Side = Side.ALLY,
    reserve_size: int = 0,
    reserve_ids: Collection[str] = (),
) -> GroupSet:
    """Repartit les unites d'un camp en groupes tactiques.

    `reserve_size` retire du front les unites les plus fraiches pour constituer
    une reserve : c'est la doctrine, pas la composition, qui en decide.

    `reserve_ids` nomme les unites **deja** en reserve, et elles y restent tant
    qu'elles sont disponibles. Seules les places vacantes se pourvoient a la
    fraicheur.

    .. rubric:: Pourquoi la fraicheur ne peut pas decider seule

    `effective_strength` inclut la fatigue, et le simple fait de se replier en
    coute. L'unite envoyee en reserve reculait de soixante metres, se fatiguait
    par ce seul recul, repassait derriere une unite de ligne restee immobile —
    et se faisait remplacer au plan suivant. Le critere de selection eliminait
    donc exactement celui qu'il venait de choisir.

    Mesure sur `skirmish_standoff`, face a un ennemi qui n'avance jamais : **119
    changements de composition** en 1201 plans, entre les quatre memes unites.
    Chaque rotation ramenait l'unite repliee dans une ligne elle-meme reculee et
    en envoyait une autre derriere — l'ancre a recule de 198 m et l'infanterie a
    parcouru 300 m **en s'eloignant** de l'ennemi.
    """
    buckets: dict[GroupKind, list[str]] = {kind: [] for kind in GroupKind}
    for unit in state.side_units(side, available_only=True):
        kind = ROLE_TO_GROUP.get(unit.role, GroupKind.FRONT_LINE)
        buckets[kind].append(unit.id)

    if reserve_size > 0 and len(buckets[GroupKind.FRONT_LINE]) > reserve_size:
        ligne = set(buckets[GroupKind.FRONT_LINE])
        line_units = [
            unit for unit in state.side_units(side, available_only=True) if unit.id in ligne
        ]
        # Les sortants d'abord, dans l'ordre stable de l'etat : la reserve ne se
        # renouvelle que par les places que la bataille a rendues vacantes.
        #
        # **La troncature porte sur une liste, jamais sur un ensemble.** Trancher
        # dans un `set` rendrait le banc dependant de `PYTHONHASHSEED` — c'est
        # exactement le defaut que l'ADR 0011 a coute a trouver.
        anciennes = set(reserve_ids)
        tenues = [unit.id for unit in line_units if unit.id in anciennes]
        reserved = set(tenues[:reserve_size])
        if len(reserved) < reserve_size:
            # Les unites les plus intactes font la meilleure reserve.
            candidates = [unit for unit in line_units if unit.id not in reserved]
            candidates.sort(key=lambda unit: unit.effective_strength, reverse=True)
            reserved |= {unit.id for unit in candidates[: reserve_size - len(reserved)]}
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
