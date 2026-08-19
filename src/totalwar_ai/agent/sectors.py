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
from enum import StrEnum

from totalwar_ai.agent.mobility import MobilityTracker
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3, centroid
from totalwar_ai.domain.unit_state import MOBILE_ROLES, RANGED_ROLES, UnitRole, UnitState

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

#: Delai au-dela duquel une unite ne participe pas a un assaut, en secondes.
#:
#: **`REACH` etait une distance, et c'etait le defaut.** Compter toute l'armee
#: dans le numerateur fait croire a une superiorite que la geometrie interdit ;
#: mais un seuil en metres traite une piece d'artillerie a 1,6 m/s et une
#: cavalerie de choc a 8,5 comme equivalentes a distance egale. L'une arrive en
#: vingt-quatre secondes, l'autre en deux minutes.
#:
#: **Ce seuil ne doit pas mordre, et c'est mesure.** Regle d'abord a 45 s — le
#: delai median jusqu'au contact valait 31 s — il a produit **zero assaut sur
#: douze graines** : au premier plan, aucune unite n'a encore ete vue marcher,
#: chacune porte donc la vitesse par defaut, et 45 s a 4 m/s ne fait que 180 m
#: quand la ligne adverse est a plus de deux cents.
#:
#: La lecon est que le delai absolu n'est pas le bon instrument. Deux unites a
#: trois cents metres qui arrivent **ensemble** portent un coup concentre ; ce
#: qui tue un assaut est la dispersion, dont `ASSAULT_WINDOW` se charge. Le
#: delai ne sert donc qu'a ecarter ce qui ne peut pas arriver du tout : au-dela
#: d'une minute et demie, l'assaut decide maintenant appartient a une autre
#: bataille.
ASSAULT_DEADLINE = 90.0

#: Ecart d'arrivee tolere au sein d'un assaut, en secondes.
#:
#: **Un assaut n'est fort que si ses unites arrivent groupees.** Trois unites qui
#: arrivent a vingt secondes d'intervalle livrent trois combats a un contre un,
#: pas un combat a trois contre un — c'est la defaite en detail, appliquee a
#: nous-memes et de notre propre initiative.
ASSAULT_WINDOW = 15.0

#: Roles qui peuvent porter un assaut, c'est-a-dire recevoir l'ordre d'attaquer.
#:
#: **Le numerateur ne doit compter que ce qui ira au contact.** Les tireurs
#: appuient l'assaut par le feu — `Planner._assault_target` concentre deja leurs
#: salves sur le secteur — mais les compter comme de la force de melee ferait
#: annoncer une superiorite qui ne sera jamais livree.
#:
#: La cavalerie **en fait partie**, parce que `_command_cavalry` honore desormais
#: l'assaut : une charge de flanc concentree est le meilleur usage possible d'une
#: cavalerie de choc, et c'est precisement la manoeuvre visee. Elle n'y figurait
#: pas tant qu'elle ne recevait pas l'ordre — compter une force qui ne vient pas
#: est exactement le defaut que l'archer a revele.
ASSAULT_ROLES = frozenset(role for role in UnitRole if role not in RANGED_ROLES)

#: Part de la force initiale sous laquelle un secteur est considere rompu.
BREAK_SHARE = 0.35

#: Distance a laquelle une unite est consideree arrivee a sa position de depart.
#:
#: **Volontairement `SUPPORT_RADIUS`, et non un nombre de plus.** C'est le rayon
#: dans lequel une unite pese deja sur le combat de sa voisine : y etre, c'est
#: etre en situation de participer. Inventer une tolerance separee obligerait a
#: la regler, alors que la question « suis-je assez pres pour compter ? » a deja
#: une reponse dans ce module.
STAGING_TOLERANCE = SUPPORT_RADIUS


class ManoeuvrePhase(StrEnum):
    """Ou en est la manoeuvre.

    Quatre etats, pas davantage : on ne multiplie pas les phases tant qu'un
    besoin reel ne l'exige pas.
    """

    #: Les participants rejoignent leur position de depart. **Personne n'attaque.**
    ASSEMBLE = "assemble"
    #: Tout le monde est libere en meme temps.
    CONTACT = "contact"
    #: Le secteur a cede ; la planification ordinaire reprend la main.
    EXPLOIT = "exploit"
    ABORTED = "aborted"


class ManoeuvreRole(StrEnum):
    """Ce qu'une unite fait **dans** la manoeuvre.

    **Participer n'est pas etre en melee.** Un tireur qui appuie a cent vingt
    metres tient son role sans jamais toucher l'ennemi, et une lecture qui ne
    compterait que les contacts le declarerait absent — c'est l'amendement C de
    LIVE-002, inscrit ici dans le modele plutot que rappele en commentaire.
    """

    ASSAULT = "assault"
    FIX = "fix"
    FIRE_SUPPORT = "fire_support"
    FLANK = "flank"
    RESERVE = "reserve"


