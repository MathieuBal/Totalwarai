"""Scenarios reproductibles.

Ils couvrent les situations de reference du README (banc de tests) et servent a
la fois de terrain d'entrainement et de garde-fou contre les regressions.
Chaque scenario est entierement deterministe : meme graine, meme bataille.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.simulation.environment import UnitSpec

#: Cap par defaut : les allies regardent vers +z, l'ennemi vers -z.
ALLY_HEADING = 0.0
ENEMY_HEADING = 3.14159


@dataclass(frozen=True, slots=True)
class Scenario:
    """Situation de depart nommee."""

    name: str
    description: str
    units: tuple[UnitSpec, ...]
    seed: int = 1
    max_battle_seconds: float = 600.0
    tags: tuple[str, ...] = ()

    def army(self, side: Side) -> list[UnitSpec]:
        return [spec for spec in self.units if spec.side is side]

    def fingerprint(self) -> str:
        """Empreinte de composition : sert a retrouver les batailles similaires."""
        return "|".join(
            f"{side.value}:{_composition(self.army(side))}" for side in (Side.ALLY, Side.ENEMY)
        )


def _composition(specs: Sequence[UnitSpec]) -> str:
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec.role.value] = counts.get(spec.role.value, 0) + 1
    return ",".join(f"{role}x{count}" for role, count in sorted(counts.items()))


def _unit(
    unit_id: str,
    side: Side,
    role: UnitRole,
    x: float,
    z: float,
    *,
    tags: tuple[str, ...] = (),
    health: float = 1.0,
    morale_ratio: float = 1.0,
    routing: bool = False,
) -> UnitSpec:
    return UnitSpec(
        id=unit_id,
        side=side,
        role=role,
        position=Vector3(x, 0.0, z),
        heading=ALLY_HEADING if side is Side.ALLY else ENEMY_HEADING,
        tags=tags,
        unit_key=unit_id,
        initial_health=health,
        initial_morale_ratio=morale_ratio,
        initial_routing=routing,
    )


def _line(
    side: Side,
    role: UnitRole,
    count: int,
    z: float,
    *,
    prefix: str,
    spacing: float = 45.0,
    tags: tuple[str, ...] = (),
) -> list[UnitSpec]:
    """Range `count` unites identiques sur une ligne centree en x = 0."""
    offset = (count - 1) / 2.0
    return [
        _unit(f"{prefix}{index + 1}", side, role, (index - offset) * spacing, z, tags=tags)
        for index in range(count)
    ]


def balanced_clash() -> Scenario:
    """Armee equilibree contre armee equilibree."""
    units = [
        *_line(Side.ALLY, UnitRole.MELEE_INFANTRY, 3, -60.0, prefix="a_inf", tags=("melee",)),
        _unit("a_spear1", Side.ALLY, UnitRole.SPEAR_INFANTRY, 70.0, -60.0, tags=("spear",)),
        *_line(Side.ALLY, UnitRole.RANGED_INFANTRY, 2, -95.0, prefix="a_arc", tags=("missile",)),
        _unit(
            "a_cav1", Side.ALLY, UnitRole.SHOCK_CAVALRY, -110.0, -80.0, tags=("cavalry", "shock")
        ),
        _unit("a_lord1", Side.ALLY, UnitRole.LORD, 0.0, -110.0, tags=("lord",)),
        *_line(Side.ENEMY, UnitRole.MELEE_INFANTRY, 3, 60.0, prefix="e_inf", tags=("melee",)),
        _unit("e_spear1", Side.ENEMY, UnitRole.SPEAR_INFANTRY, -70.0, 60.0, tags=("spear",)),
        *_line(Side.ENEMY, UnitRole.RANGED_INFANTRY, 2, 95.0, prefix="e_arc", tags=("missile",)),
        _unit("e_cav1", Side.ENEMY, UnitRole.SHOCK_CAVALRY, 110.0, 80.0, tags=("cavalry", "shock")),
        _unit("e_lord1", Side.ENEMY, UnitRole.LORD, 0.0, 110.0, tags=("lord",)),
    ]
    return Scenario(
        name="balanced_clash",
        description="Deux armees comparables se font face en terrain degage.",
        units=tuple(units),
        seed=11,
        tags=("reference", "equilibre"),
    )


def ranged_defense() -> Scenario:
    """Defense avec un fort contingent de tir face a une masse d'infanterie."""
    units = [
        *_line(Side.ALLY, UnitRole.SPEAR_INFANTRY, 3, -60.0, prefix="a_spear", tags=("spear",)),
        *_line(Side.ALLY, UnitRole.RANGED_INFANTRY, 3, -100.0, prefix="a_arc", tags=("missile",)),
        _unit("a_lord1", Side.ALLY, UnitRole.LORD, 0.0, -120.0, tags=("lord",)),
        *_line(Side.ENEMY, UnitRole.MELEE_INFANTRY, 5, 90.0, prefix="e_inf", tags=("melee",)),
    ]
    return Scenario(
        name="ranged_defense",
        description="Ligne defensive protegeant trois unites de tir.",
        units=tuple(units),
        seed=23,
        tags=("reference", "tir", "defense"),
    )


