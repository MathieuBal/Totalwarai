"""Doublure de l'IA de bataille du moteur, pour mesurer la supervision.

**Pourquoi ce module existe.** La supervision ne se juge que contre une IA qui
mene l'armee. Dans le jeu, c'est `script_ai_planner` ; au banc, il n'y avait
rien — chaque regle coutait donc une bataille reelle, et le projet n'avancait
plus qu'au rythme des essais de l'operateur.

Ce module fait jouer nos propres unites par la meme politique scriptee qui
mene deja l'adversaire depuis onze scenarios : avancer, tirer a portee, lancer
la cavalerie sur les tireurs. La supervision peut alors reprendre des unites,
et l'on mesure ce que ses regles apportent — au banc, en quelques secondes.

.. warning::

   **Ce n'est pas l'IA du jeu.** Elle ignore le terrain, les formations et le
   pathfinding, que le moteur maitrise. C'est un **filtre rapide, pas un juge**.

   L'ADR 0005 documente deux corrections que le simulateur a declarees bonnes
   et que le jeu a dementies — l'une faisait passer `balanced_clash` de 100 % a
   0 %. Une regle validee ici reste a confirmer en bataille reelle avant d'etre
   presentee comme un gain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from totalwar_ai.domain.unit_state import RANGED_ROLES, UnitRole

if TYPE_CHECKING:
    from totalwar_ai.simulation.environment import Order, SimUnit

#: Roles qui vont chercher les tireurs adverses plutot que le plus proche.
#:
#: Comportement classique du moteur, et la raison pour laquelle nos propres
#: tireurs ont besoin d'etre proteges — la regle `tireur_au_contact` existe
#: precisement pour cela.
_CHASSEURS: frozenset[UnitRole] = frozenset(
    {UnitRole.LIGHT_CAVALRY, UnitRole.SHOCK_CAVALRY, UnitRole.FLYING_UNIT}
)


def scripted_order(unit: SimUnit, opponents: list[SimUnit], *, waits: bool = False) -> Order | None:
    """Ordre que la doublure donne a une unite, ou `None` s'il n'y a rien a faire.

    `waits` reproduit l'escarmouche : l'unite tire sur ce qui entre a portee et
    repond au contact, mais n'avance jamais. C'est la situation ou deux camps
    qui attendent produisent un match nul — cas du scenario `skirmish_standoff`.
    """
    # Import differe : `environment` importe ce module, et ces types y vivent
    # avec le reste de la mecanique de simulation. Le faire ici est ce qui
    # permet aux deux modules de se connaitre sans boucler a l'import.
    from totalwar_ai.simulation.environment import Order, OrderKind

    if unit.is_engaged or not opponents:
        return None

    preferred = opponents
    if unit.role in _CHASSEURS:
        exposes = [other for other in opponents if other.role in RANGED_ROLES]
        if exposes:
            preferred = exposes

    target = min(preferred, key=lambda other: unit.position.distance_2d(other.position))
    distance = unit.position.distance_2d(target.position)

    if unit.template.is_ranged and unit.ammo > 0 and distance <= unit.template.missile_range:
        return Order(kind=OrderKind.FIRE, target_id=target.id)
    if waits:
        # Ni poursuite, ni approche : le contact viendra de l'autre camp, ou
        # n'aura pas lieu.
        return Order(kind=OrderKind.HOLD)
    return Order(kind=OrderKind.ATTACK, target_id=target.id)
