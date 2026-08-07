"""Se bat-on concentre, ou se fait-on battre en detail ?

**Pourquoi ceci existe.** Les defaites mesurees se lisaient comme des cascades
de moral : douze unites sur douze en deroute, dix au-dessus de 40 % de sante.
Lu ainsi, le remede etait de retirer les unites menacees — et cette regle a
coute neuf points de victoires au banc sans rien prouver (ADR 0010).

Le rapport de forces **local** a donne l'autre lecture. Pour chaque unite en
melee, on compte les ennemis et les allies a moins de quarante metres. Sur les
deux batailles reelles : 65 % et 58 % des melees livrees en inferiorite locale,
mediane de 1,50 et 1,67 ennemi par allie, avec des pics a 2,00 et 3,00 pendant
la cascade, pas avant.

Le rapport global etait de 1,2 contre nous. Se battre a 2 contre 1 quand on est
a 1,2 contre 1, c'est la definition de la defaite en detail : **la cascade
n'etait pas la cause, elle etait le symptome.**

Ce module rend cette mesure reproductible, parce que c'est elle qui tranchera si
le terme de concentration du planificateur sert a quelque chose. Il ne juge pas
— il donne le chiffre, minute par minute.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.unit_state import Side

#: Rayon dans lequel une unite pese sur la melee de sa voisine, en metres.
#:
#: Meme valeur que `planner.SUPPORT_RADIUS` : la mesure doit interroger
#: exactement ce que le planificateur cherche a corriger, sinon elle ne le
#: valide pas.
SUPPORT_RADIUS = 40.0

#: Duree d'une tranche de rapport, en secondes de jeu.
SLICE_SECONDS = 60.0

#: Part de melees en inferiorite locale au-dela de laquelle on est battu en
#: detail.
#:
#: Les deux batailles perdues etaient a 65 % et 58 %. La moitie est le seuil a
#: partir duquel l'infériorite locale n'est plus l'accident d'un flanc mais le
#: regime normal du combat.
OUTNUMBERED_SHARE = 0.50


@dataclass(frozen=True, slots=True)
class Engagement:
    """Une melee alliee, vue par son voisinage immediat."""

    game_time: float
    unit_id: str
    enemies_near: int
    allies_near: int

    @property
    def ratio(self) -> float:
        """Ennemis par allie sur place, l'unite elle-meme comprise."""
        return self.enemies_near / (self.allies_near + 1)

    @property
    def outnumbered(self) -> bool:
        return self.ratio > 1.0


@dataclass
class Concentration:
    """Ce que valait le rapport de forces local, sur toute une bataille."""

    engagements: list[Engagement] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        """A-t-on vu assez de melees pour dire quoi que ce soit ?

        Une bataille sans contact ne prouve pas que l'on combat concentre : elle
        prouve que l'on n'a pas combattu.
        """
        return len(self.engagements) >= 10

    @property
    def median_ratio(self) -> float:
        if not self.engagements:
            return 0.0
        return statistics.median(item.ratio for item in self.engagements)

    @property
    def outnumbered_share(self) -> float:
        if not self.engagements:
            return 0.0
        return sum(1 for item in self.engagements if item.outnumbered) / len(self.engagements)

    @property
    def defeated_in_detail(self) -> bool:
        """Se fait-on battre en detail ?"""
        return self.measured and self.outnumbered_share >= OUTNUMBERED_SHARE

    def by_slice(self) -> list[tuple[int, int, float]]:
        """Par tranche de minute : numero, nombre de melees, rapport median.

        Le decoupage temporel n'est pas cosmetique. C'est lui qui a montre que
        le rapport local montait a 2,00 **pendant** l'effondrement et non avant :
        sans lui, la moyenne aurait masque le moment ou tout s'est joue.
        """
        tranches: dict[int, list[float]] = {}
        for item in self.engagements:
            tranches.setdefault(int(item.game_time // SLICE_SECONDS), []).append(item.ratio)
        return [
            (numero, len(valeurs), statistics.median(valeurs))
            for numero, valeurs in sorted(tranches.items())
        ]

    def render(self) -> str:
        if not self.engagements:
            return (
                "  Aucune melee dans cet enregistrement — rien a dire du rapport\n"
                "  de forces local. Ce n'est pas une bonne nouvelle : c'est une\n"
                "  bataille ou nos unites ne sont jamais entrees au contact."
            )
        lignes = [
            f"  melees relevees          : {len(self.engagements)}",
            f"  rapport local median     : {self.median_ratio:.2f} ennemi par allie",
            f"  melees en inferiorite    : {self.outnumbered_share:.0%}",
            "",
            f"  {'minute':>7}{'melees':>9}{'rapport local':>16}",
        ]
        for numero, combien, rapport in self.by_slice():
            lignes.append(f"  {numero:>7d}{combien:>9d}{rapport:>16.2f}")
        if not self.measured:
            lignes += [
                "",
                "  Trop peu de melees pour conclure.",
            ]
        elif self.defeated_in_detail:
            lignes += [
                "",
                "**Nous sommes battus en detail.** Plus d'une melee sur deux est",
                "  livree a un contre plus d'un, alors que le rapport global se joue",
                "  ailleurs. Ce n'est pas le moral qui lache en premier : c'est la",
                "  ligne qui s'etale pendant que la leur se concentre.",
            ]
        else:
            lignes += [
                "",
                "  Le combat est livre concentre : la majorite des melees se fait a",
                "  parite locale ou mieux.",
            ]
        return "\n".join(lignes)


def study(states: Iterable[BattleState]) -> Concentration:
    """Le rapport de forces local, melee par melee.

    Les fuyards ne comptent dans aucun camp : une unite qui rompt ne menace plus
    et ne soutient plus. Les compter fausserait la mesure exactement au moment
    ou elle importe — pendant la cascade, quand la moitie de la carte fuit.
    """
    releves: list[Engagement] = []
    for etat in states:
        allies = [item for item in etat.side_units(Side.ALLY) if item.is_alive]
        enemies = [
            item for item in etat.side_units(Side.ENEMY) if item.is_alive and not item.is_routing
        ]
        combattants = [item for item in allies if not item.is_routing]
        for unite in allies:
            if not unite.is_engaged or unite.is_routing:
                continue
            proches_ennemis = sum(
                1
                for autre in enemies
                if unite.position.distance_2d(autre.position) <= SUPPORT_RADIUS
            )
            if not proches_ennemis:
                # En melee sans ennemi a portee de mesure : l'enregistrement est
                # incoherent, mieux vaut ne rien compter que compter faux.
                continue
            proches_allies = sum(
                1
                for autre in combattants
                if autre.id != unite.id
                and unite.position.distance_2d(autre.position) <= SUPPORT_RADIUS
            )
            releves.append(
                Engagement(
                    game_time=etat.game_time,
                    unit_id=unite.id,
                    enemies_near=proches_ennemis,
                    allies_near=proches_allies,
                )
            )
    return Concentration(engagements=releves)
