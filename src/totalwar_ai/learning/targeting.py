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

import json
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

#: Marqueur en tete d'un modele enregistre.
#:
#: Un fichier qui se nomme lui-meme reste lisible dans six mois, et un modele
#: d'un autre format ne se charge pas a moitie.
MODEL_FORMAT = "totalwar_ai.targeting.v1"

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

    # --- ce que l'agent peut en faire ----------------------------------------

    def usable(self, attacker: UnitRole) -> bool:
        """Ce role a-t-il assez de cas pour qu'on lui obeisse en bataille ?

        **Un modele a moitie appris est pire qu'une table ecrite a la main** :
        il a l'autorite d'une mesure et l'assise d'une anecdote. Tant qu'un role
        n'a pas deux couples solidement observes, l'agent garde `TARGET_PRIORITY`
        pour ce role — et seulement pour ce role.
        """
        return sum(1 for item in self.preference(attacker) if item.solid) >= 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": MODEL_FORMAT,
            "samples": self.samples,
            "skipped": self.skipped,
            "affinities": [
                {
                    "attacker": item.attacker.value,
                    "target": item.target.value,
                    "chosen": item.chosen,
                    "expected": round(item.expected, 4),
                }
                for item in sorted(
                    self.affinities.values(), key=lambda entry: (entry.attacker, entry.target)
                )
            ],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> TargetingModel:
        """Relit un modele enregistre, en ignorant ce qu'il ne comprend pas.

        Un role disparu du domaine — renomme, retire — ne doit pas empecher de
        charger les autres : le modele est une aide, jamais une dependance.
        """
        if not isinstance(raw, dict) or raw.get("format") != MODEL_FORMAT:
            return cls()
        affinites: dict[tuple[UnitRole, UnitRole], Affinity] = {}
        for entry in raw.get("affinities") or []:
            if not isinstance(entry, dict):
                continue
            try:
                attaquant = UnitRole(entry["attacker"])
                cible = UnitRole(entry["target"])
            except (KeyError, ValueError):
                continue
            affinites[(attaquant, cible)] = Affinity(
                attacker=attaquant,
                target=cible,
                chosen=int(entry.get("chosen", 0)),
                expected=float(entry.get("expected", 0.0)),
            )
        return cls(
            affinities=affinites,
            samples=int(raw.get("samples", 0)),
            skipped=int(raw.get("skipped", 0)),
        )

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> TargetingModel:
        """Modele enregistre, ou un modele vide si le fichier manque.

        Ne leve jamais : l'agent doit pouvoir jouer sans avoir rien appris.
        """
        try:
            brut = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls.from_dict(brut)

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


def episodes(observations: Iterable[Observation]) -> list[Observation]:
    """Une decision par **episode**, et non une par releve.

    **Le corpus est echantillonne a 2 Hz, et rien ne repliait ces releves.** Une
    unite qui charge le meme ennemi pendant une minute produisait ainsi cent
    vingt « decisions » identiques. Deux consequences, toutes deux fausses :

    * `samples` annoncait des milliers de decisions la ou il y en avait quelques
      dizaines, ce qui donne a la mesure une assise qu'elle n'a pas ;
    * la precision se trouvait dominee par les engagements **longs** plutot que
      par les choix **frequents** — un seul corps a corps interminable pesait
      plus que vingt decisions de ciblage distinctes.

    Une decision commence quand une unite change de cible et dure tant qu'elle
    la garde. On ne retient donc que le premier releve de chaque suite
    `(unite, cible)` consecutive.

    L'ordre d'arrivee fait foi : les observations d'une bataille sont produites
    dans l'ordre du temps de jeu par :mod:`totalwar_ai.learning.observation`.
    """
    retenues: list[Observation] = []
    en_cours: dict[str, str | None] = {}
    for item in observations:
        precedente = en_cours.get(item.unit_id, "")
        if precedente != item.target_id:
            en_cours[item.unit_id] = item.target_id
            retenues.append(item)
    return retenues