def cavalry_flank_threat() -> Scenario:
    """Cavalerie ennemie lancee sur nos tireurs."""
    units = [
        *_line(Side.ALLY, UnitRole.MELEE_INFANTRY, 2, -60.0, prefix="a_inf", tags=("melee",)),
        *_line(Side.ALLY, UnitRole.RANGED_INFANTRY, 2, -100.0, prefix="a_arc", tags=("missile",)),
        _unit("a_cav1", Side.ALLY, UnitRole.LIGHT_CAVALRY, 90.0, -90.0, tags=("cavalry",)),
        *_line(Side.ENEMY, UnitRole.MELEE_INFANTRY, 2, 70.0, prefix="e_inf", tags=("melee",)),
        _unit(
            "e_cav1", Side.ENEMY, UnitRole.SHOCK_CAVALRY, -120.0, -20.0, tags=("cavalry", "shock")
        ),
        _unit("e_cav2", Side.ENEMY, UnitRole.LIGHT_CAVALRY, -140.0, 10.0, tags=("cavalry",)),
    ]
    return Scenario(
        name="cavalry_flank_threat",
        description="Deux unites de cavalerie ennemie contournent pour atteindre nos tireurs.",
        units=tuple(units),
        seed=37,
        tags=("reference", "cavalerie", "menace_flanc"),
    )


def artillery_assault() -> Scenario:
    """Notre artillerie doit tirer sans jamais partir a l'assaut."""
    units = [
        *_line(Side.ALLY, UnitRole.MELEE_INFANTRY, 3, -60.0, prefix="a_inf", tags=("melee",)),
        _unit("a_art1", Side.ALLY, UnitRole.ARTILLERY, -30.0, -130.0, tags=("artillery",)),
        _unit("a_art2", Side.ALLY, UnitRole.ARTILLERY, 30.0, -130.0, tags=("artillery",)),
        _unit("a_lord1", Side.ALLY, UnitRole.LORD, 0.0, -110.0, tags=("lord",)),
        *_line(Side.ENEMY, UnitRole.MELEE_INFANTRY, 3, 80.0, prefix="e_inf", tags=("melee",)),
        _unit("e_art1", Side.ENEMY, UnitRole.ARTILLERY, 0.0, 150.0, tags=("artillery",)),
    ]
    return Scenario(
        name="artillery_assault",
        description="Duel d'artillerie : la notre doit rester en batterie.",
        units=tuple(units),
        seed=41,
        tags=("reference", "artillerie"),
    )


def outnumbered() -> Scenario:
    """Inferiorite numerique nette : temporiser et user l'ennemi."""
    units = [
        *_line(Side.ALLY, UnitRole.SPEAR_INFANTRY, 2, -60.0, prefix="a_spear", tags=("spear",)),
        _unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, -100.0, tags=("missile",)),
        _unit("a_lord1", Side.ALLY, UnitRole.LORD, 0.0, -120.0, tags=("lord",)),
        *_line(Side.ENEMY, UnitRole.MELEE_INFANTRY, 5, 80.0, prefix="e_inf", tags=("melee",)),
        _unit(
            "e_cav1", Side.ENEMY, UnitRole.SHOCK_CAVALRY, -130.0, 60.0, tags=("cavalry", "shock")
        ),
    ]
    return Scenario(
        name="outnumbered",
        description="Trois unites alliees contre six : survie et usure.",
        units=tuple(units),
        seed=53,
        tags=("reference", "inferiorite"),
    )


