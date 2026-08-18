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

#: Duree minimale d'une fenetre sans commande pour qu'elle soit rapportee.
#:
#: Vingt secondes : au-dela, ce n'est plus le rythme normal d'un agent qui
#: replanifie toutes les dix secondes, c'est une abstention.
SILENCE_SECONDS = 20.0

#: Phase a partir de laquelle un ordre produit reellement un effet.
#:
#: **Avant elle, le moteur acquitte sans rien faire.** Notre propre Lua le
#: documente : « un ordre emis avant `Deployed` est accepte par le moteur mais ne
#: produit aucun deplacement — constate en jeu, unite immobile 33 secondes durant
#: apres un ordre acquitte ».
ACTIVE_PHASE = "Deployed"

#: Phase a partir de laquelle on n'attend plus rien du general.
#:
#: `VictoryCountdown` et non `Complete` : quand le decompte commence, le combat
#: est deja decide, et un silence n'y est plus une abstention.
CLOSING_PHASE = "VictoryCountdown"

#: Phases de repli, si le decompte n'apparait pas.
FALLBACK_CLOSING = ("Complete",)

#: Temps de bataille, en millisecondes, tel que le jeu le prefixe a chaque ligne.
_CLOCK = re.compile(r"<(?P<ms>\d+)ms>")
_ACK = re.compile(r"ACK\s+(?P<payload>\{.*\})\s*$")
#: Changement de phase **annonce**, par la sonde ou par le moteur.
#:
#: **Jamais le heartbeat `BATTLE phase ...`.** Celui-la ne journalise que les
#: occurrences 1, 2, 3 puis une sur vingt : il est deliberement epars, donc
#: structurellement inapte a borner quoi que ce soit. Sur le journal du 18/08, le
#: premier heartbeat `Deployed` arrive a 11,1 s alors que la phase a change a
#: **8,4 s** — et cette confusion a fait compter trois lots pre-`Deployed` la ou
#: il y en avait deux.
#:
#: *Le premier evenement qui produit une valeur n'est pas l'instant ou l'etat est
#: devenu vrai.*
_PHASE = re.compile(r"(?:phase : |Battle is now entering phase: )(?P<phase>\w+)")
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
class CommandSilence:
    """Une fenetre pendant laquelle **aucune commande n'a ete emise**.

    Le nom porte exactement ce que le journal prouve. Il ne dit pas que l'agent
    ne faisait rien : un ordre anterieur pouvait encore courir, le planificateur
    pouvait deliberer, la securite pouvait tout bloquer. `NO_EFFECTIVE_CONTROL`
    demandera la telemetrie de l'agent ; `NO_COMMAND` se lit ici.
    """

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
            f"  NO_COMMAND t={self.start:.1f}..{self.end:.1f}s "
            f"({self.duration:.1f}s) reason={self.reason or 'inconnu'}"
        )
        perdues = self.allies_lost
        if perdues:
            ligne += f"  — {perdues} unite(s) alliee(s) perdue(s) pendant la fenetre"
        return ligne


