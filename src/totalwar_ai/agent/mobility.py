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
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState

#: Vitesse supposee tant que rien n'a ete observe, en metres par seconde.
#:
#: Ne sert qu'au demarrage a froid, et pour un role inconnu : au premier etat,
#: rien n'a encore bouge, et il faut bien rendre un nombre plutot que refuser de
#: composer un assaut.
DEFAULT_SPEED = 4.0

#: Vitesse presumee par role, tant que l'unite n'a pas ete vue marcher.
#:
#: **Sans cet a priori, la cavalerie n'a aucun avantage au moment ou il compte.**
#: L'assaut se compose au premier plan de la bataille, quand le suivi n'a encore
#: rien observe : tout le monde portait alors la meme vitesse, la cavalerie
#: postee sur l'aile paraissait plus lointaine que l'infanterie, et n'etait
#: jamais retenue. Mesure : zero charge de cavalerie sur toutes les batailles du
#: banc, alors que la branche existait.
#:
#: .. rubric:: Pourquoi ce n'est pas un canal privilegie
#:
#: Le role vient de notre propre classifieur, qui le deduit de la cle d'unite et
#: des etiquettes — information dont l'agent dispose **aussi en bataille reelle**.
#: C'est la difference avec `template.speed`, que seul le simulateur connait et
#: dont l'usage rendrait le banc plus juste et l'agent inapplicable.
#:
#: Ces valeurs n'ont besoin d'etre justes qu'**en ordre** : cavalerie plus rapide
#: qu'infanterie, plus rapide qu'artillerie. Les grandeurs se corrigent d'
#: elles-memes des les premieres secondes de marche.
ROLE_SPEED_PRIOR: dict[UnitRole, float] = {
    UnitRole.LIGHT_CAVALRY: 8.0,
    UnitRole.SHOCK_CAVALRY: 7.5,
    UnitRole.CHARIOT: 7.0,
    UnitRole.FLYING_UNIT: 9.0,
    UnitRole.HERO_MELEE: 6.0,
    UnitRole.HERO_CASTER: 5.5,
    UnitRole.LORD: 6.0,
    UnitRole.MONSTER: 5.5,
    UnitRole.MELEE_INFANTRY: 4.0,
    UnitRole.SPEAR_INFANTRY: 4.0,
    UnitRole.RANGED_INFANTRY: 4.0,
    UnitRole.SUPPORT: 4.0,
    UnitRole.ARTILLERY: 1.5,
}

#: Deplacement en deca duquel un releve n'est que du bruit de position, en metres.
#:
#: **Ce seuil ne protege que contre le tremblement**, aux echantillonnages fins :
#: une formation qui se resserre d'un metre ne dit rien de sa vitesse de marche.
#: Il ne protege de rien aux echantillonnages larges — voir `WALK_SPEED`.
STILL_DISTANCE = 3.0

#: Vitesse en deca de laquelle un releve n'est pas une marche, en metres/seconde.
#:
#: **Ce garde-fou vient d'un defaut mesure, et d'une erreur de raisonnement qui
#: l'avait laisse passer.** `STILL_DISTANCE` est une *distance* : ce qu'elle
#: signifie depend entierement de l'intervalle entre deux releves. Elle a ete
#: calibree pour le pas de simulation — 3 m en 0,5 s valent 6 m/s, une vraie
#: marche — puis employee dans un module alimente **une fois par plan, soit
#: toutes les dix secondes**. Au meme seuil, 3 m en 10 s valent 0,3 m/s : une
#: unite pratiquement immobile.
#:
#: Mesure sur `skirmish_standoff`, releves bruts du premier plan :
#:
#: .. code-block:: text
#:
#:     a_inf2    parcouru=  7.98m en 10.0s ->  0.80 m/s   RETENU
#:     a_cav1    parcouru= 15.96m en 10.0s ->  1.60 m/s   RETENU
#:
#: Un tassement de formation au deploiement, pris pour la vitesse de marche —
#: **et definitivement**, puisque ces unites tiennent ensuite leur position et ne
#: fournissent plus aucun releve. Consequence : une ETA de 240 a 270 secondes
#: vers un secteur distant de deux cents metres, tout le monde ecarte par
#: `ASSAULT_DEADLINE`, et **aucun secteur jamais tenable**.
#:
#: Un seuil exprime en vitesse ne depend pas de l'intervalle, ce qui est
#: precisement la propriete qui manquait.
#:
#: .. rubric:: Pourquoi deux metres par seconde
#:
#: La valeur n'est pas un reglage libre : elle est encadree des deux cotes.
#:
#: * **au-dessus** de tout ce qui a ete mesure comme tassement — 0,80 m/s pour
#:   l'infanterie, 1,60 pour la cavalerie ;
#: * **en dessous** de l'a priori du role le plus lent qui puisse porter un
#:   assaut, l'infanterie a 4,0 m/s. Aucune unite dont l'ETA sert reellement a
#:   decider ne se voit donc interdire d'etre mesuree.
#:
#: L'artillerie, a 1,5 m/s, ne franchira jamais ce seuil et gardera son a priori.
#: **C'est sans consequence** : elle appartient a `RANGED_ROLES`, donc jamais a
#: `ASSAULT_ROLES`, et son ETA n'entre dans aucune decision.
#:
#: .. rubric:: Ce que l'ADR 0018 affirmait a tort
#:
#: Il justifiait `STILL_DISTANCE` par l'accord avec `learning.observation` : « la
#: mesure a posteriori et la decision doivent s'accorder sur ce qui compte comme
#: un deplacement ». **Cet accord n'a jamais existe en fait** — ce module-la
#: travaille sur le releve a 2 Hz, celui-ci sur des plans a 10 s. Deux constantes
#: de meme valeur, et de sens incomparables.
WALK_SPEED = 2.0

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

        **Ne retient que ce qui est une marche**, au sens d'une vitesse et non
        d'une distance : un tassement de formation franchit n'importe quel seuil
        en metres des lors que les releves sont assez espaces, et s'installe
        alors comme la vitesse de l'unite pour le reste de la bataille.
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
            # **Le seuil qui decide est celui-ci, et non la distance.** Voir
            # `WALK_SPEED` : un tassement de formation franchit les trois metres
            # des que les releves sont espaces, et s'installe alors comme la
            # vitesse de l'unite pour toute la bataille.
            if mesuree < WALK_SPEED:
                continue
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

    def speed(self, unit_id: str, role: UnitRole | None = None) -> float:
        """Vitesse observee, en metres par seconde. Jamais nulle.

        A defaut d'observation, l'a priori du role — puis, a defaut de role, la
        vitesse par defaut.
        """
        observee = self._speeds.get(unit_id)
        if observee is not None:
            return max(MINIMUM_SPEED, observee)
        presumee = ROLE_SPEED_PRIOR.get(role, DEFAULT_SPEED) if role is not None else DEFAULT_SPEED
        return max(MINIMUM_SPEED, presumee)

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
        return unit.position.distance_2d(destination) / self.speed(unit.id, unit.role)

    def reset(self) -> None:
        """Oublie ce qui appartenait a la bataille precedente."""
        self._positions.clear()
        self._instants.clear()
        self._speeds.clear()
