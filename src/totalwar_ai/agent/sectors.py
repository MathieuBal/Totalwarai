"""Creer une superiorite locale, plutot que d'etre superieur partout.

**Pourquoi ceci existe.** Face a une ligne adverse, l'agent ne savait que deux
choses : avancer toute la ligne, ou attendre toute la ligne. Les deux ont ete
mesurees contre une armee qui refuse d'avancer, et aucune ne gagne — attente,
tireurs seuls, avance escortee, charge directe : nul, nul, defaite, defaite
(ADR 0015).

Ce qui manque n'est pas un seuil, c'est une capacite. Une armee n'a pas besoin
d'etre superieure partout au meme instant : elle doit l'etre **la ou ca compte**,
et tenir le reste a distance. Le critere n'est donc plus

    force_alliee_totale / force_ennemie_totale >= 1.0

mais, sur une tranche du front choisie exprès,

    force_engagee / (force_adverse_du_secteur + soutien_qui_peut_venir) >= 1.5

Une bataille globalement equilibree peut ainsi devenir localement six contre
trois, pendant que le reste de l'adversaire est fixe ailleurs.

.. rubric:: Ce module decide, il ne mesure pas

Il porte volontairement un autre nom que :mod:`totalwar_ai.learning.concentration`,
qui mesure le rapport de forces local **a posteriori** et ne decide rien. Trois
fois cette session, c'est la mesure independante qui a contredit la doctrine ;
elle ne le peut que si les deux ne partagent pas leur code.

Les deux se rejoignent sur une seule constante, et c'est voulu : `SUPPORT_RADIUS`
vaut la meme chose des deux cotes, sans quoi la mesure ne validerait pas ce que
la decision cherche a faire.

.. rubric:: Ce que le denominateur doit dire

Un secteur faible colle au gros de leur ligne n'est pas faible : c'est un appat.
Sans le terme de soutien, la primitive choisirait systematiquement le detachement
isole le plus proche de leur masse — c'est-a-dire l'endroit ou notre detachement
se ferait envelopper. `Sector.support` compte donc la force adverse **hors
secteur** capable de s'y retourner.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3, centroid
from totalwar_ai.domain.unit_state import UnitState

#: Rayon dans lequel une unite pese sur le combat de sa voisine, en metres.
#:
#: Meme valeur que `planner.SUPPORT_RADIUS` et que
#: `learning.concentration.SUPPORT_RADIUS`. Les trois doivent bouger ensemble :
#: decider sur un rayon et mesurer sur un autre ne validerait rien.
SUPPORT_RADIUS = 40.0

#: Tranches du front. Gauche, centre, droite.
#:
#: Trois et non cinq : un decoupage plus fin produit des secteurs d'une seule
#: unite, ou le rapport local depend entierement de qui a bouge de dix metres.
SECTOR_COUNT = 3

#: Rapport local a atteindre pour qu'un assaut vaille la peine.
#:
#: **En deca, on ne fait rien**, et l'agent se comporte exactement comme avant.
#: C'est cette abstention qui protege les scenarios deja gagnes : la primitive
#: ajoute une manoeuvre, elle n'en remplace aucune.
#:
#: 1.5 et non 1.2 : le rapport est calcule sur des forces residuelles a l'instant
#: du choix, alors que l'assaut met plusieurs dizaines de secondes a se former.
#: La marge paie ce delai.
ASSAULT_RATIO = 1.5

#: Distance au-dela de laquelle une unite ne participe pas a un assaut.
#:
#: Compter toute l'armee dans le numerateur ferait croire a une superiorite que
#: la geometrie interdit : une unite a trois cents metres ne concentre rien, elle
#: arrive apres.
REACH = 200.0

#: Part de la force initiale sous laquelle un secteur est considere rompu.
BREAK_SHARE = 0.35


@dataclass(frozen=True, slots=True)
class Sector:
    """Une tranche du front adverse, et ce qu'elle couterait."""

    index: int
    centre: Vector3
    enemy_ids: tuple[str, ...]
    #: Force adverse **dans** le secteur.
    enemy_strength: float
    #: Force adverse **hors** secteur, capable de s'y retourner. Voir l'en-tete.
    support: float
    #: Notre force capable d'y arriver, et non notre force totale.
    reachable: tuple[str, ...] = ()
    reachable_strength: float = 0.0
    #: Interet des roles presents : tireurs, artillerie, seigneur.
    value: float = 0.0
    distance: float = 0.0

    @property
    def cost(self) -> float:
        """Ce qu'il faut battre : le secteur **et** ce qui peut lui venir en aide."""
        return self.enemy_strength + self.support

    @property
    def local_ratio(self) -> float:
        """Rapport local atteignable. `inf` si le secteur ne coute rien."""
        if self.cost <= 1e-9:
            return float("inf")
        return self.reachable_strength / self.cost

    @property
    def feasible(self) -> bool:
        return bool(self.enemy_ids) and self.local_ratio >= ASSAULT_RATIO

    def explain(self) -> str:
        return (
            f"secteur {self.index} : {len(self.enemy_ids)} ennemi(s), "
            f"cout {self.cost:.2f} (dont {self.support:.2f} de soutien), "
            f"rapport local {self.local_ratio:.2f}"
        )


