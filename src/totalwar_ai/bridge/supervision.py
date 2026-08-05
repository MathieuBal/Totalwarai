"""Supervision de l'IA de bataille du jeu.

L'IA de Creative Assembly connait le terrain, le pathfinding et les formations —
tout ce qui nous manque. Elle joue donc mieux que notre agent, et lui confier
l'armee est le chemin le plus court vers un mod qui fonctionne.

Mais elle a des angles morts, et c'est un fait etabli : AI General 3 lui-meme
consacre l'essentiel de son code a les contourner — module de poursuite des
fuyards, gestion du tir a volonte, exclusion du seigneur. Un mod qui se
contentait de deleguer n'aurait pas eu besoin de tout cela.

**Ce module ne remplace pas l'IA du jeu : il la surveille.** Elle mene la
bataille ; nos regles observent le resultat et reprennent la seule unite dont
elle fait mauvais usage, avec un motif lisible. L'unite lui est rendue des que
la situation est retablie.

C'est la difference avec les regles de :mod:`totalwar_ai.agent.safety_rules`,
qui jugent une action **proposee** avant qu'elle ne parte. Ici il n'y a aucune
action a juger : on ne voit que l'etat, et c'est de lui qu'il faut conclure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import RANGED_ROLES, Side, UnitRole, UnitState

#: Distance a laquelle une unite reprise se replie derriere la ligne, en metres.
RETREAT_DISTANCE = 90.0

#: Sante en deca de laquelle le seigneur est retire du combat.
#:
#: Sa mort coute la bataille bien au-dela des hommes qu'il represente. L'IA du
#: jeu ne fait pas cette difference : AI General offre d'ailleurs une option
#: dediee pour ne jamais lui confier le seigneur.
LORD_CRITICAL_HEALTH = 0.35


@dataclass(frozen=True, slots=True)
class Intervention:
    """Reprise d'une unite a l'IA du jeu, avec son motif."""

    unit_id: str
    rule: str
    reason: str
    #: Ou envoyer l'unite une fois reprise. `None` : l'immobiliser sur place.
    destination: Vector3 | None = None

    def explain(self) -> str:
        """Format identique a celui de l'agent, pour un journal homogene."""
        return f"Reprise : {self.unit_id}\nRegle   : {self.rule}\nCause   : {self.reason}"


class SupervisionRule(ABC):
    """Juge l'etat observe d'une unite confiee a l'IA du jeu."""

    name: str = "regle"

    @abstractmethod
    def check(self, unit: UnitState, state: BattleState) -> Intervention | None:
        """Renvoie une reprise si l'unite est mal employee, sinon `None`."""


class ArtilleryInMeleeRule(SupervisionRule):
    """L'artillerie au contact est perdue : elle ne se defend pas.

    Regle 1 du Ticket 001, transposee a l'observation. Le planificateur du jeu
    ne protege pas specifiquement les pieces d'artillerie engagees.
    """

    name = "artillerie_au_contact"

    def check(self, unit: UnitState, state: BattleState) -> Intervention | None:
        if unit.role is not UnitRole.ARTILLERY or not unit.is_engaged:
            return None
        return Intervention(
            unit_id=unit.id,
            rule=self.name,
            reason="artillerie prise au corps a corps : elle ne s'en sortira pas seule",
            destination=_behind_the_line(unit, state),
        )


class RangedInMeleeRule(SupervisionRule):
    """Un tireur au contact perd tout ce qui fait sa valeur — **s'il lui reste
    de quoi tirer**.

    Regle 2 du Ticket 001. C'est aussi l'angle mort que le module de tir a
    volonte d'AI General cherche a compenser.

    **La condition de munitions n'est pas un detail : sans elle, cette regle
    faisait perdre des batailles.** Mesuree seule au banc supervise, sur onze
    scenarios et trois graines, elle faisait tomber l'ensemble de 30/33
    victoires a 27/33, en vingt-sept reprises. Avec elle, le jeu de regles
    complet revient a 30/33 — le niveau atteint sans aucune supervision — pour
    quinze reprises et douze points de survie du seigneur gagnes.

    L'explication tient en une phrase. Un tireur a court de munitions n'est
    plus qu'une unite de melee mediocre ; le degager ne lui rend aucune valeur,
    ouvre un trou dans la ligne, et il se fait rattraper en chemin. Ni la
    distance de repli, ni sa sante, ni le nombre d'assaillants ne changeaient
    quoi que ce soit — les munitions expliquent tout l'ecart.
    """

    name = "tireur_au_contact"

    def check(self, unit: UnitState, state: BattleState) -> Intervention | None:
        if unit.role not in RANGED_ROLES or not unit.is_engaged:
            return None
        if unit.ammo_ratio <= 0.0:
            return None
        return Intervention(
            unit_id=unit.id,
            rule=self.name,
            reason="unite de tir au corps a corps : la degager pour qu'elle tire a nouveau",
            destination=_behind_the_line(unit, state),
        )


