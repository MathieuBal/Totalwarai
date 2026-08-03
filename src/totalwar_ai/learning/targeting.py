"""Apprendre qui l'IA du moteur attaque, en la regardant choisir.

**Ce que ce module cherche.** Notre ciblage repose sur `TARGET_PRIORITY`, une
table ecrite a la main : l'artillerie vaut 1.00, les tireurs 0.85, et ainsi de
suite. Personne n'a jamais verifie que l'IA du jeu pense cela. Ce module tire la
table des faits plutot que des convictions.

.. rubric:: Pourquoi il faut denombrer ce qui etait offert

Compter les cibles frappees ne donne rien. Une cavalerie qui frappe des lanciers
neuf fois sur dix n'aime pas les lanciers : elle affronte une armee qui n'en
compte presque que. **Sans denominateur, on apprend la composition des armees
rencontrees, jamais une preference.**

D'ou l'affinite retenue : le rapport entre les choix observes et les choix
qu'aurait faits un tirage au sort **parmi ce qui etait disponible a cet
instant**. Un role d'affinite 2.0 est pris deux fois plus souvent que le hasard
ne l'aurait pris ; 0.3, trois fois moins. La valeur 1.0 signifie « indifferent »,
et c'est le seul repere qui ait un sens.

.. rubric:: Pourquoi il faut mesurer sur ce qu'on n'a pas appris

Une table d'affinites decrit toujours parfaitement les batailles dont elle est
tiree. La seule question qui vaille est : **predit-elle celles qu'elle n'a pas
vues ?** :func:`evaluate` apprend sur une part du corpus et mesure sur le reste,
contre deux etalons qu'il faut battre pour avoir appris quoi que ce soit :

- le hasard parmi ce qui etait disponible — un modele qui ne le bat pas n'a
  rien appris du tout ;
- notre `TARGET_PRIORITY` ecrite a la main — un modele qui ne le bat pas
  n'apprend rien **que nous ne sachions deja**.

Le second etalon est le plus utile : c'est lui qui dira s'il faut remplacer la
table, ou la garder.

.. rubric:: Ce que cela ne donne pas

La prediction porte sur le **role** de la cible, jamais sur l'unite. Deux
lanciers cote a cote sont indiscernables pour nos donnees, et une precision
annoncee a l'unite serait une precision inventee. La geometrie — laquelle des
deux, celle de gauche ou celle du flanc — releve d'un autre module.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from totalwar_ai.domain.unit_state import UnitRole
from totalwar_ai.learning.observation import Move, Observation

#: Mouvements qui traduisent un **choix** de cible.
#:
#: `engage` n'y figure pas, et c'est la conclusion la plus importante de
#: l'etalonnage. Une unite au contact ne choisit pas son adversaire : elle subit
#: celui qui l'a rattrapee. Apprendre de la melee, c'est apprendre **qui a
#: intercepte qui** en croyant apprendre qui etait recherche — la doublure, dont
#: la cavalerie va aux tireurs, ressortait avec un gout prononce pour les
#: lanciers, qui sont simplement ce qui l'arretait en chemin.
CHOICE_MOVES = frozenset({Move.CLOSE, Move.FIRE})

#: Choix observes en deca desquels une affinite ne se lit pas.
#:
#: Trois observations peuvent tomber sur la meme cible par accident ; un rapport
#: tire de si peu de cas dirait surtout le bruit du corpus.
MINIMUM_SAMPLES = 12

#: Affinite rendue quand un couple n'a jamais ete observe.
#:
#: Volontairement neutre : ne l'avoir jamais vu n'est pas l'avoir vu evite. Le
#: nombre d'observations, publie a cote, dit ce que vaut la valeur.
NEUTRAL_AFFINITY = 1.0


@dataclass(frozen=True, slots=True)
class Affinity:
    """Gout d'un role d'attaquant pour un role de cible, et son assise."""

    attacker: UnitRole
    target: UnitRole
    #: Fois ou ce role de cible a ete choisi.
    chosen: int
    #: Fois ou le hasard l'aurait choisi, vu ce qui etait disponible.
    expected: float

    @property
    def affinity(self) -> float:
        """Rapport choix observes / choix attendus du hasard.

        > 1 : recherche. < 1 : evite. 1.0 : indifferent.
        """
        if self.expected <= 0.0:
            return NEUTRAL_AFFINITY
        return self.chosen / self.expected

    @property
    def solid(self) -> bool:
        """L'assise suffit-elle pour que le rapport se lise ?"""
        return self.chosen + self.expected >= MINIMUM_SAMPLES

    def explain(self) -> str:
        doute = "" if self.solid else "  (trop peu de cas)"
        return (
            f"{self.attacker.value:<16} -> {self.target.value:<16} "
            f"{self.affinity:5.2f}  ({self.chosen} choix / {self.expected:.1f} attendus)"
            f"{doute}"
        )