@dataclass(frozen=True, slots=True)
class Assault:
    """Ce qu'on envoie, ou, et ce qu'on garde ailleurs."""

    sector: int
    centre: Vector3
    #: Unites alliees engagees dans l'assaut.
    attackers: tuple[str, ...]
    #: Ennemis du secteur vise.
    targets: tuple[str, ...]
    ratio: float
    #: Force adverse du secteur au moment du choix, pour juger de la rupture.
    initial_enemy_strength: float = 0.0
    started_at: float = 0.0

    def broken(self, state: BattleState) -> bool:
        """Le secteur a-t-il cede ?

        **Rompre n'est pas poursuivre.** Une fois le secteur enfonce, l'assaut
        est dissous et le choix recommence sur l'etat du moment. Poursuivre
        automatiquement les fuyards a travers la carte est le piege que l'ADR
        0012 a mesure.
        """
        restant = sum(
            unit.effective_strength
            for unit in state.enemies()
            if unit.id in set(self.targets) and unit.is_available
        )
        if self.initial_enemy_strength <= 1e-9:
            return True
        return restant <= self.initial_enemy_strength * BREAK_SHARE

    def live_ratio(self, state: BattleState) -> float:
        """Le rapport local **maintenant**, sur les memes cibles.

        **C'est le chiffre qui manquait.** Le rapport publie jusqu'ici etait celui
        du *choix* : 1,50 sur `outnumbered`. Personne ne savait ce qu'il valait
        une fois les assaillants arrives — et sans ce second chiffre, « le
        probleme est la mobilite » et « le probleme est la conversion » sont
        indiscernables, alors qu'ils appellent des correctifs opposes.

        Meme formule qu'au choix, soutien adverse compris : c'est la comparaison
        qui doit avoir un sens, pas le chiffre pris isolement.
        """
        vises = set(self.targets)
        dedans = [unit for unit in state.enemies() if unit.id in vises and unit.is_available]
        if not dedans:
            return float("inf")
        cout = sum(unit.effective_strength for unit in dedans) + sum(
            unit.effective_strength
            for unit in state.enemies()
            if unit.id not in vises
            and unit.is_available
            and any(unit.position.distance_2d(autre.position) <= SUPPORT_RADIUS for autre in dedans)
        )
        engagee = sum(
            unit.effective_strength
            for unit in state.allies()
            if unit.id in set(self.attackers) and unit.is_available
        )
        return engagee / cout if cout > 1e-9 else float("inf")

    def contact(self, state: BattleState) -> int:
        """Combien d'assaillants sont au contact d'une cible du secteur."""
        vises = set(self.targets)
        return sum(
            1
            for unit in state.allies()
            if unit.id in set(self.attackers)
            and unit.is_engaged
            and any(
                autre.id in vises and unit.position.distance_2d(autre.position) <= SUPPORT_RADIUS
                for autre in state.enemies()
            )
        )

    def explain(self) -> str:
        return (
            f"assaut du secteur {self.sector} : {len(self.attackers)} unite(s) "
            f"sur {len(self.targets)} ennemi(s), rapport {self.ratio:.2f}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "sector": self.sector,
            "attackers": list(self.attackers),
            "targets": list(self.targets),
            "ratio": round(self.ratio, 3),
            "initial_enemy_strength": round(self.initial_enemy_strength, 3),
            "started_at": self.started_at,
        }


@dataclass
class SectorMap:
    """Le front adverse decoupe, avec ce que chaque tranche vaut."""

    sectors: list[Sector] = field(default_factory=list)

    def best(self) -> Sector | None:
        """Le secteur ou frapper, ou `None` s'il n'y en a aucun de tenable.

        Le classement multiplie le rapport local par l'interet des roles
        presents : a rapport egal, une tranche qui tient l'artillerie adverse
        vaut mieux qu'une tranche d'infanterie. **L'identifiant tranche les
        egalites** — la meme situation doit produire le meme choix, sinon le banc
        cesse d'etre reproductible (ADR 0011).
        """
        tenables = [item for item in self.sectors if item.feasible]
        if not tenables:
            return None
        return max(tenables, key=lambda item: (item.local_ratio * (1.0 + item.value), -item.index))

    def render(self) -> str:
        if not self.sectors:
            return "  Aucun secteur : pas d'ennemi visible."
        return "\n".join(f"  {item.explain()}" for item in self.sectors)