@dataclass(frozen=True, slots=True)
class Assignment:
    """Une unite, son role, et l'endroit ou elle doit etre avant le contact."""

    unit_id: str
    role: ManoeuvreRole
    #: Position de depart. C'est la que `ASSEMBLE` l'envoie, et rien d'autre.
    staging: Vector3
    #: Sa presence conditionne-t-elle le passage a `CONTACT` ?
    required: bool = True

    def ready(
        self,
        state: BattleState,
        *,
        centre: Vector3,
        targets: Sequence[str] = (),
        mobility: MobilityTracker | None = None,
    ) -> bool:
        """Cette unite est-elle en situation de tenir son role ?

        **La condition depend du role, et c'est tout l'interet.** Demander a
        chacun d'etre au contact ferait attendre l'assaut apres des tireurs qui
        n'ont rien a y faire, et declarerait absent un flanqueur parfaitement en
        place. Le jour ou cette question portera sur toute la manoeuvre, c'est
        cette methode-ci qui repondra, pas un comptage de melees.
        """
        unite = state.unit(self.unit_id)
        if unite is None or not unite.is_available:
            return False
        en_place = unite.position.distance_2d(self.staging) <= STAGING_TOLERANCE

        if self.role is ManoeuvreRole.FIRE_SUPPORT:
            # Position **et** portee : un tireur en place mais hors d'atteinte
            # n'appuie rien. `missile_range` vaut 0 pour qui n'a pas de tir.
            portee = float(unite.metadata.get("missile_range", 0.0) or 0.0)
            vises = set(targets)
            return en_place and any(
                autre.id in vises and unite.position.distance_2d(autre.position) <= portee
                for autre in state.enemies()
            )
        if self.role is ManoeuvreRole.FLANK:
            # **Pas au contact** : un flanqueur deja engage n'est pas pret, il est
            # en retard — c'est exactement le defaut du 18/08.
            return en_place and not unite.is_engaged
        if self.role is ManoeuvreRole.ASSAULT:
            if en_place or unite.is_engaged:
                return True
            suivi = mobility if mobility is not None else MobilityTracker()
            return suivi.eta(unite, centre) <= ASSAULT_WINDOW
        return en_place

    def to_dict(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "role": self.role.value,
            "staging": self.staging.to_dict(),
            "required": self.required,
        }


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
class Manoeuvre:
    """Ce qu'on envoie, ou, avec quels roles, et dans quel ordre.

    .. rubric:: Pourquoi la classe s'appelle ainsi et la cle non

    Elle s'est longtemps appelee `Assault`, quand elle ne portait qu'une liste
    d'attaquants. Depuis qu'elle porte une phase et des roles, ce n'est plus un
    assaut mais une manoeuvre, et le code doit le dire.

    La cle serialisee reste pourtant `"assault"` : quatre instruments la lisent
    dans les corpus deja enregistres — `learning.assault`, `learning.sector_value`,
    `simulation.runner` et leurs tests. La renommer rendrait illisibles les
    batailles qui servent justement de point de comparaison.

    .. rubric:: Persistante par construction

    `Planner._manoeuvre` la garde d'un plan a l'autre tant qu'elle n'a pas rompu.
    Rechoisir a chaque tour, c'est la cavalerie qui recevait dix cibles en cent
    trente secondes sans achever un seul contournement (ADR 0013) — et une phase
    reconstruite chaque tour ne franchirait jamais `ASSEMBLE`.
    """

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
    #: Ou en est la manoeuvre. **Une seule transition par plan.**
    phase: ManoeuvrePhase = ManoeuvrePhase.CONTACT
    #: Qui fait quoi, et depuis ou. Vide tant que personne n'a de role.
    assignments: tuple[Assignment, ...] = ()
    abort_reason: str | None = None

    def assignment(self, unit_id: str) -> Assignment | None:
        return next((item for item in self.assignments if item.unit_id == unit_id), None)

    def role_of(self, unit_id: str) -> ManoeuvreRole | None:
        affectation = self.assignment(unit_id)
        return affectation.role if affectation is not None else None

    @property
    def holding(self) -> bool:
        """Les participants doivent-ils encore se retenir d'ouvrir le combat ?"""
        return self.phase is ManoeuvrePhase.ASSEMBLE

    def ready_assignments(
        self, state: BattleState, *, mobility: MobilityTracker | None = None
    ) -> tuple[Assignment, ...]:
        return tuple(
            item
            for item in self.assignments
            if item.ready(state, centre=self.centre, targets=self.targets, mobility=mobility)
        )

    def missing(
        self, state: BattleState, *, mobility: MobilityTracker | None = None
    ) -> tuple[str, ...]:
        """Participants **requis** qui ne sont pas encore en situation.

        C'est la reponse a « qui attendait qui ? », et elle doit etre lisible
        apres la bataille sans rejouer quoi que ce soit.
        """
        prets = {item.unit_id for item in self.ready_assignments(state, mobility=mobility)}
        return tuple(
            item.unit_id for item in self.assignments if item.required and item.unit_id not in prets
        )

    def can_engage(self, state: BattleState, *, mobility: MobilityTracker | None = None) -> bool:
        """Les participants requis sont-ils en situation de tenir leurs roles ?

        **La condition porte sur les requis de cette manoeuvre, pas sur l'armee.**
        Un seuil du type « huit unites sur douze » mesurerait la masse et non la
        coordination : `numerical_superiority` gagne ses trois batailles avec une
        seule unite au contact.
        """
        if not any(item.required for item in self.assignments):
            return True
        return not self.missing(state, mobility=mobility)

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
            f"sur {len(self.targets)} ennemi(s), rapport {self.ratio:.2f} "
            f"[{self.phase.value}]"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "sector": self.sector,
            "attackers": list(self.attackers),
            "targets": list(self.targets),
            "ratio": round(self.ratio, 3),
            "initial_enemy_strength": round(self.initial_enemy_strength, 3),
            "started_at": self.started_at,
            "phase": self.phase.value,
            "assignments": [item.to_dict() for item in self.assignments],
            "abort_reason": self.abort_reason,
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
    mobility: MobilityTracker | None = None,
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

    # Sans suivi de mobilite — au premier plan d'une bataille, ou dans un test
    # qui n'en fournit pas — chacun porte la vitesse par defaut, et le tri par
    # temps redevient un tri par distance. C'est exactement le comportement
    # precedent : la degradation est douce, jamais un refus de composer.
    mobility = mobility if mobility is not None else MobilityTracker()
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

    # **Seules les unites qui recevront l'ordre d'attaquer comptent.**
    # `_command_front_line` n'itere que le groupe de front : un tireur ou une
    # cavalerie figurant parmi les assaillants ne recoit jamais d'ordre d'assaut,
    # et gonflait pourtant le rapport annonce.
    #
    # Mesure sur `outnumbered` : l'assaut annoncait 1,50 — deux lanciers et un
    # archer, soit 3,00 contre un cout de 2,00 — alors que la melee reelle
    # opposait les deux lanciers seuls, 2,00 contre 2,00. **La parite, presentee
    # comme une superiorite de moitie.** L'agent a echange toute sa ligne de
    # melee contre deux ennemis, puis n'a plus rien fait pendant quatre minutes.
    combattants = [unit for unit in allies if unit.role in ASSAULT_ROLES]
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
        atteignables = [
            unit for unit in combattants if mobility.eta(unit, centre) <= ASSAULT_DEADLINE
        ]
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
    sector: Sector,
    state: BattleState,
    allies: Sequence[UnitState],
    game_time: float,
    *,
    mobility: MobilityTracker | None = None,
    slide: bool = False,
) -> Manoeuvre | None:
    """Compose l'assaut : le necessaire, qui arrive ensemble, et pas davantage.

    **Envoyer toute l'armee n'est pas concentrer, c'est s'engager.** On ajoute
    les unites jusqu'a depasser `ASSAULT_RATIO`, puis on s'arrete : ce qui reste
    tient le front ailleurs, et c'est cette fixation qui empeche l'adversaire de
    venir retablir la partie qu'on attaque.

    .. rubric:: Par temps d'arrivee, et non par distance

    Le tri se faisait par distance, et l'agent n'avait aucune notion de vitesse.
    Mesure sur `outnumbered`, douze graines : le rapport annonce 1,50 au choix
    valait **0,96 au contact** — l'assaut arrivait sous la parite, apres 31 s de
    trajet, ayant perdu 36 % de son avantage en chemin.

    Deux bornes le corrigent, et la seconde est la plus importante :

    * `ASSAULT_DEADLINE` ecarte ce qui ne peut pas arriver a temps ;
    * `ASSAULT_WINDOW` ecarte ce qui arriverait **trop tard par rapport aux
      autres**. Trois unites separees de vingt secondes livrent trois combats a
      un contre un, pas un combat a trois contre un : c'est la defaite en detail
      que l'on s'infligerait soi-meme.

    Rend `None` si le necessaire n'existe pas — l'agent garde alors son
    comportement habituel, et c'est cette abstention qui protege les scenarios
    deja gagnes.
    """
    suivi = mobility if mobility is not None else MobilityTracker()
    disponibles = [unit for unit in allies if unit.id in set(sector.reachable)]
    # L'identifiant tranche les egalites : deux unites de meme ETA doivent
    # toujours etre prises dans le meme ordre, sinon le banc cesse d'etre
    # reproductible (ADR 0011).
    disponibles.sort(key=lambda unit: (suivi.eta(unit, sector.centre), unit.id))

    besoin = sector.cost * ASSAULT_RATIO
    # **La fenetre glissante est un canal de mesure, eteint en production.**
    #
    # Ancree sur la premiere arrivee, la fenetre laisse l'unite la plus rapide
    # opposer son veto au groupe : sur `skirmish_standoff`, une cavalerie
    # devancant l'infanterie de dix-huit secondes faisait refuser les trois
    # unites qui, elles, arrivaient a cinq secondes les unes des autres, et
    # `commit()` rendait `None` a chacun des cinquante-six plans.
    #
    # La faire glisser leve ce veto sans rien relacher — tout assaut compose
    # arrive toujours dans une seule fenetre. Mesure a l'ADR 0019 : 56 assauts
    # la ou il n'y en avait aucun, et `skirmish_standoff` passant du nul a la
    # **defaite**, forces restantes 100 % -> 74 % pour 2 % des leurs.
    #
    # Elle reste donc eteinte : le defaut qu'elle a revele est ailleurs, dans le
    # choix du secteur. Mais sans elle, aucune donnee n'existe sur ce que les
    # secteurs valent, puisqu'aucun assaut ne s'y compose. Elle sert la sonde de
    # mesure, et rien d'autre.
    departs = range(len(disponibles)) if slide else range(1)
    engagees: list[str] = []
    force = 0.0
    for depart in departs:
        origine = suivi.eta(disponibles[depart], sector.centre) if disponibles else 0.0
        groupe: list[str] = []
        cumul = 0.0
        for unit in disponibles[depart:]:
            if cumul >= besoin:
                break
            if suivi.eta(unit, sector.centre) - origine > ASSAULT_WINDOW:
                # Les suivantes sont plus lentes encore : le tri le garantit.
                break
            groupe.append(unit.id)
            cumul += unit.effective_strength
        if groupe and (sector.cost <= 1e-9 or cumul >= besoin):
            engagees, force = groupe, cumul
            break
        if not slide:
            engagees, force = groupe, cumul
    if not engagees or (sector.cost > 1e-9 and force < besoin):
        return None

    par_id = {unit.id: unit for unit in allies}
    return Manoeuvre(
        sector=sector.index,
        centre=sector.centre,
        attackers=tuple(engagees),
        targets=sector.enemy_ids,
        ratio=force / sector.cost if sector.cost > 1e-9 else float("inf"),
        initial_enemy_strength=sector.enemy_strength,
        started_at=game_time,
        assignments=tuple(
            _assign(par_id[unit_id], sector.centre) for unit_id in engagees if unit_id in par_id
        ),
    )


