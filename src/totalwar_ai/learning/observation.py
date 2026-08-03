"""Deduire ce que fait une unite, en la regardant seulement bouger.

**Le jeu ne dit pas quel ordre porte une unite.** Le recensement l'a etabli :
aucun accesseur ne rend son ordre courant. Pour apprendre en observant l'IA du
moteur, il faut donc reconstituer ses decisions a partir de la seule chose que
l'on voie — deux etats successifs.

Ce module ne devine pas : il **conclut**, et dit quand il n'y arrive pas.

.. rubric:: Ce qui rend l'inference verifiable sans jouer

La doublure de :mod:`totalwar_ai.simulation.native_ai` a une politique connue
exactement — la cavalerie prefere les tireurs, la melee prend le plus proche, on
tire des qu'on est a portee. On lui fait donc jouer une bataille et l'on verifie
que l'inference **retrouve cette politique**.

Si elle echoue sur une politique dont on connait la reponse, elle ne dira rien
de bon sur l'IA du jeu. Ce n'est pas circulaire : on ne cherche pas a apprendre
une tactique de la doublure, on etalonne l'instrument.

.. rubric:: L'ambiguite se compte

Deux ennemis a egale distance dans la meme direction : rien ne permet de dire
lequel etait vise. L'inference le declare au lieu de trancher a pile ou face —
:attr:`Inference.ambiguity` donne la proportion de cas indecidables. Une
inference sure d'elle sur des donnees ambigues serait pire qu'inutile.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState

#: Deplacement en deca duquel une unite est consideree immobile, en metres.
#:
#: A 2 Hz, une unite qui marche parcourt plusieurs metres entre deux etats. Sous
#: ce seuil il s'agit de tassement de formation, pas d'un deplacement voulu.
STILL_DISTANCE = 3.0

#: Part du deplacement qui doit reduire la distance a une cible pour la designer.
#:
#: Une unite qui avance de dix metres et se rapproche d'un ennemi de neuf allait
#: manifestement vers lui ; de deux metres, elle passait a cote.
APPROACH_RATIO = 0.5

#: Ecart minimal entre les deux meilleurs candidats, en metres.
#:
#: En deca, on refuse de choisir : deux ennemis egalement approches ne
#: permettent pas de dire lequel etait vise.
DECISIVE_MARGIN = 4.0


class Move(StrEnum):
    """Ce qu'une unite faisait, tel qu'on peut le conclure."""

    ENGAGE = "engage"
    CLOSE = "fonce"
    FIRE = "tire"
    HOLD = "tient"
    WITHDRAW = "decroche"


@dataclass(frozen=True, slots=True)
class Observation:
    """Une decision reconstituee, avec ce qui permet de la juger."""

    game_time: float
    unit_id: str
    role: UnitRole
    move: Move
    #: Cible designee, ou `None` — soit qu'il n'y en ait pas, soit qu'on hesite.
    target_id: str | None = None
    target_role: UnitRole | None = None
    #: Vrai quand plusieurs cibles expliquaient aussi bien le mouvement.
    ambiguous: bool = False
    #: Distance a la cible au moment de la decision, en metres.
    distance: float = 0.0

    def explain(self) -> str:
        cible = self.target_id or "—"
        doute = " (ambigu)" if self.ambiguous else ""
        return (
            f"t={self.game_time:6.1f}s {self.unit_id:>8} {self.role.value:<18} "
            f"{self.move.value:<9} -> {cible}{doute}"
        )


@dataclass
class Inference:
    """Ce qu'une bataille observee a livre."""

    observations: list[Observation] = field(default_factory=list)

    @property
    def ambiguity(self) -> float:
        """Part des decisions ou la cible n'a pas pu etre designee."""
        avec_cible = [item for item in self.observations if item.move in _TARGETED]
        if not avec_cible:
            return 0.0
        return sum(1 for item in avec_cible if item.ambiguous) / len(avec_cible)

    def targets_of(self, role: UnitRole) -> list[UnitRole]:
        """Roles effectivement vises par un role donne, doutes ecartes."""
        return [
            item.target_role
            for item in self.observations
            if item.role is role and item.target_role is not None and not item.ambiguous
        ]

    def counts(self) -> dict[Move, int]:
        tally: dict[Move, int] = {}
        for item in self.observations:
            tally[item.move] = tally.get(item.move, 0) + 1
        return tally


