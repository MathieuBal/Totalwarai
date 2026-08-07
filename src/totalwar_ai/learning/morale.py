"""Voir venir la deroute, puisque le jeu ne donne pas le moral.

**Pourquoi ceci existe.** Trois batailles reelles menees a leur terme disent la
meme chose : on ne perd pas par usure, on perd par contagion. Sur la premiere
reference proprement mesuree, **douze unites sur douze ont rompu, dont dix
au-dessus de 40 % de sante** — la premiere a 63 %, une derniere a 100 %.

Une regle fondee sur la sante ne verra jamais cela venir. `unary_morale` est
absent du bac a sable Lua, verifie accesseur par accesseur : la variable qui
decide de ces batailles nous est invisible.

Elle laisse pourtant des traces. `is_routing` dit **apres coup** qu'une unite a
cede, et c'est une etiquette — des centaines de milliers d'exemples etiquetes,
la seule chose dans ce projet dont on ait assez pour apprendre vraiment. Ce
module mesure d'abord si ces traces existent : **quels signaux observables
precedent une rupture**, et de combien.

.. rubric:: Mesurer avant de modeliser

Il ne construit aucun predicteur. Il compte, pour chaque signal, la frequence
d'une rupture prochaine quand le signal est present et quand il ne l'est pas. Un
signal qui ne separe rien ne merite pas de modele ; un signal qui separe
nettement en merite un, et on saura lequel.

C'est la meme conduite qu'ailleurs ici : une correlation nommee vaut mieux
qu'une cause inventee, et une mesure vaut mieux qu'une intuition.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState

#: Delai dans lequel on cherche a voir venir la rupture, en secondes de jeu.
#:
#: Quinze secondes laissent le temps de decrocher une unite : les reprises
#: mesurees en bataille prennent effet en deux a trois secondes.
HORIZON = 15.0

#: Rayon dans lequel la deroute d'un allie compte comme voisine, en metres.
#:
#: La contagion de moral est spatiale dans Total War. Cent metres est l'ordre de
#: grandeur d'une ligne de bataille, pas d'une carte.
CONTAGION_RADIUS = 100.0

#: Distance en deca de laquelle un ennemi presse une unite, en metres.
PRESSURE_RADIUS = 50.0


@dataclass(frozen=True, slots=True)
class Signal:
    """Un signal observable, et ce qu'il annonce."""

    name: str
    #: Etats ou le signal etait present.
    present: int = 0
    #: Parmi eux, ceux suivis d'une rupture dans l'horizon.
    present_then_rout: int = 0
    #: Etats ou le signal etait absent.
    absent: int = 0
    absent_then_rout: int = 0

    @property
    def rate_present(self) -> float:
        return self.present_then_rout / self.present if self.present else 0.0

    @property
    def rate_absent(self) -> float:
        return self.absent_then_rout / self.absent if self.absent else 0.0

    @property
    def lift(self) -> float:
        """Combien de fois la rupture est plus frequente quand le signal est la.

        1.0 : le signal n'annonce rien. Au-dela : il annonce quelque chose.
        """
        if self.rate_absent <= 0.0:
            return 1.0 if self.rate_present <= 0.0 else float("inf")
        return self.rate_present / self.rate_absent

    @property
    def usable(self) -> bool:
        """Assez de cas des deux cotes pour que le rapport se lise ?"""
        return self.present >= 30 and self.absent >= 30

    def explain(self) -> str:
        if not self.usable:
            return f"{self.name:<28} trop peu de cas ({self.present} presents)"
        rapport = "  " if self.lift == float("inf") else f"x{self.lift:4.1f}"
        return (
            f"{self.name:<28} {self.rate_present:5.1%} contre {self.rate_absent:5.1%}  "
            f"{rapport}  ({self.present} etats)"
        )


@dataclass
class MoraleStudy:
    """Ce qui precede une rupture, mesure sur un corpus."""

    signals: list[Signal] = field(default_factory=list)
    #: Etats d'unite examines.
    samples: int = 0
    #: Parmi eux, ceux suivis d'une rupture dans l'horizon.
    routs: int = 0

    @property
    def base_rate(self) -> float:
        return self.routs / self.samples if self.samples else 0.0

    @property
    def informative(self) -> list[Signal]:
        """Signaux qui separent vraiment, du plus au moins."""
        retenus = [item for item in self.signals if item.usable and item.lift > 1.2]
        return sorted(retenus, key=lambda item: item.lift, reverse=True)

    def render(self) -> str:
        if not self.samples:
            return (
                "Aucun etat exploitable : le corpus ne contient aucune bataille "
                "avec des unites et une deroute."
            )
        lignes = [
            f"{self.samples} etats d'unite examines, "
            f"{self.routs} suivis d'une rupture sous {HORIZON:.0f} s "
            f"({self.base_rate:.1%} de base)",
            "",
            "  signal                       rupture si present / si absent",
        ]
        lignes += [
            f"  {item.explain()}"
            for item in sorted(self.signals, key=lambda entry: entry.lift, reverse=True)
        ]

        utiles = self.informative
        if utiles:
            lignes += [
                "",
                f"{len(utiles)} signal(aux) annoncent vraiment quelque chose. "
                f"Le plus net : {utiles[0].name}.",
                "  Un predicteur de rupture a donc de quoi se construire — c'est le seul",
                "  endroit du projet ou l'apprentissage dispose d'assez d'exemples.",
            ]
        else:
            lignes += [
                "",
                "Aucun signal ne separe nettement. La rupture reste imprevisible avec",
                "  ce que le jeu nous montre, et aucun modele n'y changera rien.",
            ]
        return "\n".join(lignes)


