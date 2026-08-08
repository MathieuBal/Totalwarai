"""Nos degats ont-ils fait tomber quelque chose ?

**Pourquoi ceci existe.** Sur les deux batailles jouees, l'agent a retire
**9,77 et 5,30 unites-equivalent** de points de vie a l'adversaire — de quoi
abattre dix regiments dans un cas, cinq dans l'autre. Il n'en a detruit
**aucun**. Les degats etaient etales sur dix-neuf des vingt regiments adverses,
51 % de leur barre chacun en moyenne.

C'est la signature du jeu debutant, et elle se chiffre. Un regiment a 50 % se
bat encore. S'il rompt, il se rallie et il revient. Un regiment abattu ne revient
jamais et ne rend plus un seul coup — **detruire une unite, c'est supprimer ses
degats futurs**, ce que l'entamer ne fait pas.

Ce module compte ce que nos degats ont reellement achete. Il ne juge pas le
resultat de la bataille : on peut perdre en ayant bien joue, mais pas en ayant
egratigne vingt regiments sans en abattre un.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.unit_state import Side

#: Perte de barre en deca de laquelle on considere l'unite intacte.
#:
#: Sous ce seuil, il s'agit de tirs perdus ou d'un contact fugace, pas d'un
#: engagement.
SCRATCH = 0.02

#: Rendement en deca duquel les degats sont etales.
#:
#: Un tue par unite-equivalent infligee serait le rendement parfait. Le banc
#: tourne autour de 0,40 ; les batailles jouees etaient a **0,00**. Un cinquieme
#: est le seuil en dessous duquel on ne concentre manifestement rien.
SPREAD_YIELD = 0.20


@dataclass
class Attrition:
    """Ce que nos degats ont achete, adversaire par adversaire."""

    #: Perte de barre subie par chaque unite adverse, sur toute la bataille.
    damage: dict[str, float] = field(default_factory=dict)
    #: Unites adverses qui ont disparu du champ de bataille.
    destroyed: set[str] = field(default_factory=set)
    #: Unites adverses vues en deroute au moins une fois.
    routed: set[str] = field(default_factory=set)

    @property
    def seen(self) -> int:
        return len(self.damage)

    @property
    def engaged(self) -> int:
        """Unites adverses reellement entamees."""
        return sum(1 for perte in self.damage.values() if perte > SCRATCH)

    @property
    def total_damage(self) -> float:
        """Degats totaux, en unites-equivalent."""
        return sum(self.damage.values())

    @property
    def yield_per_damage(self) -> float:
        """Unites abattues par unite-equivalent de degats infliges.

        C'est le chiffre qui distingue un combat concentre d'un combat etale :
        la meme quantite de degats peut abattre cinq regiments ou n'en abattre
        aucun, selon qu'on l'a portee au meme endroit ou partout.
        """
        if self.total_damage <= 0.0:
            return 0.0
        return len(self.destroyed) / self.total_damage

    @property
    def average_damage(self) -> float:
        """Part de barre retiree en moyenne aux unites entamees."""
        entamees = [perte for perte in self.damage.values() if perte > SCRATCH]
        return sum(entamees) / len(entamees) if entamees else 0.0

    @property
    def spread(self) -> bool:
        """Les degats ont-ils ete etales sans rien faire tomber ?"""
        return self.total_damage >= 1.0 and self.yield_per_damage < SPREAD_YIELD

    def render(self) -> str:
        if not self.damage:
            return "  Aucune unite adverse observee."
        lignes = [
            f"  unites adverses vues     : {self.seen}",
            f"  entamees                 : {self.engaged}"
            f" ({self.average_damage:.0%} de leur barre en moyenne)",
            f"  mises en deroute         : {len(self.routed)}",
            f"  **detruites**            : {len(self.destroyed)}",
            "",
            f"  degats infliges          : {self.total_damage:.2f} unite-equivalent",
            f"  rendement                : {self.yield_per_damage:.2f} "
            "unite abattue par unite-equivalent",
        ]
        if self.spread:
            manques = int(self.total_damage) - len(self.destroyed)
            lignes += [
                "",
                "**Nos degats sont etales.** De quoi abattre "
                f"{self.total_damage:.0f} regiment(s), "
                f"{len(self.destroyed)} abattu(s) :",
                f"  {manques} de perdus a egratigner tout le monde. Un regiment a demi",
                "  entame se bat encore, et s'il rompt il se rallie ; un regiment",
                "  abattu ne revient jamais.",
            ]
        return "\n".join(lignes)


def study(states: Iterable[BattleState]) -> Attrition:
    """Ce que nos degats ont fait tomber, sur toute la bataille.

    Une unite est comptee detruite si elle **cesse d'apparaitre vivante** : le
    jeu retire les unites detruites de ses listes, et attendre un compte d'hommes
    a zero ne verrait jamais aucune mort.
    """
    rapport = Attrition()
    depart: dict[str, float] = {}
    dernier: dict[str, float] = {}
    vivantes: set[str] = set()

    for etat in states:
        presentes: set[str] = set()
        for unite in etat.side_units(Side.ENEMY):
            if not unite.is_alive:
                continue
            presentes.add(unite.id)
            barre = unite.health_ratio * unite.entity_ratio
            depart.setdefault(unite.id, barre)
            dernier[unite.id] = barre
            if unite.is_routing:
                rapport.routed.add(unite.id)
        vivantes = presentes

    for unite_id, initiale in depart.items():
        detruite = unite_id not in vivantes
        if detruite:
            rapport.destroyed.add(unite_id)
        restante = 0.0 if detruite else dernier[unite_id]
        rapport.damage[unite_id] = max(0.0, initiale - restante)

    return rapport
