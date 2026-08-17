"""Combien de temps une unite met-elle a arriver ?

**Pourquoi ceci existe.** La concentration locale choisit un secteur ou nous
valons 1,50 contre 1, puis compose l'assaut en prenant les unites **les plus
proches**. Mesure sur `outnumbered`, douze graines : le rapport annonce 1,50 au
moment du choix et vaut **0,96 au contact** — l'assaut arrive sous la parite,
apres 31 secondes de trajet, avec 64 % de son avantage.

La cause tient en une ligne : `commit()` triait par distance, et l'agent n'avait
aucune notion de vitesse. Or le simulateur va de **1,6 m/s** pour l'artillerie a
**8,5** pour la cavalerie de choc. A deux cents metres du secteur, ces deux-la
comptaient pour la meme chose dans le numerateur du rapport local — l'une arrive
en vingt-quatre secondes, l'autre en deux minutes, et le rapport annonce
supposait les deux presentes.

.. rubric:: La vitesse s'observe, elle ne se lit pas

Le simulateur connait `template.speed`. **Le jeu ne le donne pas** : le
recensement a essaye `speed`, absent. Batir l'ETA sur la vitesse du gabarit
rendrait le banc plus juste et l'agent inapplicable en bataille reelle — un canal
privilegie de plus, exactement ce que le simulateur a deja coute a debusquer
(voir l'ADR 0015 sur le tir en repli).

La vitesse se deduit donc du **deplacement observe entre deux etats**, canal que
le simulateur et le pont fournissent tous deux a 2 Hz. Si `fast_speed()` repond
au recensement, il deviendra un meilleur point de depart ; il ne remplacera pas
l'observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitState

#: Vitesse supposee tant que rien n'a ete observe, en metres par seconde.
#:
#: La vitesse de base du simulateur. Elle ne sert qu'au demarrage a froid : au
#: premier etat, rien n'a encore bouge, et il faut bien rendre un nombre plutot
#: que refuser de composer un assaut.
DEFAULT_SPEED = 4.0

#: Deplacement en deca duquel une unite est consideree immobile, en metres.
#:
#: Meme valeur que `learning.observation.STILL_DISTANCE`, et c'est deliberé : la
#: mesure a posteriori et la decision doivent s'accorder sur ce qui compte comme
#: un deplacement, sinon la premiere ne valide pas la seconde.
STILL_DISTANCE = 3.0

#: Poids du dernier releve dans la vitesse lissee.
#:
#: **Une unite au contact pietine**, et un releve isole la ferait passer pour
#: immobile — puis pour incapable d'arriver nulle part. Le lissage garde la
#: memoire de ce qu'elle a montre quand elle marchait vraiment.
SMOOTHING = 0.30

#: Vitesse plancher, en metres par seconde.
#:
#: Sans elle, une unite qui n'a jamais bouge aurait une vitesse nulle et un ETA
#: infini : elle serait exclue de tout assaut a jamais, y compris quand c'est
#: precisement elle qu'il faudrait envoyer.
MINIMUM_SPEED = 1.0


@dataclass
class MobilityTracker:
    """Vitesse de chaque unite, deduite de ce qu'elle a reellement parcouru."""

    _positions: dict[str, Vector3] = field(default_factory=dict)
    _instants: dict[str, float] = field(default_factory=dict)
    _speeds: dict[str, float] = field(default_factory=dict)

    def observe(self, state: BattleState) -> None:
        """Releve le deplacement depuis le dernier etat vu.

        Ne retient que les deplacements **au-dela du seuil d'immobilite** : sous
        trois metres, il s'agit de tassement de formation, pas de marche, et en
        tenir compte ferait chuter la vitesse de toute unite arretee.
        """
        for unit in state.side_units(Side.ALLY):
            precedente = self._positions.get(unit.id)
            instant = self._instants.get(unit.id)
            self._positions[unit.id] = unit.position
            self._instants[unit.id] = state.game_time
            if precedente is None or instant is None:
                continue

            ecoule = state.game_time - instant
            if ecoule <= 1e-6:
                continue
            parcouru = precedente.distance_2d(unit.position)
            if parcouru < STILL_DISTANCE:
                continue

            mesuree = parcouru / ecoule
            connue = self._speeds.get(unit.id)
            if connue is None:
                self._speeds[unit.id] = mesuree
            else:
                # **On retient le plus rapide observe, lisse.** Une unite montre
                # sa vraie vitesse quand elle marche librement, jamais quand elle
                # contourne un obstacle ou piétine derriere une voisine : la
                # moyenne des deplacements sous-estimerait systematiquement ce
                # dont elle est capable.
                cible = max(connue, mesuree)
                self._speeds[unit.id] = connue + SMOOTHING * (cible - connue)

    def speed(self, unit_id: str) -> float:
        """Vitesse observee, en metres par seconde. Jamais nulle."""
        return max(MINIMUM_SPEED, self._speeds.get(unit_id, DEFAULT_SPEED))

    def observed(self, unit_id: str) -> bool:
        """A-t-on vu cette unite marcher, ou lui prete-t-on la vitesse par defaut ?"""
        return unit_id in self._speeds

    def eta(self, unit: UnitState, destination: Vector3) -> float:
        """Secondes avant que cette unite n'atteigne ce point.

        Distance a vol d'oiseau : le jeu expose `can_reach_position`, jamais une
        longueur de chemin. Ce que l'on calcule est donc un **plancher** — le
        trajet reel ne peut qu'etre plus long. C'est suffisant pour comparer deux
        unites entre elles, ce qui est le seul usage.
        """
        return unit.position.distance_2d(destination) / self.speed(unit.id)

    def reset(self) -> None:
        """Oublie ce qui appartenait a la bataille precedente."""
        self._positions.clear()
        self._instants.clear()
        self._speeds.clear()