@dataclass
class LiveReading:
    """Ce qu'une bataille reelle a produit comme commandes, et comme silences."""

    #: Lots emis **apres** `Deployed` : les seuls qui aient pu produire un effet.
    batches: list[Batch] = field(default_factory=list)
    #: Lots emis **avant** `Deployed` : acquittes par le moteur, sans effet.
    #:
    #: Les mêler aux autres ferait croire a une activite qui n'a rien produit.
    #: Sur le journal du 18/08 : 2 lots, 13 ordres, entre 3,1 s et 4,6 s.
    pre_deployed: list[Batch] = field(default_factory=list)
    silences: list[CommandSilence] = field(default_factory=list)
    #: Effectifs releves, par instant : (temps, allies, ennemis).
    census: list[tuple[float, int, int]] = field(default_factory=list)
    #: Bornes de la periode ou l'on attend des ordres, lues sur les **evenements**.
    active_from: float | None = None
    closing_at: float | None = None

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
    def longest_no_command_window(self) -> float:
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
            f"  {len(self.batches)} lot(s) de commandes effectifs, "
            f"{self.requested} action(s) demandee(s), "
            f"{self.launched} lancee(s), {self.refused} refusee(s)",
            "  premiere attaque : "
            + (
                f"{self.time_to_first_attack:.1f}s"
                if self.time_to_first_attack is not None
                else "**jamais**"
            ),
            f"  plus longue fenetre sans commande : {self.longest_no_command_window:.1f}s",
        ]
        pertes = self.losses_before_first_attack
        adverses = self.enemy_losses_before_first_attack
        if pertes is not None:
            # **« Unites disparues », et non « pertes ».** Ce compte suit les
            # unites sorties du releve `BATTLE` ; il ne dit rien des hommes tues
            # ni de la force effective perdue. La revision 16 separera les trois.
            lignes.append(
                f"  unites disparues avant la premiere attaque : {pertes} alliee(s), "
                f"{adverses} ennemie(s)"
            )
        if self.active_from is not None:
            fin = f"{self.closing_at:.1f}s" if self.closing_at is not None else "?"
            lignes.append(f"  fenetre active : {self.active_from:.1f}s -> {fin}")
        if self.pre_deployed:
            lignes += [
                "",
                f"  **PRE_DEPLOYED_COMMAND : {len(self.pre_deployed)} lot(s), "
                f"{sum(item.launched for item in self.pre_deployed)} ordre(s)** avant `Deployed`.",
                "  Le moteur les acquitte et ne les execute pas : ils ne comptent pas",
                "  parmi les commandes effectives. Ils occupent tout de meme une",
                "  signature d'anti-repetition pendant `order_ttl`.",
            ]
        if self.refused:
            lignes.append("")
            lignes += [
                f"  refus : sequence {item.sequence} — {item.error}"
                for item in self.batches
                if item.refused and item.error
            ]
        if self.silences:
            lignes += ["", "--- fenetres sans commande ---", ""]
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
    """Lit un journal de bataille : lots, silences, effectifs, bornes de phase.

    Cinq contrats, chacun tire d'un defaut mesure :

    1. **une sequence, un lot** — la deduplication porte sur l'insertion, pas
       seulement sur le dictionnaire ;
    2. **un lot est un `ACK` qui compte des ordres** — tout autre `accepted` n'en
       est pas un et ne fractionne aucun silence ;
    3. le fait mesure est l'absence de **commande**, jamais l'absence d'action ;
    4. **les bornes viennent des evenements de phase**, jamais des heartbeats ;
    5. les ordres anterieurs a `Deployed` sont comptes **a part** : le moteur les
       acquitte sans rien faire.
    """
    lots: dict[int, dict[str, object]] = {}
    ordre: list[int] = []
    dernier: int | None = None
    releves: list[tuple[float, int, int]] = []
    phases: list[tuple[float, str]] = []

    for brute in lines:
        ligne = brute.rstrip()
        horloge = _CLOCK.search(ligne)
        instant = int(horloge.group("ms")) / 1000.0 if horloge is not None else 0.0

        # **Avant le filtre de prefixe.** L'annonce de phase du moteur — « Battle
        # is now entering phase: Deployed » — ne porte pas notre marque, et c'est
        # pourtant la source la plus sure de l'instant du changement.
        if (found := _PHASE.search(ligne)) is not None:
            phases.append((instant, found.group("phase")))
            continue

        if PREFIX not in ligne:
            continue
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
            detail = charge.get("detail")
            note = str(detail.get("note", "")) if isinstance(detail, dict) else ""
            comptes = _COUNTS.search(note)
            if comptes is None:
                # **Un accuse qui ne compte aucun ordre n'est pas un lot de
                # commandes.** Un futur `DELEGATE accepted` en creerait un faux,
                # qui couperait une fenetre de silence en deux et raccourcirait
                # `longest_no_command_window` sans que rien ne le dise.
                continue
            sequence = _entier(charge.get("sequence"))
            if sequence in lots:
                # Une sequence, un lot — meme si le meme accuse est journalise
                # deux fois. Le dictionnaire seul ne suffisait pas : `ordre`
                # recevait deux fois la cle et reconstruisait deux `Batch`.
                dernier = sequence
                continue
            lots[sequence] = {
                "sequence": sequence,
                "at": instant,
                "launched": int(comptes.group("launched")),
                "refused": int(comptes.group("refused")),
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
    debut_actif = _phase_at(phases, ACTIVE_PHASE)
    fin_active = _closing_at(phases, releves, batches)
    effectifs = [item for item in batches if debut_actif is None or item.at >= debut_actif]
    return LiveReading(
        batches=effectifs,
        pre_deployed=[
            item for item in batches if debut_actif is not None and item.at < debut_actif
        ],
        silences=_silences(effectifs, releves, silence_seconds, debut_actif, fin_active),
        census=releves,
        active_from=debut_actif,
        closing_at=fin_active,
    )


def _phase_at(phases: Sequence[tuple[float, str]], voulue: str) -> float | None:
    """Instant du **changement** vers cette phase, jamais celui d'un heartbeat."""
    return next((instant for instant, nom in phases if nom == voulue), None)


def _closing_at(
    phases: Sequence[tuple[float, str]],
    releves: Sequence[tuple[float, int, int]],
    batches: Sequence[Batch],
) -> float | None:
    """Fin de la periode ou l'on attend encore des ordres.

    `VictoryCountdown` d'abord : au-dela, le combat est decide. `Complete` sert de
    repli, et a defaut le dernier instant observe — sans quoi une bataille dont la
    fin n'est pas journalisee perdrait sa fenetre de silence terminale.
    """
    for nom in (CLOSING_PHASE, *FALLBACK_CLOSING):
        if (instant := _phase_at(phases, nom)) is not None:
            return instant
    derniers = [item[0] for item in releves] + [item.at for item in batches]
    return max(derniers) if derniers else None


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
    active_from: float | None,
    closing_at: float | None,
) -> list[CommandSilence]:
    """Fenetres sans commande, **bornes de bataille comprises**.

    `pairwise` seul ne voit que les creux entre deux commandes. Un agent qui joue
    deux minutes puis se tait jusqu'a la defaite n'aurait jamais de « commande
    suivante », et sa paralysie ne serait comptee nulle part : la mesure
    afficherait `longest_no_command_window = 0` sur la bataille la plus muette.
    """
    bornes: list[tuple[float, float]] = []
    if active_from is not None and batches:
        bornes.append((active_from, batches[0].at))
    bornes += [(precedent.at, suivant.at) for precedent, suivant in pairwise(batches)]
    if closing_at is not None and batches:
        bornes.append((batches[-1].at, closing_at))

    fenetres: list[CommandSilence] = []
    for depart, arrivee in bornes:
        if arrivee - depart < seuil:
            continue
        avant = _counts_at(releves, depart)
        apres = _counts_at(releves, arrivee)
        fenetres.append(
            CommandSilence(
                start=depart,
                end=arrivee,
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
