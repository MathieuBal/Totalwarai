"""Qui tenait la hauteur ?

**Pourquoi ceci existe.** Le relief est la seule donnee de terrain que le jeu
nous donne, il circule depuis toujours — `position():get_y()`, enregistre a
chaque etat pour chaque unite — et **aucune decision ne le lisait**.

Ce que la mesure a trouve sur les deux batailles reelles, une fois les volantes
ecartees : l'agent est **arrive au contact en contrebas** dans les deux cas,
-5,25 m et -6,46 m, avec des creux au-dela de onze metres. Le relief de ces
cartes n'a rien d'anecdotique : vingt-deux et quinze metres separent le point
haut du point bas entre les deux lignes.

Cela se joue pendant la phase d'approche — les deux cents secondes ou l'agent, au
meme moment, ne donnait aucun ordre.

**Les volantes sont exclues des deux camps.** L'altitude d'une unite en vol est
celle de son vol, pas celle du sol : une volante relevee a 222 m quand le terrain
alentour est a 60 fausserait tout. C'est la premiere chose que cette mesure a
apprise.

Les ecarter ne suffit pourtant pas, et c'est la seconde lecon : **un seigneur
volant n'est ni de role `flying_unit` ni etiquete `flying`** — la regle du
classifieur cede la priorite a `lord` pour ne pas lui retirer sa protection. Une
telle unite passerait donc au travers. D'ou la mediane plutot que la moyenne :
sur douze unites, un seul point a deux cents metres deplace la moyenne de
treize metres et ne deplace pas la mediane du tout.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState

#: Duree d'une tranche, en secondes de jeu. Meme decoupage que `concentration`.
SLICE_SECONDS = 60.0

#: Ecart d'altitude au-dela duquel la position est franchement dominee.
#:
#: Les deux batailles perdues tenaient -8,51 m et -7,36 m de moyenne avant
#: l'effondrement. Trois metres est le plus petit ecart qui distingue un terrain
#: incline d'un terrain plat, sous lequel il n'y a rien a conclure.
COMMANDING_HEIGHT = 3.0


@dataclass(frozen=True, slots=True)
class Reading:
    """L'ecart d'altitude entre les deux lignes, a un instant."""

    game_time: float
    ally: float
    enemy: float
    #: Une unite etait-elle deja au contact a cet instant ?
    contact: bool = False

    @property
    def advantage(self) -> float:
        """Metres au-dessus d'eux. Negatif : nous sommes en contrebas."""
        return self.ally - self.enemy

    @property
    def uphill(self) -> bool:
        """Combattons-nous vers le haut, franchement ?"""
        return self.advantage <= -COMMANDING_HEIGHT


