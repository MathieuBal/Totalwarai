"""L'armee arrive-t-elle ensemble, ou par petits paquets ?

**Pourquoi ceci existe.** LIVE-001 a montre *ou* les commandes disparaissaient ;
il n'expliquait pas pourquoi la bataille se perdait. Le journal du 18/08 22h20 le
dit sans ambiguite, une fois les premiers contacts alignes :

.. code-block:: text

    1010   192,6 s      <- trois unites rapides, parties seules
    1011   206,6 s
    1012   241,6 s
    ------------------  284,5 secondes de vide
    1003   477,1 s      <- le premier fantassin de la ligne
    1004   623,1 s
    1002   687,6 s

Dans la minute qui suit le premier choc, **trois unites sur douze** ont rejoint
le combat. C'est la traduction chiffree de « se faire manger au compte-gouttes ».

.. rubric:: Mesurer avant de corriger

Ce module ne decide rien et ne propose aucun seuil. Il rend des chiffres
comparables **avant et apres** un changement de doctrine, sur le banc comme sur
une bataille reelle — sans quoi « la cohesion s'est amelioree » resterait une
impression.

.. rubric:: Ce qu'on ne mesure surtout pas

« Toute l'armee doit avancer » serait un mauvais critere. Une diversion a deux
unites, un flanc a trois, une reserve deliberee, une ligne qui fixe pendant qu'un
groupe frappe : toutes sont des manoeuvres legitimes.

Le contrat visé est plus etroit, et c'est celui qu'`isolated_contact` approche :

    une unite ne doit pas declencher un engagement qu'elle est censee mener
    avec du soutien, si ce soutien n'est pas en situation de participer.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

#: Fenetres d'observation apres le premier contact, en secondes.
COHORT_WINDOWS = (30.0, 60.0, 120.0)

#: Delai au-dela duquel une unite entree au contact est restee seule.
#:
#: Soixante secondes : au-dela, le renfort n'arrive plus dans le meme combat. La
#: bataille du 18/08 mesure 284,5 s entre le premier choc et le premier soutien
#: de la ligne — cinq fois cette fenetre.
SUPPORT_WINDOW = 60.0

#: Nombre de voisines au contact en deca duquel un engagement est dit isole.
#:
#: **Une.** C'est-a-dire : n'est isolee que l'unite qui s'engage sans qu'aucune
#: autre ne combatte dans la fenetre de soutien.
#:
#: Le seuil valait d'abord deux, et c'etait trop severe : il condamnait une
#: diversion a deux unites, un flanc a trois, une ligne qui fixe pendant qu'un
#: groupe frappe — toutes des manoeuvres legitimes. Tant que le planificateur
#: n'a pas de notion de manoeuvre, il n'existe aucun moyen de distinguer une
#: paire deliberee d'une paire accidentelle, et seul l'isolement **complet** est
#: non ambigu.
#:
#: Le jour ou la manoeuvre portera ses participants, ce seuil devra ceder la
#: place a « la part des soutiens assignes qui peut participer ».
ISOLATION_THRESHOLD = 1


@dataclass(frozen=True, slots=True)
class UnitTimeline:
    """Ce qu'une unite a vecu, reduit aux deux instants qui comptent."""

    unit_id: str
    #: Premier ordre **reellement emis** vers le jeu pour cette unite.
    first_order_at: float | None = None
    #: Premiere entree au contact.
    first_contact_at: float | None = None