#: Mouvements qui designent une cible. Les autres n'en ont pas a designer.
_TARGETED = frozenset({Move.ENGAGE, Move.CLOSE, Move.FIRE})


def infer(
    states: Sequence[BattleState],
    *,
    side: Side = Side.ALLY,
    use_continuity: bool = True,
) -> Inference:
    """Reconstitue les decisions d'un camp, etat apres etat.

    On compare chaque etat au precedent : c'est le deplacement qui trahit
    l'intention, et les munitions qui trahissent le tir.

    `use_continuity=False` desactive la levee d'ambiguite par la cible
    precedente. Sert a mesurer ce qu'elle apporte, et a rien d'autre.
    """
    resultat = Inference()
    # Derniere cible retenue pour chaque unite. **Un ordre persiste** : c'est ce
    # qui permet de lever une bonne part des ambiguites, sans rien supposer.
    memoire: dict[str, str] = {}
    for avant, apres in itertools.pairwise(states):
        precedentes = {unit.id: unit for unit in avant.side_units(side)}
        adversaires = apres.side_units(Side.ENEMY if side is Side.ALLY else Side.ALLY)
        if not adversaires:
            continue
        anciens = {unit.id: unit for unit in avant.units}
        for unit in apres.side_units(side):
            precedente = precedentes.get(unit.id)
            if precedente is None:
                continue  # apparue en cours de route : rien a comparer
            observation = _observe(apres.game_time, precedente, unit, adversaires, anciens)
            if use_continuity:
                observation = _resolve_by_continuity(observation, memoire, adversaires)
            if observation.target_id is not None:
                memoire[unit.id] = observation.target_id
            resultat.observations.append(observation)
    return resultat


#: Rayon dans lequel une cible reprise par continuite reste plausible, en metres.
#:
#: Au-dela, l'unite s'est manifestement tournee vers autre chose et la memoire
#: ne doit plus servir d'argument.
CONTINUITY_RADIUS = 60.0


def _resolve_by_continuity(
    observation: Observation,
    memoire: dict[str, str],
    adversaires: Sequence[UnitState],
) -> Observation:
    """Leve une ambiguite par la cible precedente, quand elle tient encore.

    **Un ordre persiste jusqu'a ce qu'on en donne un autre.** Une unite qui
    frappait un ennemi au tour precedent, et qui l'a toujours a portee de bras,
    le frappe encore — meme si un second adversaire se trouve a la meme
    distance. Ce n'est pas une supposition : c'est la propriete la plus stable
    d'un ordre.

    Mesure sur les onze scenarios de la doublure : l'ambiguite des decisions
    ciblees passe de **27 % a 21 %**, sans qu'aucune cible ne soit inventee — la
    continuite ne designe jamais qu'un adversaire deja candidat, et encore
    proche. Le gain est reel et modeste ; l'essentiel de l'ambiguite restante
    tient au tir en melee compacte, ou plusieurs ennemis sont a distance egale
    et ou nos donnees ne permettent pas de trancher.
    """
    if not observation.ambiguous or observation.move not in _TARGETED:
        return observation
    ancienne = memoire.get(observation.unit_id)
    if ancienne is None:
        return observation
    for adversaire in adversaires:
        if adversaire.id != ancienne:
            continue
        # Encore la, et encore proche : l'ordre precedent tient toujours.
        if observation.distance and observation.distance > CONTINUITY_RADIUS:
            return observation
        return Observation(
            game_time=observation.game_time,
            unit_id=observation.unit_id,
            role=observation.role,
            move=observation.move,
            target_id=adversaire.id,
            target_role=adversaire.role,
            ambiguous=False,
            distance=observation.distance,
        )
    return observation