@dataclass
class Elevation:
    """Ce que valait notre position, en hauteur, sur toute une bataille."""

    readings: list[Reading] = field(default_factory=list)

    @property
    def approach(self) -> list[Reading]:
        """Les releves **avant le premier contact**.

        C'est la seule tranche qui juge le pilotage. Une fois les lignes au
        contact, l'altitude ne se choisit plus : elle est subie, et les fuyards
        qui refluent la font remonter sans que personne l'ait voulu. Mesure sur
        `854ebefb` : -11 m pendant l'approche, puis +11 m a la neuvieme minute
        alors que l'armee etait detruite. La moyenne des deux vaut -2 m et ne
        decrit aucun moment de la bataille.
        """
        avant = [item for item in self.readings if not item.contact]
        return avant if avant else list(self.readings)

    @property
    def approach_advantage(self) -> float:
        """Ecart moyen avant le contact — le chiffre qui juge le pilotage."""
        tranche = self.approach
        return statistics.mean(item.advantage for item in tranche) if tranche else 0.0

    @property
    def measured(self) -> bool:
        """A-t-on vu assez d'etats pour dire quoi que ce soit ?"""
        return len(self.readings) >= 10

    @property
    def average_advantage(self) -> float:
        if not self.readings:
            return 0.0
        return statistics.mean(item.advantage for item in self.readings)

    @property
    def worst_advantage(self) -> float:
        if not self.readings:
            return 0.0
        return min(item.advantage for item in self.readings)

    @property
    def uphill_share(self) -> float:
        """Part du temps passe franchement en contrebas."""
        if not self.readings:
            return 0.0
        return sum(1 for item in self.readings if item.uphill) / len(self.readings)

    @property
    def relief(self) -> float:
        """Amplitude du relief releve, tous camps confondus.

        Sans elle, un ecart nul ne se distingue pas d'une carte plate — et sur
        une carte plate il n'y a aucune hauteur a disputer.
        """
        if not self.readings:
            return 0.0
        altitudes = [valeur for item in self.readings for valeur in (item.ally, item.enemy)]
        return max(altitudes) - min(altitudes)

    @property
    def fought_uphill(self) -> bool:
        """Est-on **arrive** au contact en contrebas ?

        Juge sur l'approche et non sur la bataille entiere : voir `approach`.
        """
        return self.measured and self.approach_advantage <= -COMMANDING_HEIGHT

    def by_slice(self) -> list[tuple[int, int, float]]:
        """Par tranche de minute : numero, relevés, ecart moyen."""
        tranches: dict[int, list[float]] = {}
        for item in self.readings:
            tranches.setdefault(int(item.game_time // SLICE_SECONDS), []).append(item.advantage)
        return [
            (numero, len(valeurs), statistics.mean(valeurs))
            for numero, valeurs in sorted(tranches.items())
        ]

    def render(self) -> str:
        if not self.readings:
            return (
                "  Aucune altitude exploitable — il faut des unites au sol dans\n"
                "  les deux camps, les volantes ne renseignent pas le terrain."
            )
        lignes = [
            f"  releves                  : {len(self.readings)}",
            f"  **ecart avant contact**  : {self.approach_advantage:+.2f} m",
            f"  ecart sur la bataille    : {self.average_advantage:+.2f} m",
            f"  pire moment              : {self.worst_advantage:+.2f} m",
            f"  temps en contrebas       : {self.uphill_share:.0%}",
            f"  relief de la carte       : {self.relief:.1f} m",
            "",
            f"  {'minute':>7}{'releves':>9}{'ecart':>10}",
        ]
        for numero, combien, ecart in self.by_slice():
            lignes.append(f"  {numero:>7d}{combien:>9d}{ecart:>+10.2f}")

        if not self.measured:
            lignes += ["", "  Trop peu de releves pour conclure."]
        elif self.relief < COMMANDING_HEIGHT:
            lignes += [
                "",
                "  Terrain plat : il n'y avait aucune hauteur a disputer.",
            ]
        elif self.fought_uphill:
            lignes += [
                "",
                "**Nous avons livre la bataille en contrebas.** L'adversaire tenait",
                "  la hauteur, et c'est pendant la phase d'approche que cela se joue —",
                "  celle ou l'agent a le plus de temps et le moins d'ennemis au contact.",
            ]
        else:
            lignes += [
                "",
                "  La hauteur ne nous etait pas defavorable.",
            ]
        return "\n".join(lignes)


def _flies(unit: UnitState) -> bool:
    """L'unite est-elle en vol ? Son altitude ne renseigne alors pas le sol."""
    return unit.role is UnitRole.FLYING_UNIT or "flying" in unit.tags


def study(states: Iterable[BattleState]) -> Elevation:
    """L'ecart d'altitude entre les deux lignes, etat par etat.

    Ne retient que les etats ou **les deux camps** ont au moins une unite au sol
    vivante : comparer notre ligne a rien du tout produirait un avantage
    imaginaire au moment ou l'armee adverse sort du champ.
    """
    releves: list[Reading] = []
    for etat in states:
        altitudes: dict[Side, list[float]] = {}
        for camp in (Side.ALLY, Side.ENEMY):
            altitudes[camp] = [
                unite.position.y
                for unite in etat.side_units(camp)
                if unite.is_alive and not _flies(unite)
            ]
        if not altitudes[Side.ALLY] or not altitudes[Side.ENEMY]:
            continue
        releves.append(
            Reading(
                game_time=etat.game_time,
                contact=any(unite.is_engaged for unite in etat.side_units(Side.ALLY)),
                # Mediane et non moyenne : voir l'en-tete du module.
                ally=statistics.median(altitudes[Side.ALLY]),
                enemy=statistics.median(altitudes[Side.ENEMY]),
            )
        )
    return Elevation(readings=releves)