@dataclass
class Cohesion:
    """La chronologie d'engagement d'une armee, et ce qu'elle en dit."""

    units: list[UnitTimeline] = field(default_factory=list)

    @property
    def contacts(self) -> list[UnitTimeline]:
        """Unites entrees au contact, dans l'ordre ou elles y sont entrees."""
        engagees = [item for item in self.units if item.first_contact_at is not None]
        return sorted(engagees, key=lambda item: (item.first_contact_at or 0.0, item.unit_id))

    @property
    def first_contact_time(self) -> float | None:
        premiers = self.contacts
        return premiers[0].first_contact_at if premiers else None

    @property
    def first_contact_unit(self) -> str | None:
        premiers = self.contacts
        return premiers[0].unit_id if premiers else None

    def contact_cohort(self, window: float) -> int:
        """Unites au contact dans les `window` secondes suivant le premier choc."""
        depart = self.first_contact_time
        if depart is None:
            return 0
        return sum(
            1
            for item in self.contacts
            if item.first_contact_at is not None and item.first_contact_at - depart <= window
        )

    @property
    def support_lags(self) -> list[float]:
        """Delais entre le premier choc et chaque arrivee ulterieure."""
        depart = self.first_contact_time
        if depart is None:
            return []
        return [
            item.first_contact_at - depart
            for item in self.contacts[1:]
            if item.first_contact_at is not None
        ]

    @property
    def largest_contact_gap(self) -> tuple[float, str, str] | None:
        """Le plus grand vide entre deux entrees au contact successives.

        **C'est le chiffre qui nomme le defaut.** Sur la bataille du 18/08, trois
        unites s'engagent entre 192,6 s et 241,6 s, puis **plus rien pendant 235
        secondes**. Une mediane de delais ne montre pas ce vide ; elle le noie
        parmi des arrivees tardives qui appartiennent deja a une autre phase de
        la bataille.
        """
        engagees = self.contacts
        if len(engagees) < 2:
            return None
        ecarts = [
            (
                (suivante.first_contact_at or 0.0) - (precedente.first_contact_at or 0.0),
                precedente.unit_id,
                suivante.unit_id,
            )
            for precedente, suivante in pairwise(engagees)
        ]
        return max(ecarts)

    @property
    def support_lag_median(self) -> float | None:
        delais = self.support_lags
        return statistics.median(delais) if delais else None

    @property
    def support_lag_max(self) -> float | None:
        delais = self.support_lags
        return max(delais) if delais else None

    def ordered_army_share(self, window: float) -> float:
        """Part de l'armee ayant recu un ordre dans les `window` premieres secondes.

        Comptee depuis le **premier ordre emis**, et non depuis le debut de la
        bataille : ce que l'on mesure est la mise en mouvement, pas le temps
        passe en deploiement.
        """
        if not self.units:
            return 0.0
        instants = [item.first_order_at for item in self.units if item.first_order_at is not None]
        if not instants:
            return 0.0
        depart = min(instants)
        return sum(1 for item in instants if item - depart <= window) / len(self.units)

    @property
    def isolated_contacts(self) -> list[UnitTimeline]:
        """Unites entrees au contact sans soutien en situation de participer.

        **Approximation assumee, et elle est temporelle.** Le contrat vise porte
        sur les soutiens *assignes a la meme manoeuvre* — notion qui n'existe pas
        encore dans le planificateur. En attendant, on constate l'isolement par
        ce qui est observable : une unite entre au contact, et trop peu d'alliees
        l'y rejoignent dans la fenetre de soutien.

        Le jour ou la manoeuvre sera une entite du planificateur, ce calcul
        devra porter sur ses participants et non sur toute l'armee.
        """
        isolees: list[UnitTimeline] = []
        for unite in self.contacts:
            instant = unite.first_contact_at
            if instant is None:
                continue
            voisines = sum(
                1
                for autre in self.contacts
                if autre.unit_id != unite.unit_id
                and autre.first_contact_at is not None
                and abs(autre.first_contact_at - instant) <= SUPPORT_WINDOW
            )
            if voisines < ISOLATION_THRESHOLD:
                isolees.append(unite)
        return isolees

    def render(self) -> str:
        if not self.units:
            return "  Aucune unite observee."
        premier = self.first_contact_time
        if premier is None:
            return (
                f"  {len(self.units)} unite(s), **aucun contact** : l'armee n'a jamais\n"
                "  engage le combat."
            )
        lignes = [
            f"  premier contact : {self.first_contact_unit} a {premier:.1f}s",
            "",
        ]
        for fenetre in COHORT_WINDOWS:
            part = self.contact_cohort(fenetre)
            lignes.append(
                f"  cohorte a +{fenetre:>5.0f}s : {part}/{len(self.units)} "
                f"({part / len(self.units):.0%})"
            )
        lignes.append("")
        mediane, maximum = self.support_lag_median, self.support_lag_max
        if mediane is not None:
            lignes.append(f"  delai de soutien : mediane {mediane:.1f}s, max {maximum:.1f}s")
        vide = self.largest_contact_gap
        if vide is not None:
            ecart, avant, apres = vide
            lignes.append(f"  **plus grand vide : {ecart:.1f}s** ({avant} -> {apres})")
        for fenetre in (30.0, 60.0):
            lignes.append(
                f"  armee ordonnee a +{fenetre:.0f}s : {self.ordered_army_share(fenetre):.0%}"
            )
        isolees = self.isolated_contacts
        lignes += ["", f"  **engagements isoles : {len(isolees)}**"]
        if isolees:
            lignes.append(
                "    "
                + ", ".join(f"{item.unit_id} a {item.first_contact_at:.0f}s" for item in isolees)
            )
            lignes += [
                "",
                "  Une unite qui entre au contact sans que ses voisines l'y rejoignent",
                "  ne mene pas un engagement : elle le subit.",
            ]
        return "\n".join(lignes)