def learn(observations: Iterable[Observation]) -> TargetingModel:
    """Tire une table d'affinites des decisions observees.

    Quatre sortes de decisions ne sont pas des choix et sont ecartees : celles
    sans cible, celles dont la cible etait ambigue — retenir une supposition la
    ferait passer pour un fait —, celles ou un seul role etait disponible, ou
    choisir ne voulait rien dire, et **la melee**, ou l'on ne choisit plus rien
    (voir :data:`CHOICE_MOVES`).

    Les releves sont d'abord replies en episodes (voir :func:`episodes`) : sans
    cela, la table apprend la duree des engagements et non les preferences.
    """
    total = 0
    choix: list[Choice] = []
    for item in episodes(observations):
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
    #: Nombre de batailles servies en controle, une par passe.
    folds: int = 0
    #: Precision la plus basse et la plus haute parmi ces passes.
    #:
    #: **Un chiffre unique sur trois batailles ne mesure rien.** L'ecart entre
    #: les passes dit s'il reste quelque chose apres avoir change de bataille,
    #: ou si l'on a mesure une bataille particuliere.
    worst: float = 0.0
    best: float = 0.0

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
        lignes = [
            f"{self.samples} decision(s) de controle"
            + (f", {self.folds} bataille(s) en controle a tour de role" if self.folds else ""),
            f"  appris            {self.learned:6.1%}",
            f"  hasard            {self.random:6.1%}",
            f"  TARGET_PRIORITY   {self.handwritten:6.1%}",
        ]
        if self.folds > 1:
            lignes.append(f"  ecart entre passes {self.worst:5.1%} a {self.best:.1%}")
        lignes.append(f"  -> {verdict}")
        return "\n".join(lignes)


def evaluate(battles: Sequence[Sequence[Observation]]) -> Accuracy:
    """Apprend sur toutes les batailles sauf une, et predit celle-la.

    **La coupe doit separer des batailles, pas des decisions.** La version
    precedente decoupait une liste plate a 70 % et sa docstring affirmait que
    cela empechait « la meme bataille de fuiter des deux cotes ». C'etait faux :
    la coupe tombe au milieu d'une bataille, et les decisions d'une meme bataille
    partagent les memes unites, les memes positions, la meme composition adverse.
    Le modele retrouvait donc des reponses qu'il avait deja vues, et les 82 %
    publies par l'ADR 0008 n'etaient pas une mesure de generalisation.

    On prend donc chaque bataille a son tour comme lot de controle — une seule
    coupe ne dirait rien sur un corpus de trois. La precision rendue est la
    moyenne des passes ; `worst` et `best` disent ce qu'elle cache.

    Accepte une sequence de batailles, chacune etant la suite de ses
    observations. L'appelant les tient deja separees : c'est en les concatenant
    qu'il perdait l'information.
    """
    from totalwar_ai.agent.planner import TARGET_PRIORITY

    par_bataille = [
        [retenu for retenu in map(as_choice, episodes(bataille)) if retenu is not None]
        for bataille in battles
    ]
    par_bataille = [lot for lot in par_bataille if lot]
    if len(par_bataille) < 2:
        # Une seule bataille exploitable : il n'y a rien contre quoi valider, et
        # inventer une coupe interne rendrait exactement le chiffre flatteur que
        # cette fonction existe pour eviter.
        return Accuracy(samples=0, learned=0.0, random=0.0, handwritten=0.0)

    total = 0
    scores: list[float] = []
    alea: list[float] = []
    table: list[float] = []
    for index, controle in enumerate(par_bataille):
        entrainement = [
            choix for autre, lot in enumerate(par_bataille) if autre != index for choix in lot
        ]
        if not entrainement:
            continue
        modele = _build(entrainement, skipped=0)
        justes = hasard = ecrite = 0.0
        for retenu in controle:
            offerts = retenu.available
            if modele.predict(retenu.attacker, offerts) is retenu.target:
                justes += 1
            # Le hasard se calcule au lieu de se tirer : la probabilite de tomber
            # sur le bon role est exactement sa part parmi les unites disponibles.
            hasard += offerts.count(retenu.target) / len(offerts)
            choix_table = max(offerts, key=lambda role: TARGET_PRIORITY.get(role, 0.4))
            if choix_table is retenu.target:
                ecrite += 1
        total += len(controle)
        # **Une bataille, une voix.** La version precedente rendait `bons / total`
        # — une moyenne ponderee par la taille des batailles — alors que sa
        # docstring promettait « la moyenne des passes ». Une bataille longue
        # ecrasait donc les autres, ce qui vide de son sens une coupe faite
        # precisement pour separer les batailles.
        scores.append(justes / len(controle))
        alea.append(hasard / len(controle))
        table.append(ecrite / len(controle))

    if not total or not scores:
        return Accuracy(samples=0, learned=0.0, random=0.0, handwritten=0.0)

    return Accuracy(
        samples=total,
        learned=statistics.mean(scores),
        random=statistics.mean(alea),
        handwritten=statistics.mean(table),
        folds=len(scores),
        worst=min(scores),
        best=max(scores),
    )