@dataclass
class TargetingModel:
    """Ce que l'IA observee recherche, role par role."""

    #: (role attaquant, role cible) -> affinite.
    affinities: dict[tuple[UnitRole, UnitRole], Affinity] = field(default_factory=dict)

    #: Decisions ciblees retenues pour l'apprentissage.
    samples: int = 0

    #: Decisions ecartees faute de cible sure ou de choix reel.
    skipped: int = 0

    def affinity(self, attacker: UnitRole, target: UnitRole) -> float:
        entry = self.affinities.get((attacker, target))
        return entry.affinity if entry is not None else NEUTRAL_AFFINITY

    def preference(self, attacker: UnitRole) -> list[Affinity]:
        """Ce qu'un role recherche, du plus au moins, assises comprises."""
        entries = [item for item in self.affinities.values() if item.attacker is attacker]
        return sorted(entries, key=lambda item: item.affinity, reverse=True)

    def predict(self, attacker: UnitRole, available: Sequence[UnitRole]) -> UnitRole | None:
        """Role que cet attaquant choisirait parmi ce qui lui est offert.

        A egalite d'affinite — cas frequent quand rien n'a ete appris sur le
        couple — le premier role disponible l'emporte. Ce n'est pas une opinion
        du modele : c'est ce qu'il faut bien rendre pour que la mesure ait lieu.
        """
        if not available:
            return None
        return max(available, key=lambda role: self.affinity(attacker, role))

    def render(self) -> str:
        if not self.affinities:
            return "Aucune preference apprise : le corpus ne porte aucune cible sure."
        lignes = [
            f"{self.samples} decision(s) ciblee(s) retenue(s), {self.skipped} ecartee(s)",
            "",
        ]
        for attaquant in sorted({item.attacker for item in self.affinities.values()}):
            lignes.append(f"  {attaquant.value}")
            for entry in self.preference(attaquant):
                lignes.append(f"    {entry.explain()}")
        return "\n".join(lignes)


def learn(observations: Iterable[Observation]) -> TargetingModel:
    """Tire une table d'affinites des decisions observees.

    Quatre sortes de decisions ne sont pas des choix et sont ecartees : celles
    sans cible, celles dont la cible etait ambigue — retenir une supposition la
    ferait passer pour un fait —, celles ou un seul role etait disponible, ou
    choisir ne voulait rien dire, et **la melee**, ou l'on ne choisit plus rien
    (voir :data:`CHOICE_MOVES`).
    """
    total = 0
    choix: list[Choice] = []
    for item in observations:
        total += 1
        retenu = as_choice(item)
        if retenu is not None:
            choix.append(retenu)
    return _build(choix, skipped=total - len(choix))


def _build(choix: Sequence[Choice], *, skipped: int) -> TargetingModel:
    """Table d'affinites a partir de choix deja tries."""
    choisis: dict[tuple[UnitRole, UnitRole], int] = defaultdict(int)
    attendus: dict[tuple[UnitRole, UnitRole], float] = defaultdict(float)
    for retenu in choix:
        offerts = retenu.available
        choisis[(retenu.attacker, retenu.target)] += 1
        # Ce qu'un tirage au sort parmi les unites disponibles aurait donne.
        for role in set(offerts):
            attendus[(retenu.attacker, role)] += offerts.count(role) / len(offerts)

    couples = set(choisis) | set(attendus)
    return TargetingModel(
        affinities={
            couple: Affinity(
                attacker=couple[0],
                target=couple[1],
                chosen=choisis.get(couple, 0),
                expected=attendus.get(couple, 0.0),
            )
            for couple in couples
        },
        samples=len(choix),
        skipped=skipped,
    )