class WoundedLordRule(SupervisionRule):
    """Un seigneur mourant coute plus que les hommes qu'il represente.

    L'IA du jeu ne fait pas cette difference. AI General offre une option
    dediee — `ALL_BUT_LORD` — precisement pour ne jamais le lui confier.
    """

    name = "seigneur_en_danger"

    def check(self, unit: UnitState, state: BattleState) -> Intervention | None:
        if unit.role is not UnitRole.LORD:
            return None
        if unit.health_ratio > LORD_CRITICAL_HEALTH:
            return None
        return Intervention(
            unit_id=unit.id,
            rule=self.name,
            reason=f"seigneur a {unit.health_ratio:.0%} : le retirer du combat",
            destination=_behind_the_line(unit, state),
        )


#: Regles appliquees, dans l'ordre. La premiere qui repond gagne pour une unite.
#:
#: **Les regles de detresse passent en premier.** Sauver une unite coute moins
#: cher que saisir une occasion, et le budget de reprises est borne.
DEFAULT_RULES: tuple[SupervisionRule, ...] = (
    WoundedLordRule(),
    ArtilleryInMeleeRule(),
    RangedInMeleeRule(),
)


@dataclass
class Supervisor:
    """Surveille les unites confiees a l'IA du jeu."""

    rules: tuple[SupervisionRule, ...] = DEFAULT_RULES
    #: Unites reprises, avec le temps de jeu de la reprise.
    reclaimed: dict[str, float] = field(default_factory=dict)
    #: Delai avant de rendre une unite reprise, en secondes de jeu.
    #:
    #: Assez long pour que le repli aboutisse ; la rendre trop tot la ferait
    #: renvoyer au contact par l'IA du jeu, et la reprise recommencerait —
    #: l'unite ferait des allers-retours au lieu de se degager.
    cooldown_seconds: float = 20.0

    def review(self, state: BattleState, delegated: set[str]) -> list[Intervention]:
        """Unites a reprendre a l'IA du jeu, avec leur motif.

        Une unite deja reprise n'est pas reprise deux fois : elle est sous notre
        controle, pas sous le sien.
        """
        interventions: list[Intervention] = []
        for unit in state.allies():
            if unit.id not in delegated or unit.id in self.reclaimed:
                continue
            for rule in self.rules:
                verdict = rule.check(unit, state)
                if verdict is not None:
                    interventions.append(verdict)
                    self.reclaimed[unit.id] = state.game_time
                    break
        return interventions

    def ready_to_return(self, state: BattleState) -> list[str]:
        """Unites reprises que l'on peut rendre a l'IA du jeu.

        Deux conditions : le delai est ecoule, et la situation qui a motive la
        reprise a cesse. Rendre une unite encore au contact la remettrait dans
        l'etat exact qui a declenche la reprise.
        """
        pretes: list[str] = []
        for unit_id, moment in self.reclaimed.items():
            if state.game_time - moment < self.cooldown_seconds:
                continue
            unit = state.unit(unit_id)
            if unit is None:
                pretes.append(unit_id)  # morte ou disparue : plus rien a suivre
                continue
            if any(rule.check(unit, state) is not None for rule in self.rules):
                continue
            pretes.append(unit_id)
        return pretes

    @property
    def held(self) -> set[str]:
        """Unites que la supervision tient en ce moment.

        Une unite reprise est a nous **volontairement** : la reconfier a l'IA du
        jeu annulerait la correction dans le tour meme ou elle est donnee.
        """
        return set(self.reclaimed)

    def forget(self, unit_ids: list[str]) -> None:
        """Oublie des unites rendues a l'IA du jeu."""
        for unit_id in unit_ids:
            self.reclaimed.pop(unit_id, None)


def _behind_the_line(unit: UnitState, state: BattleState) -> Vector3:
    """Point de repli, a l'oppose de l'ennemi le plus proche.

    Se replier vers le centre de notre armee ne suffit pas : si la menace vient
    de l'arriere, cela reviendrait a courir vers elle. On s'ecarte donc de
    l'ennemi qui a provoque la reprise.
    """
    enemies = state.enemies()
    if not enemies:
        return unit.position
    menace = min(enemies, key=lambda enemy: unit.position.distance_2d(enemy.position))
    fuite = menace.position.direction_to(unit.position)
    if fuite.length_2d() <= 1e-9:
        fuite = state.centroid(Side.ENEMY).direction_to(state.centroid(Side.ALLY))
    return unit.position + fuite.scaled(RETREAT_DISTANCE)