def study(battles: Iterable[Sequence[BattleState]], *, horizon: float = HORIZON) -> MoraleStudy:
    """Mesure quels signaux observables precedent une rupture.

    Prend les batailles **une par une** : un horizon ne traverse pas la fin
    d'une bataille, et melanger les etats ferait annoncer la deroute d'une
    partie par l'etat d'une autre.
    """
    comptes: dict[str, list[int]] = {}
    total = ruptures = 0

    for etats in battles:
        for echantillon, va_rompre in _samples(etats, horizon):
            total += 1
            ruptures += int(va_rompre)
            for nom, present in echantillon.items():
                case = comptes.setdefault(nom, [0, 0, 0, 0])
                if present:
                    case[0] += 1
                    case[1] += int(va_rompre)
                else:
                    case[2] += 1
                    case[3] += int(va_rompre)

    return MoraleStudy(
        signals=[
            Signal(
                name=nom,
                present=case[0],
                present_then_rout=case[1],
                absent=case[2],
                absent_then_rout=case[3],
            )
            for nom, case in sorted(comptes.items())
        ],
        samples=total,
        routs=ruptures,
    )


def _samples(etats: Sequence[BattleState], horizon: float) -> list[tuple[dict[str, bool], bool]]:
    """Chaque unite alliee encore en ligne, avec ce qu'on voyait d'elle."""
    # Instant de rupture de chaque unite : c'est l'etiquette.
    rupture: dict[str, float] = {}
    for etat in etats:
        for unit in etat.allies(available_only=False):
            if unit.is_routing and unit.id not in rupture:
                rupture[unit.id] = etat.game_time

    sante_passee: dict[str, list[tuple[float, float]]] = {}
    resultat: list[tuple[dict[str, bool], bool]] = []
    for precedent, etat in itertools.pairwise(etats):
        deroutes = [
            unit.position
            for unit in etat.allies(available_only=False)
            if unit.is_routing and unit.is_alive
        ]
        ennemis = etat.side_units(Side.ENEMY, available_only=True)
        seigneur_debout = any(
            unit.role is UnitRole.LORD for unit in etat.allies(available_only=True)
        )

        for unit in etat.allies(available_only=True):
            # Une unite qui a deja rompu n'apprend rien : on cherche ce qui
            # precede la rupture, pas ce qui la suit.
            quand = rupture.get(unit.id)
            if quand is not None and etat.game_time >= quand:
                continue
            historique = sante_passee.setdefault(unit.id, [])
            historique.append((etat.game_time, unit.health_ratio))
            resultat.append(
                (
                    _signals(unit, precedent, deroutes, ennemis, historique, seigneur_debout),
                    quand is not None and quand - etat.game_time <= horizon,
                )
            )
    return resultat


def _signals(
    unit: UnitState,
    precedent: BattleState,
    deroutes: Sequence[Vector3],
    ennemis: Sequence[UnitState],
    historique: list[tuple[float, float]],
    seigneur_debout: bool,
) -> dict[str, bool]:
    """Ce que l'on voit de cette unite, et rien de plus.

    Chaque signal est **booleen a dessein** : on cherche d'abord s'il separe,
    pas de quelle facon. Un seuil ajuste avant d'avoir montre que le signal
    porte serait un reglage sur du bruit.
    """
    voisins = sum(1 for point in deroutes if unit.position.distance_2d(point) <= CONTAGION_RADIUS)
    presse = sum(
        1 for autre in ennemis if unit.position.distance_2d(autre.position) <= PRESSURE_RADIUS
    )
    avant = precedent.unit(unit.id)
    saignee = avant is not None and (avant.health_ratio - unit.health_ratio) > 0.01
    return {
        "au contact": unit.is_engaged,
        "un allie en deroute a moins de 100 m": voisins >= 1,
        "deux allies en deroute a moins de 100 m": voisins >= 2,
        "deux ennemis a moins de 50 m": presse >= 2,
        "trois ennemis a moins de 50 m": presse >= 3,
        "sante sous 50 %": unit.health_ratio < 0.5,
        "sante sous 25 %": unit.health_ratio < 0.25,
        "perd des hommes en ce moment": saignee,
        "perte rapide sur dix secondes": _bleeding(historique, unit.health_ratio),
        "seigneur tombe": not seigneur_debout,
    }


def _bleeding(historique: list[tuple[float, float]], sante: float) -> bool:
    """L'unite a-t-elle perdu plus d'un dixieme de sa force en dix secondes ?"""
    if not historique:
        return False
    limite = historique[-1][0] - 10.0
    anciens = [valeur for instant, valeur in historique if instant <= limite]
    if not anciens:
        return False
    return (anciens[-1] - sante) > 0.10