def numerical_superiority() -> Scenario:
    """Superiorite numerique : savoir en profiter sans se disperser."""
    units = [
        *_line(Side.ALLY, UnitRole.MELEE_INFANTRY, 4, -60.0, prefix="a_inf", tags=("melee",)),
        *_line(Side.ALLY, UnitRole.RANGED_INFANTRY, 2, -100.0, prefix="a_arc", tags=("missile",)),
        _unit(
            "a_cav1", Side.ALLY, UnitRole.SHOCK_CAVALRY, -140.0, -80.0, tags=("cavalry", "shock")
        ),
        _unit("a_lord1", Side.ALLY, UnitRole.LORD, 0.0, -120.0, tags=("lord",)),
        *_line(Side.ENEMY, UnitRole.MELEE_INFANTRY, 3, 80.0, prefix="e_inf", tags=("melee",)),
    ]
    return Scenario(
        name="numerical_superiority",
        description="Huit unites alliees contre trois : exploiter l'avantage.",
        units=tuple(units),
        seed=67,
        tags=("reference", "superiorite"),
    )


def slow_versus_mobile() -> Scenario:
    """Armee lente contre armee mobile : ne pas courir apres la cavalerie."""
    units = [
        *_line(Side.ALLY, UnitRole.SPEAR_INFANTRY, 3, -60.0, prefix="a_spear", tags=("spear",)),
        _unit("a_art1", Side.ALLY, UnitRole.ARTILLERY, 0.0, -120.0, tags=("artillery",)),
        _unit("a_lord1", Side.ALLY, UnitRole.LORD, 0.0, -100.0, tags=("lord",)),
        _unit("e_cav1", Side.ENEMY, UnitRole.LIGHT_CAVALRY, -150.0, 40.0, tags=("cavalry",)),
        _unit("e_cav2", Side.ENEMY, UnitRole.LIGHT_CAVALRY, 150.0, 40.0, tags=("cavalry",)),
        _unit("e_cav3", Side.ENEMY, UnitRole.SHOCK_CAVALRY, 0.0, 120.0, tags=("cavalry", "shock")),
    ]
    return Scenario(
        name="slow_versus_mobile",
        description="Infanterie et artillerie face a une armee entierement montee.",
        units=tuple(units),
        seed=71,
        tags=("reference", "mobilite"),
    )


def monster_threat() -> Scenario:
    """Presence de monstres : cibles prioritaires et lignes de lances."""
    units = [
        *_line(Side.ALLY, UnitRole.SPEAR_INFANTRY, 3, -60.0, prefix="a_spear", tags=("spear",)),
        *_line(Side.ALLY, UnitRole.RANGED_INFANTRY, 2, -100.0, prefix="a_arc", tags=("missile",)),
        _unit("a_lord1", Side.ALLY, UnitRole.LORD, 0.0, -120.0, tags=("lord",)),
        _unit("e_mon1", Side.ENEMY, UnitRole.MONSTER, -40.0, 90.0, tags=("monster",)),
        _unit("e_mon2", Side.ENEMY, UnitRole.MONSTER, 40.0, 90.0, tags=("monster",)),
        _unit("e_inf1", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 110.0, tags=("melee",)),
    ]
    return Scenario(
        name="monster_threat",
        description="Deux monstres appuyes par une infanterie face a une ligne de lances.",
        units=tuple(units),
        seed=83,
        tags=("reference", "monstres"),
    )