def study(units: Iterable[UnitTimeline]) -> Cohesion:
    return Cohesion(units=list(units))


def from_events(events: Sequence[object], states: Sequence[object]) -> Cohesion:
    """Chronologie tiree d'une bataille du banc.

    `ACTION_SENT` porte les acteurs et l'instant ; les etats portent l'engagement.
    Les deux sources du projet — banc et bataille reelle — alimentent ainsi la
    **meme** mesure, sans quoi leurs chiffres ne se compareraient pas.
    """
    from totalwar_ai.domain.unit_state import Side
    from totalwar_ai.telemetry.events import EventType

    ordres: dict[str, float] = {}
    for event in events:
        if event.type is not EventType.PLAN_SELECTED and event.type.value == "action_sent":  # type: ignore[attr-defined]
            for acteur in event.payload.get("actors") or ():  # type: ignore[attr-defined]
                ordres.setdefault(str(acteur), float(event.game_time))  # type: ignore[attr-defined]

    contacts: dict[str, float] = {}
    connues: set[str] = set()
    for state in states:
        for unit in state.side_units(Side.ALLY):  # type: ignore[attr-defined]
            connues.add(unit.id)
            if unit.is_engaged:
                contacts.setdefault(unit.id, float(state.game_time))  # type: ignore[attr-defined]

    return study(
        UnitTimeline(
            unit_id=unit_id,
            first_order_at=ordres.get(unit_id),
            first_contact_at=contacts.get(unit_id),
        )
        for unit_id in sorted(connues)
    )


def from_battle_log(lines: Iterable[str]) -> Cohesion:
    """Chronologie tiree d'un corpus de bataille reelle (`BattleRecorder`).

    Le contact se lit sur `in_melee` dans l'inventaire des unites ; l'ordre, sur
    les `orders` d'un tour de decision — **ceux reellement envoyes au jeu**, pas
    les intentions. Une intention deja satisfaite ne met personne en mouvement,
    et la compter ferait paraitre l'armee ordonnee alors qu'elle ne bouge pas.
    """
    import json

    ordres: dict[str, float] = {}
    contacts: dict[str, float] = {}
    connues: set[str] = set()

    for ligne in lines:
        try:
            entree = json.loads(ligne)
        except ValueError:
            continue
        if not isinstance(entree, dict):
            continue
        instant = float(entree.get("game_time_ms", 0)) / 1000.0
        # **Les ordres anterieurs a `Deployed` ne comptent pas.** Le moteur les
        # acquitte sans les executer : les compter faisait annoncer « armee
        # ordonnee a 100 % » sur une bataille du 18/08 ou les douze unites
        # avaient toutes recu leur premier ordre a 3,1 s — c'est-a-dire avant que
        # la phase `Deployed` ne commence a 7,6 s, donc sans qu'aucune ne bouge.
        jouable = entree.get("phase") in (None, "", "Deployed", "Complete")

        for unite in entree.get("units") or ():
            identifiant = str(unite.get("id", ""))
            if not identifiant:
                continue
            # L'inventaire porte les deux camps : seules nos unites ont recu des
            # ordres, et ce sont elles dont on mesure la cohesion.
            if unite.get("in_melee"):
                contacts.setdefault(identifiant, instant)

        ordonnees = (entree.get("orders") or {}) if jouable else {}
        for deplacement in ordonnees.get("moves") or ():
            connues.add(str(deplacement.get("unit_id", "")))
            ordres.setdefault(str(deplacement.get("unit_id", "")), instant)
        for attaque in ordonnees.get("attacks") or ():
            identifiant = str(attaque.get("unit_id", attaque.get("actor_id", "")))
            if identifiant:
                connues.add(identifiant)
                ordres.setdefault(identifiant, instant)
        for arret in ordonnees.get("halts") or ():
            connues.add(str(arret))
            ordres.setdefault(str(arret), instant)

    connues.discard("")
    # Une unite jamais commandee mais entree au contact appartient quand meme a
    # l'armee : l'omettre flatterait la cohorte.
    connues |= {identifiant for identifiant in contacts if identifiant in ordres} or set()
    return study(
        UnitTimeline(
            unit_id=identifiant,
            first_order_at=ordres.get(identifiant),
            first_contact_at=contacts.get(identifiant),
        )
        for identifiant in sorted(connues)
    )