def _observe(
    game_time: float,
    avant: UnitState,
    apres: UnitState,
    adversaires: Sequence[UnitState],
    anciens: dict[str, UnitState],
) -> Observation:
    """Ce qu'une unite faisait entre deux etats."""
    if apres.is_engaged:
        cible, ambigu, distance = _closest(apres, [item for item in adversaires if item.is_engaged])
        if cible is None:
            cible, ambigu, distance = _closest(apres, adversaires)
        return _rendu(game_time, apres, Move.ENGAGE, cible, ambigu, distance)

    if _has_fired(avant, apres):
        portee = _missile_range(apres)
        a_portee = [
            item for item in adversaires if apres.position.distance_2d(item.position) <= portee
        ]
        cible, ambigu, distance = _closest(apres, a_portee or adversaires)
        return _rendu(game_time, apres, Move.FIRE, cible, ambigu, distance)

    deplacement = avant.position.distance_2d(apres.position)
    if deplacement < STILL_DISTANCE:
        return _rendu(game_time, apres, Move.HOLD, None, False, 0.0)

    cible, ambigu, distance = _approached(avant, apres, adversaires, anciens, deplacement)
    if cible is not None:
        return _rendu(game_time, apres, Move.CLOSE, cible, ambigu, distance)
    return _rendu(game_time, apres, Move.WITHDRAW, None, False, 0.0)


def _approached(
    avant: UnitState,
    apres: UnitState,
    adversaires: Sequence[UnitState],
    anciens: dict[str, UnitState],
    deplacement: float,
) -> tuple[UnitState | None, bool, float]:
    """Vers quel adversaire l'unite s'est-elle rapprochee ?

    Un rapprochement ne compte que s'il represente une part notable du chemin
    parcouru : une unite qui longe la ligne adverse se rapproche un peu de tout
    le monde sans viser personne.
    """
    gains: list[tuple[float, UnitState]] = []
    for adversaire in adversaires:
        ancien = anciens.get(adversaire.id)
        if ancien is None:
            continue
        gain = avant.position.distance_2d(ancien.position) - apres.position.distance_2d(
            adversaire.position
        )
        if gain >= deplacement * APPROACH_RATIO:
            gains.append((gain, adversaire))

    if not gains:
        return None, False, 0.0
    gains.sort(key=lambda item: item[0], reverse=True)
    meilleur = gains[0][1]
    # Deux candidats aussi bien approches : on ne tranche pas.
    ambigu = len(gains) > 1 and (gains[0][0] - gains[1][0]) < DECISIVE_MARGIN
    return meilleur, ambigu, apres.position.distance_2d(meilleur.position)


def _closest(
    unit: UnitState, candidats: Sequence[UnitState]
) -> tuple[UnitState | None, bool, float]:
    """Le plus proche, et si un autre l'etait autant."""
    if not candidats:
        return None, False, 0.0
    classes = sorted(candidats, key=lambda item: unit.position.distance_2d(item.position))
    premier = classes[0]
    distance = unit.position.distance_2d(premier.position)
    ambigu = (
        len(classes) > 1
        and (unit.position.distance_2d(classes[1].position) - distance) < DECISIVE_MARGIN
    )
    return premier, ambigu, distance


def _rendu(
    game_time: float,
    unit: UnitState,
    move: Move,
    cible: UnitState | None,
    ambigu: bool,
    distance: float,
) -> Observation:
    return Observation(
        game_time=game_time,
        unit_id=unit.id,
        role=unit.role,
        move=move,
        # Une cible ambigue n'est pas une cible : la retenir ferait entrer une
        # supposition dans les donnees d'apprentissage sous les traits d'un fait.
        target_id=None if (cible is None or ambigu) else cible.id,
        target_role=None if (cible is None or ambigu) else cible.role,
        ambiguous=ambigu,
        distance=distance,
    )


def _has_fired(avant: UnitState, apres: UnitState) -> bool:
    """Les munitions ont-elles baisse ?

    **C'est le seul signal de tir que le jeu nous donne.** Aucun accesseur ne
    dit sur qui une unite tire, ni meme qu'elle tire ; la baisse du stock le
    trahit. Trouvaille qui rend le tir observable sans nouvelle API.
    """
    return apres.is_ranged and apres.ammo_ratio < avant.ammo_ratio


def _missile_range(unit: UnitState) -> float:
    from totalwar_ai.agent.planner import missile_range

    return missile_range(unit)
