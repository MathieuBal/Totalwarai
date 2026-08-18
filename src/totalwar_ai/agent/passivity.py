"""L'adversaire vient-il, et nous coute-t-il quelque chose ?

**Pourquoi ceci existe.** Les deux scenarios du banc qui ne produisent jamais de
victoire partagent un seul mode d'echec, mesure apres deux minutes de bataille :

    skirmish_standoff : 399 ordres, dont 320 « tenir la position », zero attaque
    outnumbered       :  59 ordres, dont 48 deplacements, zero attaque

L'agent passe huit minutes a repeter qu'il tient sa ligne. **Il n'a aucun moyen
de conclure qu'attendre est en train de lui couter la bataille.**

.. rubric:: Une permission, jamais un ordre

Un detecteur d'enlisement a deja ete ecrit, mesure sous quatre formes, et
**supprime** : aucune ne gagnait (ADR 0015). Celui-la *ordonnait une avance* —
attente, tireurs seuls, avance escortee, charge directe : nul, nul, defaite,
defaite.

Celui-ci ne commande rien. Il leve une interdiction : en posture `DEFEND`,
l'assaut de secteur est interdit, et cette interdiction est justifiee — la lever
partout faisait tomber `balanced_clash` de 100 % a 0 %, parce que la manoeuvre
gagnante y est d'attendre sous le feu puis de reculer en tirant.

Quand l'adversaire est **prouve passif**, la supposition qui fonde cette posture
est fausse, et l'assaut redevient examinable. Il garde alors toutes ses
conditions d'abstention : rapport local suffisant sur les seuls roles qui iront
au contact, arrivee groupee, pas pendant un repli tirant, pas une fois les lignes
engagees. Si aucune n'est satisfaite, **il ne se passe rien**.

.. rubric:: Deux faits, pas un delai

Un simple compteur de secondes etait la partie fausse de la regle rejetee. Il
faut deux constats independants :

* **rien ne saigne** — la force adverse totale n'a pas baisse ; nos tirs ne
  mordent pas, donc attendre ne rapporte plus ;
* **rien n'approche** — la distance de leur masse a notre ligne ne diminue pas.

C'est le second qui distingue « il attend » de « il arrive et je ne l'ai pas
encore touche ». Sans lui, la permission serait accordee pendant toute phase
d'approche silencieuse — exactement le cas ou attendre est la bonne reponse.
"""

from __future__ import annotations

from dataclasses import dataclass

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side

#: Duree sans le moindre fait nouveau avant de conclure a la passivite.
#:
#: Assez long pour qu'une approche lente ne soit pas prise pour de l'immobilite,
#: assez court pour qu'il reste du temps de bataille apres le constat.
PASSIVITY_SECONDS = 40.0

#: Baisse de force adverse en deca de laquelle on considere que rien ne saigne.
#:
#: Assez bas pour qu'une seule volee portee compte, assez haut pour qu'un
#: flottement d'arrondi n'empeche jamais de constater l'enlisement.
BLEED_EPSILON = 1e-3

#: Rapprochement en deca duquel on considere que l'adversaire n'avance pas.
#:
#: Trois metres, le meme seuil que le tassement de formation ailleurs : sous
#: cette valeur, la masse adverse a bouge sans venir vers nous.
APPROACH_EPSILON = 3.0


@dataclass
class PassivityWatch:
    """Depuis quand l'adversaire ne fait-il plus rien ?"""

    _since: float | None = None
    _strength: float | None = None
    _distance: float | None = None

    def observe(self, state: BattleState, anchor: Vector3) -> None:
        """Constate ce que l'adversaire a fait depuis le dernier plan."""
        enemies = [unit for unit in state.side_units(Side.ENEMY) if unit.is_available]
        if not enemies:
            self.reset()
            return

        force = sum(unit.effective_strength for unit in enemies)
        distance = min(anchor.distance_2d(unit.position) for unit in enemies)

        saigne = self._strength is not None and force < self._strength - BLEED_EPSILON
        approche = self._distance is not None and distance < self._distance - APPROACH_EPSILON

        # **Le compteur ne repart que sur un fait**, jamais sur le simple passage
        # du temps : c'est ce qui separe cette regle de celle qui a ete rejetee.
        if saigne or approche or self._since is None:
            self._since = state.game_time
        # **On retient le dernier releve, et non le meilleur jamais vu.**
        #
        # La premiere version gardait le minimum, pour qu'un adversaire avancant
        # puis reculant d'un pas ne remette pas le compteur a zero. Mais comparer
        # a la plus courte distance *jamais* atteinte rend tout rapprochement
        # ulterieur indetectable des que l'un des deux camps a recule une fois :
        # mesure sur `balanced_clash`, la distance passe de 35,6 m a 38,4 puis a
        # 200, et `approche` reste faux pour le reste de la bataille — un ennemi
        # qui se serait ressaisi et serait revenu aurait ete tenu pour inerte.
        #
        # C'est aussi ce que la docstring annonce — « ce que l'adversaire a fait
        # depuis le dernier plan » — et que le code ne faisait pas. L'oscillation
        # reste couverte par `APPROACH_EPSILON`, dont c'est le role.
        self._strength = force
        self._distance = distance

    def passive_since(self, game_time: float) -> float:
        """Secondes ecoulees depuis le dernier fait nouveau."""
        return 0.0 if self._since is None else max(0.0, game_time - self._since)

    def passive(self, game_time: float) -> bool:
        """L'adversaire est-il assez inerte pour qu'attendre soit perdre ?"""
        return self.passive_since(game_time) >= PASSIVITY_SECONDS

    def reset(self) -> None:
        """Oublie ce qui appartenait a la bataille precedente."""
        self._since = None
        self._strength = None
        self._distance = None
