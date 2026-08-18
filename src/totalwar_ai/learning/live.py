"""Ce que l'agent a reellement fait en bataille, lu dans le journal du jeu.

**Pourquoi ceci existe.** Le premier compte rendu tire du journal du 18/08
annoncait « 204 manoeuvres, 4 ordres refuses ». Le journal en contient **102 et
2**. La cause est mecanique : chaque lot de commandes y figure **deux fois** —
un `ACK` structure, puis une ligne `manoeuvre` lisible — et compter les lignes
qui contiennent le mot revenait a compter deux representations du meme
evenement.

C'est le troisieme instrument de ce projet a doubler ou a inventer une realite,
apres le tableau du banc qui confondait nul et defaite et le verdict de tir tire
d'une seule unite. La regle qui en sort : **une seule source canonique**, et une
deduplication par `sequence` plutot que par ressemblance de texte.

.. rubric:: L'ecart que ce module existe pour mesurer

Le meme journal montre, en temps de bataille :

.. code-block:: text

    dernier ordre        312,1 s
    ordre suivant        676,1 s      -> 364 s sans la moindre commande
    allies 12 -> 10      381,1 s
    allies 10 ->  9      591,1 s
    premiere attaque     852,1 s

L'agent regarde son armee fondre pendant six minutes de temps de bataille sans
emettre une commande. Le simulateur ne reproduit rien de tel : c'est le plus gros
ecart connu entre le banc et le jeu, et il ne se voyait dans aucune metrique.

.. rubric:: Ce que ce module ne peut pas dire

Il lit le journal du **jeu**, qui porte les ordres envoyes et non les raisons de
n'en envoyer aucun. Une fenetre de silence y apparait donc avec son contexte
observable — effectifs, instants — mais **sans motif**. Le motif appartient a la
telemetrie de l'agent, et `reason` reste `None` tant qu'elle ne le fournit pas :
un instrument qui inventerait la cause d'un silence serait pire qu'un silence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

#: Prefixe appose par `PROBE:log`.
PREFIX = "[totalwar_ai]"

#: Duree minimale d'une fenetre sans ordre pour qu'elle soit rapportee, en secondes.
#:
#: Vingt secondes : au-dela, ce n'est plus le rythme normal d'un agent qui
#: replanifie toutes les dix secondes, c'est une abstention.
SILENCE_SECONDS = 20.0

#: Temps de bataille, en millisecondes, tel que le jeu le prefixe a chaque ligne.
_CLOCK = re.compile(r"<(?P<ms>\d+)ms>")
_ACK = re.compile(r"ACK\s+(?P<payload>\{.*\})\s*$")
_MANOEUVRE = re.compile(
    r"manoeuvre : (?P<moves>\d+) deplacement\(s\), (?P<attacks>\d+) attaque\(s\), "
    r"(?P<halts>\d+) arret\(s\)"
)
_COUNTS = re.compile(r"(?P<launched>\d+) ordre\(s\) lance\(s\), (?P<refused>\d+) refuse\(s\)")
_BATTLE = re.compile(
    r"BATTLE phase (?P<phase>\S+) : (?P<allies>\d+) allies, (?P<enemies>\d+) ennemis"
)


@dataclass(frozen=True, slots=True)
class Batch:
    """Un lot de commandes, compte **une seule fois**.

    La source canonique est l'`ACK` : lui seul porte un `sequence`, donc lui seul
    permet de dedupliquer autrement que par ressemblance de texte. La ligne
    `manoeuvre` qui le suit ne fait qu'enrichir la ventilation.
    """

    sequence: int
    at: float
    launched: int = 0
    refused: int = 0
    moves: int = 0
    attacks: int = 0
    halts: int = 0
    error: str | None = None

    @property
    def requested(self) -> int:
        """Actions demandees : lancees plus refusees."""
        return self.launched + self.refused


@dataclass(frozen=True, slots=True)
class Silence:
    """Une fenetre pendant laquelle aucune commande n'a ete emise."""

    start: float
    end: float
    allies_before: int | None = None
    allies_after: int | None = None
    enemies_before: int | None = None
    enemies_after: int | None = None
    #: Motif d'abstention, quand la telemetrie de l'agent le fournit.
    #:
    #: `None` **n'est pas** « sans raison » : c'est « le journal du jeu ne la
    #: porte pas ». Voir l'en-tete du module.
    reason: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def allies_lost(self) -> int | None:
        if self.allies_before is None or self.allies_after is None:
            return None
        return self.allies_before - self.allies_after

    def explain(self) -> str:
        ligne = (
            f"  NO_ACTION t={self.start:.1f}..{self.end:.1f}s "
            f"({self.duration:.1f}s) reason={self.reason or 'inconnu'}"
        )
        perdues = self.allies_lost
        if perdues:
            ligne += f"  — {perdues} unite(s) alliee(s) perdue(s) pendant la fenetre"
        return ligne