@dataclass(frozen=True, slots=True)
class Choice:
    """Une decision dont on peut apprendre : un choix, et ce qui l'entourait."""

    attacker: UnitRole
    target: UnitRole
    available: tuple[UnitRole, ...]


def as_choice(item: Observation) -> Choice | None:
    """La decision observee, si c'en est une dont on puisse apprendre.

    Rend `None` pour les quatre cas ecartes, decrits par :func:`learn`.
    """
    if item.move not in CHOICE_MOVES or item.ambiguous or item.target_role is None:
        return None
    # Un seul role en face : le choisir n'apprend rien.
    if len(set(item.available)) < 2:
        return None
    return Choice(attacker=item.role, target=item.target_role, available=item.available)


@dataclass(frozen=True, slots=True)
class Accuracy:
    """Ce que le modele predit, et ce que valent les etalons face a lui."""

    #: Decisions du lot de controle sur lesquelles la mesure a porte.
    samples: int
    #: Part des cibles dont le modele appris a retrouve le role.
    learned: float
    #: Part qu'un tirage au sort parmi le disponible aurait atteinte.
    random: float
    #: Part que notre table `TARGET_PRIORITY` ecrite a la main atteint.
    handwritten: float

    @property
    def beats_random(self) -> bool:
        return self.learned > self.random

    @property
    def beats_handwritten(self) -> bool:
        return self.learned > self.handwritten

    def explain(self) -> str:
        verdict = (
            "le modele appris depasse la table ecrite a la main"
            if self.beats_handwritten
            else "la table ecrite a la main tient : rien a remplacer"
            if self.beats_random
            else "rien n'a ete appris : le hasard fait aussi bien"
        )
        return (
            f"{self.samples} decision(s) de controle\n"
            f"  appris            {self.learned:6.1%}\n"
            f"  hasard            {self.random:6.1%}\n"
            f"  TARGET_PRIORITY   {self.handwritten:6.1%}\n"
            f"  -> {verdict}"
        )


def evaluate(observations: Sequence[Observation], *, holdout: float = 0.3) -> Accuracy:
    """Apprend sur une part des decisions et mesure sur le reste.

    La coupe est **chronologique** : on apprend du debut d'un corpus et l'on
    predit sa fin. Melanger les decisions ferait fuiter la meme bataille des
    deux cotes de la coupe, et la precision annoncee serait celle d'un modele
    qui a deja vu la reponse.
    """
    utiles = [retenu for retenu in map(as_choice, observations) if retenu is not None]
    if len(utiles) < 2:
        return Accuracy(samples=0, learned=0.0, random=0.0, handwritten=0.0)

    coupe = max(1, int(len(utiles) * (1.0 - holdout)))
    modele = _build(utiles[:coupe], skipped=0)
    controle = utiles[coupe:]
    if not controle:
        return Accuracy(samples=0, learned=0.0, random=0.0, handwritten=0.0)

    from totalwar_ai.agent.planner import TARGET_PRIORITY

    bons = alea = table = 0.0
    for retenu in controle:
        offerts = retenu.available
        if modele.predict(retenu.attacker, offerts) is retenu.target:
            bons += 1
        # Le hasard se calcule au lieu de se tirer : la probabilite de tomber
        # sur le bon role est exactement sa part parmi les unites disponibles.
        alea += offerts.count(retenu.target) / len(offerts)
        choix = max(offerts, key=lambda role: TARGET_PRIORITY.get(role, 0.4))
        if choix is retenu.target:
            table += 1

    total = len(controle)
    return Accuracy(
        samples=total,
        learned=bons / total,
        random=alea / total,
        handwritten=table / total,
    )