def _assign(unit: UnitState, centre: Vector3) -> Assignment:
    """Role et position de depart d'un assaillant.

    **Les unites rapides flanquent, les autres portent le choc.** C'est deja ce
    que le planificateur fait — `_command_cavalry` emet un `FLANK` la ou
    `_command_front_line` emet un `ATTACK_TARGET` — mais nulle part ce n'etait
    ecrit : la manoeuvre le nomme, pour pouvoir ensuite le retenir jusqu'au
    contact.

    La position de depart se prend **du cote d'ou l'unite arrive**, a
    `STAGING_TOLERANCE` du centre du secteur. Ni un point commun, qui ferait un
    tas, ni la position actuelle, qui ne demanderait aucun rassemblement.
    """
    role = ManoeuvreRole.FLANK if unit.role in MOBILE_ROLES else ManoeuvreRole.ASSAULT
    approche = centre.direction_to(unit.position)
    if approche.length_2d() <= 1e-9:
        return Assignment(unit_id=unit.id, role=role, staging=centre)
    ecart = approche.scaled(STAGING_TOLERANCE)
    if role is ManoeuvreRole.FLANK:
        # Le flanqueur se prepare **a cote** de l'axe d'assaut, pas derriere :
        # arriver par la meme direction que la ligne n'est pas un flanquement.
        ecart = Vector3(approche.z, 0.0, -approche.x).scaled(STAGING_TOLERANCE)
    return Assignment(unit_id=unit.id, role=role, staging=centre + ecart)