@dataclass
class LiveReading:
    """Ce qu'une bataille reelle a produit comme commandes, et comme silences."""

    batches: list[Batch] = field(default_factory=list)
    silences: list[Silence] = field(default_factory=list)
    #: Effectifs releves, par instant : (temps, allies, ennemis).
    census: list[tuple[float, int, int]] = field(default_factory=list)

    @property
    def launched(self) -> int:
        return sum(item.launched for item in self.batches)

    @property
    def refused(self) -> int:
        return sum(item.refused for item in self.batches)

    @property
    def requested(self) -> int:
        return self.launched + self.refused

    @property
    def attacks(self) -> int:
        return sum(item.attacks for item in self.batches)

    @property
    def time_to_first_attack(self) -> float | None:
        """Instant du premier ordre d'attaque. `None` s'il n'y en a jamais eu."""
        return next((item.at for item in self.batches if item.attacks > 0), None)

    @property
    def longest_no_action_window(self) -> float:
        return max((item.duration for item in self.silences), default=0.0)

    def _counts_at(self, instant: float) -> tuple[int, int] | None:
        releve = [item for item in self.census if item[0] <= instant]
        return (releve[-1][1], releve[-1][2]) if releve else None

    @property
    def losses_before_first_attack(self) -> int | None:
        """Unites alliees perdues avant que le premier ordre d'attaque ne parte."""
        return self._losses_before(ally=True)

    @property
    def enemy_losses_before_first_attack(self) -> int | None:
        return self._losses_before(ally=False)

    def _losses_before(self, *, ally: bool) -> int | None:
        instant = self.time_to_first_attack
        if instant is None or not self.census:
            return None
        _, allies, ennemis = self.census[0]
        depart = allies if ally else ennemis
        courant = self._counts_at(instant)
        if courant is None:
            return None
        return depart - (courant[0] if ally else courant[1])

    def render(self) -> str:
        if not self.batches:
            return "  Aucun lot de commandes dans ce journal : l'agent n'a jamais pilote."
        lignes = [
            f"  {len(self.batches)} lot(s) de commandes, "
            f"{self.requested} action(s) demandee(s), "
            f"{self.launched} lancee(s), {self.refused} refusee(s)",
            "  premiere attaque : "
            + (
                f"{self.time_to_first_attack:.1f}s"
                if self.time_to_first_attack is not None
                else "**jamais**"
            ),
            f"  plus longue fenetre sans ordre : {self.longest_no_action_window:.1f}s",
        ]
        pertes = self.losses_before_first_attack
        adverses = self.enemy_losses_before_first_attack
        if pertes is not None:
            lignes.append(
                f"  pertes avant la premiere attaque : {pertes} alliee(s), {adverses} ennemie(s)"
            )
        if self.refused:
            lignes.append("")
            lignes += [
                f"  refus : sequence {item.sequence} — {item.error}"
                for item in self.batches
                if item.refused and item.error
            ]
        if self.silences:
            lignes += ["", "--- fenetres sans ordre ---", ""]
            lignes += [item.explain() for item in self.silences]
            if any(item.reason is None for item in self.silences):
                lignes += [
                    "",
                    "  **`reason=inconnu` ne veut pas dire « sans raison ».** Le journal",
                    "  du jeu porte les ordres envoyes, jamais les motifs de n'en envoyer",
                    "  aucun. Le motif viendra de la telemetrie de l'agent.",
                ]
        return "\n".join(lignes)