def fragile_lord() -> Scenario:
    """Protection d'un seigneur deja entame : il doit survivre."""
    units = [
        *_line(Side.ALLY, UnitRole.MELEE_INFANTRY, 3, -60.0, prefix="a_inf", tags=("melee",)),
        _unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, -100.0, tags=("missile",)),
        _unit("a_lord1", Side.ALLY, UnitRole.LORD, 0.0, -80.0, tags=("lord",), health=0.25),
        _unit(
            "a_hero1",
            Side.ALLY,
            UnitRole.HERO_CASTER,
            40.0,
            -110.0,
            tags=("hero", "caster"),
            health=0.4,
        ),
        *_line(Side.ENEMY, UnitRole.MELEE_INFANTRY, 3, 70.0, prefix="e_inf", tags=("melee",)),
        _unit(
            "e_cav1", Side.ENEMY, UnitRole.SHOCK_CAVALRY, -120.0, 30.0, tags=("cavalry", "shock")
        ),
    ]
    return Scenario(
        name="fragile_lord",
        description="Le seigneur et le sorcier sont blesses : les preserver reste prioritaire.",
        units=tuple(units),
        seed=89,
        tags=("reference", "commandement"),
    )


def rout_pursuit() -> Scenario:
    """Fin de bataille : poursuivre sans se disperser."""
    units = [
        *_line(Side.ALLY, UnitRole.MELEE_INFANTRY, 3, -20.0, prefix="a_inf", tags=("melee",)),
        _unit("a_cav1", Side.ALLY, UnitRole.LIGHT_CAVALRY, -80.0, -10.0, tags=("cavalry",)),
        _unit("a_cav2", Side.ALLY, UnitRole.SHOCK_CAVALRY, 80.0, -10.0, tags=("cavalry", "shock")),
        _unit("a_arc1", Side.ALLY, UnitRole.RANGED_INFANTRY, 0.0, -60.0, tags=("missile",)),
        _unit(
            "e_inf1",
            Side.ENEMY,
            UnitRole.MELEE_INFANTRY,
            -40.0,
            120.0,
            tags=("melee",),
            health=0.3,
            routing=True,
        ),
        _unit(
            "e_inf2",
            Side.ENEMY,
            UnitRole.MELEE_INFANTRY,
            40.0,
            140.0,
            tags=("melee",),
            health=0.25,
            routing=True,
        ),
        _unit(
            "e_inf3",
            Side.ENEMY,
            UnitRole.MELEE_INFANTRY,
            0.0,
            60.0,
            tags=("melee",),
            health=0.6,
            morale_ratio=0.4,
        ),
    ]
    return Scenario(
        name="rout_pursuit",
        description="L'ennemi rompt : achever sans lancer la cavalerie a l'autre bout du champ.",
        units=tuple(units),
        seed=97,
        tags=("reference", "poursuite"),
    )


#: Registre des scenarios disponibles.
SCENARIOS: dict[str, Callable[[], Scenario]] = {
    "balanced_clash": balanced_clash,
    "ranged_defense": ranged_defense,
    "cavalry_flank_threat": cavalry_flank_threat,
    "artillery_assault": artillery_assault,
    "outnumbered": outnumbered,
    "numerical_superiority": numerical_superiority,
    "slow_versus_mobile": slow_versus_mobile,
    "monster_threat": monster_threat,
    "fragile_lord": fragile_lord,
    "rout_pursuit": rout_pursuit,
}


@dataclass(frozen=True, slots=True)
class ScenarioCatalog:
    """Acces nomme aux scenarios."""

    factories: dict[str, Callable[[], Scenario]] = field(default_factory=lambda: dict(SCENARIOS))

    def names(self) -> list[str]:
        return sorted(self.factories)

    def get(self, name: str) -> Scenario:
        factory = self.factories.get(name)
        if factory is None:
            available = ", ".join(self.names())
            raise KeyError(f"Scenario inconnu : {name!r} (disponibles : {available})")
        return factory()

    def all(self) -> list[Scenario]:
        return [self.get(name) for name in self.names()]


def get_scenario(name: str) -> Scenario:
    """Raccourci : recupere un scenario par son nom."""
    return ScenarioCatalog().get(name)


def scenario_names() -> list[str]:
    return ScenarioCatalog().names()