def split_sectors(
    state: BattleState,
    front: Vector3,
    allies: Sequence[UnitState],
    *,
    count: int = SECTOR_COUNT,
) -> SectorMap:
    """Decoupe le front adverse en tranches laterales, et evalue chacune.

    La projection se fait sur la perpendiculaire au front — la meme latérale que
    le planificateur emploie pour placer ses ailes. Les tranches sont contigues
    et d'egale largeur : un decoupage par grappes serait plus fin, mais il
    dependrait d'un seuil de plus, et l'on veut d'abord savoir si la manoeuvre
    vaut quelque chose.
    """
    from totalwar_ai.agent.planner import TARGET_PRIORITY, lateral_of

    ennemis = [unit for unit in state.enemies() if unit.is_available]
    if not ennemis or count <= 0:
        return SectorMap()

    lateral = lateral_of(front)
    projete = {
        unit.id: unit.position.x * lateral.x + unit.position.z * lateral.z for unit in ennemis
    }
    gauche = min(projete.values())
    droite = max(projete.values())
    largeur = max(droite - gauche, 1e-6) / count

    ancre = centroid([unit.position for unit in allies]) if allies else Vector3()
    tranches: list[Sector] = []
    for index in range(count):
        borne = gauche + largeur * (index + 1)
        dedans = [
            unit
            for unit in ennemis
            # La derniere tranche prend la borne haute, sinon l'unite la plus a
            # droite n'appartiendrait a aucun secteur.
            if projete[unit.id] < borne or (index == count - 1 and projete[unit.id] <= borne)
        ]
        dedans = [unit for unit in dedans if projete[unit.id] >= gauche + largeur * index]
        if not dedans:
            continue

        ids = tuple(sorted(unit.id for unit in dedans))
        centre = centroid([unit.position for unit in dedans])
        force = sum(unit.effective_strength for unit in dedans)
        # **Le soutien se mesure depuis les membres, pas depuis le centre.** Une
        # tranche fait plus de cent metres de large et `SUPPORT_RADIUS` en fait
        # quarante : mesure depuis le centre, le terme serait vide presque
        # toujours, et l'appat passerait pour un secteur faible. Ce qui menace un
        # assaut, c'est le voisin **de l'unite qu'on attaque**, pas celui du
        # milieu geometrique de la tranche.
        membres = set(ids)
        soutien = sum(
            unit.effective_strength
            for unit in ennemis
            if unit.id not in membres
            and any(unit.position.distance_2d(autre.position) <= SUPPORT_RADIUS for autre in dedans)
        )
        atteignables = [unit for unit in allies if centre.distance_2d(unit.position) <= REACH]
        tranches.append(
            Sector(
                index=index,
                centre=centre,
                enemy_ids=ids,
                enemy_strength=force,
                support=soutien,
                reachable=tuple(sorted(unit.id for unit in atteignables)),
                reachable_strength=sum(unit.effective_strength for unit in atteignables),
                value=max((TARGET_PRIORITY.get(unit.role, 0.4) for unit in dedans), default=0.4),
                distance=ancre.distance_2d(centre),
            )
        )
    return SectorMap(sectors=tranches)


def commit(
    sector: Sector, state: BattleState, allies: Sequence[UnitState], game_time: float
) -> Assault | None:
    """Compose l'assaut : le necessaire, et pas davantage.

    **Envoyer toute l'armee n'est pas concentrer, c'est s'engager.** On ajoute
    les unites par ordre de proximite au secteur jusqu'a depasser
    `ASSAULT_RATIO`, puis on s'arrete : ce qui reste tient le front ailleurs, et
    c'est cette fixation qui empeche l'adversaire de venir retablir la partie
    qu'on attaque.

    Rend `None` si le necessaire n'existe pas — l'agent garde alors son
    comportement habituel.
    """
    disponibles = [unit for unit in allies if unit.id in set(sector.reachable)]
    disponibles.sort(key=lambda unit: (sector.centre.distance_2d(unit.position), unit.id))

    engagees: list[str] = []
    force = 0.0
    besoin = sector.cost * ASSAULT_RATIO
    for unit in disponibles:
        if force >= besoin:
            break
        engagees.append(unit.id)
        force += unit.effective_strength
    if not engagees or (sector.cost > 1e-9 and force < besoin):
        return None

    return Assault(
        sector=sector.index,
        centre=sector.centre,
        attackers=tuple(engagees),
        targets=sector.enemy_ids,
        ratio=force / sector.cost if sector.cost > 1e-9 else float("inf"),
        initial_enemy_strength=sector.enemy_strength,
        started_at=game_time,
    )