def read(lines: Iterable[str], *, silence_seconds: float = SILENCE_SECONDS) -> LiveReading:
    """Lit un journal de bataille et rend les lots, les silences et les effectifs.

    **Deduplication par `sequence`.** Un lot journalise deux fois — son `ACK` et
    sa ligne `manoeuvre` — ne compte qu'une fois : l'`ACK` est la source
    canonique, la ligne `manoeuvre` n'apporte que la ventilation.
    """
    lots: dict[int, dict[str, object]] = {}
    ordre: list[int] = []
    dernier: int | None = None
    releves: list[tuple[float, int, int]] = []

    for brute in lines:
        ligne = brute.rstrip()
        if PREFIX not in ligne:
            continue
        horloge = _CLOCK.search(ligne)
        instant = int(horloge.group("ms")) / 1000.0 if horloge is not None else 0.0
        corps = ligne.split(PREFIX, 1)[1].strip()

        if (found := _BATTLE.search(corps)) is not None:
            releve = (instant, int(found.group("allies")), int(found.group("enemies")))
            if not releves or releves[-1][1:] != releve[1:]:
                releves.append(releve)
            continue

        if (found := _ACK.search(corps)) is not None:
            charge = _payload(found.group("payload"))
            if charge is None or charge.get("status") != "accepted":
                continue
            sequence = _entier(charge.get("sequence"))
            detail = charge.get("detail")
            note = str(detail.get("note", "")) if isinstance(detail, dict) else ""
            comptes = _COUNTS.search(note)
            lots[sequence] = {
                "sequence": sequence,
                "at": instant,
                "launched": int(comptes.group("launched")) if comptes else 0,
                "refused": int(comptes.group("refused")) if comptes else 0,
                "error": charge.get("error") or None,
            }
            ordre.append(sequence)
            dernier = sequence
            continue

        if (found := _MANOEUVRE.search(corps)) is not None and dernier is not None:
            # La ligne `manoeuvre` **enrichit** le lot precedent ; elle n'en cree
            # jamais un nouveau. C'est toute la deduplication.
            lots[dernier].update(
                moves=int(found.group("moves")),
                attacks=int(found.group("attacks")),
                halts=int(found.group("halts")),
            )

    batches = [Batch(**lots[sequence]) for sequence in ordre]  # type: ignore[arg-type]
    return LiveReading(
        batches=batches,
        silences=_silences(batches, releves, silence_seconds),
        census=releves,
    )


def _entier(valeur: object, defaut: int = -1) -> int:
    return int(valeur) if isinstance(valeur, (int, float)) else defaut


def _payload(brut: str) -> dict[str, object] | None:
    try:
        charge = json.loads(brut)
    except json.JSONDecodeError:
        return None
    return charge if isinstance(charge, dict) else None


def _silences(
    batches: Sequence[Batch],
    releves: Sequence[tuple[float, int, int]],
    seuil: float,
) -> list[Silence]:
    """Intervalles entre deux lots depassant le seuil, avec leur contexte."""
    fenetres: list[Silence] = []
    for precedent, suivant in pairwise(batches):
        if suivant.at - precedent.at < seuil:
            continue
        avant = _counts_at(releves, precedent.at)
        apres = _counts_at(releves, suivant.at)
        fenetres.append(
            Silence(
                start=precedent.at,
                end=suivant.at,
                allies_before=avant[0] if avant else None,
                allies_after=apres[0] if apres else None,
                enemies_before=avant[1] if avant else None,
                enemies_after=apres[1] if apres else None,
            )
        )
    return fenetres


def _counts_at(releves: Sequence[tuple[float, int, int]], instant: float) -> tuple[int, int] | None:
    vus = [item for item in releves if item[0] <= instant]
    return (vus[-1][1], vus[-1][2]) if vus else None
