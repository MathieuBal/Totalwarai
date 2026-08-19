"""L'armee entre-t-elle en melee ensemble, ou par petits paquets ?

**Pourquoi ceci existe.** LIVE-001 a montre *ou* les commandes disparaissaient ;
il n'expliquait pas pourquoi la bataille se perdait. Le journal du 18/08 22h20 le
dit sans ambiguite, une fois les premiers contacts alignes :

.. code-block:: text

    1010   192,6 s      trois unites rapides, parties seules
    1011   206,6 s
    1012   241,6 s
    ------------------  235,5 secondes de vide
    1003   477,1 s      le premier fantassin de la ligne

.. rubric:: Ce que ces mesures observent exactement

**La melee, et rien d'autre.** Le contact se lit sur `in_melee` : ces chiffres
repondent a « dans quel ordre nos unites entrent-elles au corps a corps ? », pas
a « la manoeuvre est-elle coherente ? ».

La nuance decidera de la suite. Une manoeuvre parfaitement coordonnee peut
n'avoir que deux regiments en melee — les autres fixant, tirant a cent vingt
metres, ou attendant un flanc. Une lecture naive y verrait `2/12` et conclurait a
une mauvaise cohesion, alors que les douze unites tiendraient leur role.

C'est pourquoi ces mesures portent **`contact` dans leur nom**, et pourquoi elles
ne devront pas devenir mecaniquement des mesures de participation le jour ou la
manoeuvre existera. La participation sera alors fonction du role : contact pour
la ligne d'assaut, position et portee pour l'appui-feu, position de flanc, de
fixation, de reserve — et le juge de `ASSEMBLE -> FIX -> CONTACT -> EXPLOIT` sera
`participant_ready_share`, pas ceci.

.. rubric:: Mesurer avant de corriger

Ce module ne decide rien et n'impose aucun seuil. Il rend des chiffres
comparables avant et apres un changement de doctrine — sans quoi « la cohesion
s'est amelioree » resterait une impression.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from totalwar_ai.bridge.command_models import orders_take_effect

#: Fenetres d'observation apres le premier contact, en secondes.
COHORT_WINDOWS = (30.0, 60.0, 120.0)

#: Nombre de voisines qu'il suffit d'avoir pour ne pas etre seule.
#:
#: Une a ete choisie plutot que deux : une diversion a deux unites, un flanc a
#: trois, une reserve deliberee sont des manoeuvres legitimes. Ce qui compte est
#: qu'une unite ne parte pas seule, pas que l'armee entiere parte.
ISOLATION_THRESHOLD = 1

#: Actions qui n'envoient personne nulle part.
#:
#: `OrderTranslator` ne produit un `halt` que pour `HOLD_POSITION` ; tout le
#: reste devient un deplacement, une attaque, ou rien du tout. C'est la meme
#: frontiere que celle du lecteur live entre `halts` et `moves`/`attacks`, et
#: c'est elle qui separe `commanded_army_share` de `active_order_share`.
PASSIVE_ACTIONS = frozenset({"HOLD_POSITION"})

#: Ecart au-dela duquel deux entrees en melee appartiennent a deux vagues.
#:
#: Soixante secondes : au-dela, le renfort n'arrive plus dans le meme combat. La
#: bataille du 18/08 mesure 235,5 s entre la premiere vague et la suivante —
#: presque quatre fois cette fenetre.
SUPPORT_WINDOW = 60.0


@dataclass(frozen=True, slots=True)
class UnitTimeline:
    """Ce qu'une unite a vecu, reduit aux instants qui comptent."""

    unit_id: str
    #: Premiere apparition au roster. **Le denominateur en depend.**
    #:
    #: Une armee de douze unites renforcee de quatre regiments cinq minutes plus
    #: tard donnerait `3/16` a la premiere vague, alors que ces quatre-la ne
    #: pouvaient pas y participer. Les mesures relatives au premier contact ne
    #: comptent donc que les unites **deja presentes** a cet instant.
    first_seen_at: float | None = None
    #: Premier ordre recu, quel qu'il soit — deplacement, attaque ou arret.
    first_order_at: float | None = None
    #: Premier ordre qui met reellement en mouvement : deplacement ou attaque.
    #:
    #: **Un arret n'est pas une mise en mouvement.** Une doctrine qui enverrait
    #: douze `HOLD` afficherait « armee ordonnee a 100 % » sans que personne ne
    #: bouge : exactement le faux positif que ces mesures existent pour eviter.
    first_active_order_at: float | None = None
    #: Premiere entree en melee.
    first_contact_at: float | None = None


@dataclass
class Cohesion:
    """La chronologie d'entree en melee d'une armee, et ce qu'elle en dit."""

    units: list[UnitTimeline] = field(default_factory=list)

    # --- contact ------------------------------------------------------------

    @property
    def contacts(self) -> list[UnitTimeline]:
        engagees = [item for item in self.units if item.first_contact_at is not None]
        return sorted(engagees, key=lambda item: (item.first_contact_at or 0.0, item.unit_id))

    @property
    def no_contact(self) -> bool:
        """Aucune unite n'a jamais touche l'ennemi.

        **A distinguer d'une melee fragmentee.** Une bataille sans contact n'a pas
        une mauvaise cohesion de contact : elle n'en a **pas**. Confondre les deux
        ferait passer une armee qui n'engage jamais pour une armee parfaitement
        groupee.
        """
        return not self.contacts

    @property
    def first_contact_time(self) -> float | None:
        premiers = self.contacts
        return premiers[0].first_contact_at if premiers else None

    @property
    def first_contact_unit(self) -> str | None:
        premiers = self.contacts
        return premiers[0].unit_id if premiers else None

    def available_at(self, instant: float) -> list[UnitTimeline]:
        """Unites deja presentes a cet instant. Voir `UnitTimeline.first_seen_at`."""
        return [
            item
            for item in self.units
            if item.first_seen_at is None or item.first_seen_at <= instant
        ]

    def contact_cohort(self, window: float) -> int:
        depart = self.first_contact_time
        if depart is None:
            return 0
        return sum(
            1
            for item in self.contacts
            if item.first_contact_at is not None and item.first_contact_at - depart <= window
        )

    # --- vagues de melee ----------------------------------------------------

    @property
    def contact_waves(self) -> list[list[UnitTimeline]]:
        """Groupes d'entrees en melee separes par plus que la fenetre de soutien.

        **Meilleure description que « engagement isole ».** Elle ne pretend pas
        qu'un trio parti ensemble etait illegitime — un flanc a trois est une
        manoeuvre valable. Elle dit seulement : une premiere vague entre au
        combat, puis plus personne ne la rejoint pendant tant de secondes.
        """
        engagees = self.contacts
        if not engagees:
            return []
        vagues: list[list[UnitTimeline]] = [[engagees[0]]]
        for precedente, suivante in pairwise(engagees):
            ecart = (suivante.first_contact_at or 0.0) - (precedente.first_contact_at or 0.0)
            if ecart > SUPPORT_WINDOW:
                vagues.append([suivante])
            else:
                vagues[-1].append(suivante)
        return vagues

    @property
    def contact_wave_count(self) -> int:
        return len(self.contact_waves)

    @property
    def first_contact_wave_size(self) -> int:
        vagues = self.contact_waves
        return len(vagues[0]) if vagues else 0

    @property
    def first_contact_wave_share(self) -> float:
        """Part de l'armee **disponible** engagee dans la premiere vague."""
        depart = self.first_contact_time
        if depart is None:
            return 0.0
        presentes = self.available_at(depart)
        return self.first_contact_wave_size / len(presentes) if presentes else 0.0

    @property
    def largest_contact_gap(self) -> tuple[float, str, str] | None:
        """Le plus grand vide entre deux entrees en melee successives.

        **C'est le chiffre qui nomme le defaut.** Une mediane de delais ne montre
        pas ce vide : elle le noie parmi des arrivees appartenant deja a une autre
        phase de la bataille. Sur le 18/08, la mediane vaut 727 s — vraie et
        inutilisable — quand le vide vaut 235,5 s et designe l'endroit exact.
        """
        engagees = self.contacts
        if len(engagees) < 2:
            return None
        return max(
            (
                (suivante.first_contact_at or 0.0) - (precedente.first_contact_at or 0.0),
                precedente.unit_id,
                suivante.unit_id,
            )
            for precedente, suivante in pairwise(engagees)
        )

    @property
    def isolated_contacts(self) -> list[UnitTimeline]:
        """Unites entrees en melee sans qu'aucune voisine les y rejoigne a temps.

        **Metrique secondaire, et il faut le dire.** Avec le seuil a une voisine,
        les trois unites parties ensemble le 18/08 ne sont pas isolees : elles
        sont trois. Le phenomene observe — une premiere vague de trois, puis
        quatre minutes de vide — n'est donc **pas** porte par ce chiffre, mais par
        `contact_cohort` et `largest_contact_gap`.

        Elle reste utile pour ce qu'elle dit vraiment : une unite qui entre au
        contact seule ne mene pas un engagement, elle le subit.
        """
        engagees = self.contacts
        seules = []
        for item in engagees:
            instant = item.first_contact_at or 0.0
            voisines = sum(
                1
                for autre in engagees
                if autre is not item
                and abs((autre.first_contact_at or 0.0) - instant) <= SUPPORT_WINDOW
            )
            if voisines < ISOLATION_THRESHOLD:
                seules.append(item)
        return seules

    @property
    def support_lags(self) -> list[float]:
        depart = self.first_contact_time
        if depart is None:
            return []
        return [
            item.first_contact_at - depart
            for item in self.contacts[1:]
            if item.first_contact_at is not None
        ]

    @property
    def support_lag_median(self) -> float | None:
        delais = self.support_lags
        return statistics.median(delais) if delais else None

    @property
    def support_lag_max(self) -> float | None:
        delais = self.support_lags
        return max(delais) if delais else None

    # --- commandement -------------------------------------------------------

    def commanded_army_share(self, window: float) -> float:
        """Part de l'armee ayant recu **un ordre quelconque**, arrets compris.

        Mesure de commandes recues, **jamais preuve de cohesion** : douze `HOLD`
        y donneraient 100 %.
        """
        return self._share(window, active=False)

    def active_order_share(self, window: float) -> float:
        """Part de l'armee ayant recu un ordre qui la met en mouvement."""
        return self._share(window, active=True)

    def _share(self, window: float, *, active: bool) -> float:
        if not self.units:
            return 0.0
        instants = [
            item.first_active_order_at if active else item.first_order_at for item in self.units
        ]
        connus = [item for item in instants if item is not None]
        if not connus:
            return 0.0
        depart = min(connus)
        return sum(1 for item in connus if item - depart <= window) / len(self.units)

    def render(self) -> str:
        if not self.units:
            return "  Aucune unite observee."
        if self.no_contact:
            return (
                f"  {len(self.units)} unite(s), **aucun contact**.\n"
                "  La cohesion de melee n'est pas mauvaise ici : elle est **non definie**.\n"
                "  C'est un autre defaut, a compter a part."
            )
        premier = self.first_contact_time or 0.0
        presentes = len(self.available_at(premier))
        lignes = [
            f"  premier contact : {self.first_contact_unit} a {premier:.1f}s "
            f"({presentes} unite(s) disponible(s))",
            "",
        ]
        for fenetre in COHORT_WINDOWS:
            part = self.contact_cohort(fenetre)
            lignes.append(
                f"  cohorte melee a +{fenetre:>5.0f}s : {part}/{presentes} ({part / presentes:.0%})"
            )
        lignes += [
            "",
            f"  vagues de contact : {self.contact_wave_count}",
            f"  **premiere vague : {self.first_contact_wave_size}/{presentes} "
            f"({self.first_contact_wave_share:.0%})**",
        ]
        vide = self.largest_contact_gap
        if vide is not None:
            ecart, avant, apres = vide
            lignes.append(f"  **plus grand vide : {ecart:.1f}s** ({avant} -> {apres})")
        mediane, maximum = self.support_lag_median, self.support_lag_max
        if mediane is not None and maximum is not None:
            lignes.append(f"  delai de soutien : mediane {mediane:.1f}s, max {maximum:.1f}s")
        lignes.append("")
        for fenetre in (30.0, 60.0):
            lignes.append(
                f"  a +{fenetre:.0f}s : commandees {self.commanded_army_share(fenetre):.0%}, "
                f"**mises en mouvement {self.active_order_share(fenetre):.0%}**"
            )
        return "\n".join(lignes)


def study(units: Iterable[UnitTimeline]) -> Cohesion:
    return Cohesion(units=list(units))


@dataclass
class Survey:
    """Plusieurs batailles, et ce qu'on a le droit d'en moyenner.

    **Trois etats, pas deux.** `largest_contact_gap` exige deux entrees en melee
    pour exister. Une bataille ou une seule unite sur douze engage pendant que
    onze regardent n'a donc pas de vide mesurable — exactement comme une bataille
    sans aucun contact. Une agregation qui se contenterait de moyenner les vides
    definis ferait sortir le pire cas par la meme porte que l'absence de defaut.

    D'ou trois compteurs distincts, et l'interdiction de convertir un vide absent
    en zero : zero voudrait dire « aucun delai », c'est-a-dire le contraire de ce
    qui s'est produit.
    """

    battles: list[tuple[str, Cohesion]] = field(default_factory=list)

    def add(self, label: str, cohesion: Cohesion) -> None:
        self.battles.append((label, cohesion))

    @property
    def no_contact(self) -> list[tuple[str, Cohesion]]:
        return [item for item in self.battles if len(item[1].contacts) == 0]

    @property
    def single_contact(self) -> list[tuple[str, Cohesion]]:
        return [item for item in self.battles if len(item[1].contacts) == 1]

    @property
    def multi_contact(self) -> list[tuple[str, Cohesion]]:
        return [item for item in self.battles if len(item[1].contacts) >= 2]

    @property
    def gaps(self) -> list[float]:
        """Vides mesurables. **Ne contient que les batailles a deux contacts.**"""
        return [
            cohesion.largest_contact_gap[0]
            for _, cohesion in self.multi_contact
            if cohesion.largest_contact_gap is not None
        ]

    @property
    def first_wave_shares(self) -> list[float]:
        """Parts de premiere vague. Definie des qu'une unite engage, elle."""
        return [
            cohesion.first_contact_wave_share for _, cohesion in self.battles if cohesion.contacts
        ]

    def render(self) -> str:
        if not self.battles:
            return "  Aucune bataille observee."
        lignes = [
            f"  {len(self.battles)} bataille(s) : "
            f"{len(self.multi_contact)} avec melee comparable, "
            f"{len(self.single_contact)} a contact unique, "
            f"{len(self.no_contact)} sans contact",
            "",
        ]
        vides = self.gaps
        if vides:
            lignes.append(
                f"  plus grand vide : median {statistics.median(vides):.1f}s, "
                f"max {max(vides):.1f}s  ({len(vides)} bataille(s))"
            )
        else:
            lignes.append("  plus grand vide : **non defini** — aucune bataille a deux contacts")
        parts = self.first_wave_shares
        if parts:
            lignes.append(
                f"  premiere vague : mediane {statistics.median(parts):.0%}, "
                f"min {min(parts):.0%}  ({len(parts)} bataille(s))"
            )
        if self.single_contact:
            lignes += [
                "",
                "  **Contact unique** — une seule unite engage, les autres regardent :",
                "    " + ", ".join(nom for nom, _ in self.single_contact),
                "  Leur vide est indefini comme celui d'une bataille sans contact,",
                "  mais le defaut est inverse. Ne pas les confondre.",
            ]
        if self.no_contact:
            lignes += [
                "",
                f"  **Sans contact** : {', '.join(nom for nom, _ in self.no_contact)}",
                "  Cohesion de melee non definie — c'est un autre defaut, a compter a part.",
            ]
        return "\n".join(lignes)


def from_events(events: Sequence[object], states: Sequence[object]) -> Cohesion:
    """Chronologie tiree d'une bataille du banc.

    `ACTION_SENT` porte les acteurs et l'instant ; les etats portent la presence
    et l'engagement. Les deux sources du projet — banc et bataille reelle —
    alimentent la **meme** mesure, sans quoi leurs chiffres ne se compareraient
    pas.

    **La symetrie porte sur les quatre champs, `first_seen_at` compris.** Si le
    banc prenait pour denominateur « toutes les unites du scenario » quand le live
    prend « les alliees deja presentes », les deux `first_contact_wave_share`
    porteraient le meme nom sans mesurer la meme chose — et la comparaison
    banc/live qui a conclu que le banc ne reproduit pas la fragmentation
    comparerait deux definitions.
    """
    from totalwar_ai.domain.unit_state import Side
    from totalwar_ai.telemetry.events import EventType

    ordres: dict[str, float] = {}
    actifs: dict[str, float] = {}
    for event in events:
        if event.type is not EventType.ACTION_SENT:  # type: ignore[attr-defined]
            continue
        instant = float(event.game_time)  # type: ignore[attr-defined]
        payload = event.payload  # type: ignore[attr-defined]
        for acteur in payload.get("actors") or ():
            ordres.setdefault(str(acteur), instant)
            if payload.get("type") not in PASSIVE_ACTIONS:
                actifs.setdefault(str(acteur), instant)

    premieres: dict[str, float] = {}
    contacts: dict[str, float] = {}
    for state in states:
        instant = float(state.game_time)  # type: ignore[attr-defined]
        for unit in state.side_units(Side.ALLY):  # type: ignore[attr-defined]
            premieres.setdefault(unit.id, instant)
            if unit.is_engaged:
                contacts.setdefault(unit.id, instant)

    return study(
        UnitTimeline(
            unit_id=unit_id,
            first_seen_at=premieres[unit_id],
            first_order_at=ordres.get(unit_id),
            first_active_order_at=actifs.get(unit_id),
            first_contact_at=contacts.get(unit_id),
        )
        for unit_id in sorted(premieres)
    )


def from_battle_log(lines: Iterable[str]) -> Cohesion:
    """Chronologie tiree d'un corpus de bataille reelle (`BattleRecorder`).

    .. rubric:: La population vient du roster, et de nulle part ailleurs

    Le lecteur precedent construisait l'armee a partir des unites **ayant recu un
    ordre**, tout en affirmant en commentaire l'inverse : qu'une unite jamais
    commandee mais entree au contact appartenait quand meme a l'armee. Une unite
    oubliee par la doctrine disparaissait donc du denominateur — et c'est
    exactement le defaut que cette mesure existe pour attraper. Une doctrine
    future qui abandonnerait deux regiments aurait vu ses ratios **s'ameliorer**.

    Il enregistrait de surcroit `in_melee` pour tous les identifiants de
    l'inventaire, ennemis compris : sur un corpus a deux camps, la chronologie
    censee decrire notre armee contenait des unites adverses.

    Le roster porte `side`, donc la reponse : les allies sont la population, et
    ordres comme contacts ne font que renseigner leurs instants — `None` compris.

    .. rubric:: Le roster est republie, donc temporel

    `_refresh_roster` ajoute les unites qui apparaissent en cours de bataille.
    Compter un renfort arrive a 648 s dans le denominateur d'une premiere vague
    commencee a 86 s le diluerait sans qu'il ait pu y participer : d'ou
    `first_seen_at`, lu sur le premier etat ou l'unite est **observee**, et non
    sur la ligne de roster, qui ne porte qu'un numero de tour.

    .. rubric:: Les ordres anterieurs a `Deployed` ne comptent pas

    Le moteur les acquitte sans les executer. Les compter faisait annoncer
    « armee ordonnee a 100 % » sur la bataille du 18/08, ou les douze unites
    avaient recu leur premier ordre a 3,1 s — avant meme que la phase `Deployed`
    ne commence — sans que personne ne bouge.
    """
    allies: set[str] = set()
    premieres: dict[str, float] = {}
    ordres: dict[str, float] = {}
    actifs: dict[str, float] = {}
    contacts: dict[str, float] = {}

    for ligne in lines:
        try:
            entree = json.loads(ligne)
        except ValueError:
            continue
        if not isinstance(entree, dict):
            continue

        roster = entree.get("roster")
        if isinstance(roster, dict):
            allies |= {
                str(identifiant)
                for identifiant, fiche in roster.items()
                if isinstance(fiche, dict) and fiche.get("side") == "ally"
            }
            continue

        instant = float(entree.get("game_time_ms", 0)) / 1000.0

        for unite in entree.get("units") or ():
            identifiant = str(unite.get("id", ""))
            if identifiant not in allies:
                continue
            premieres.setdefault(identifiant, instant)
            if unite.get("in_melee"):
                contacts.setdefault(identifiant, instant)

        if not orders_take_effect(str(entree.get("phase", ""))):
            continue
        ordonnees = entree.get("orders") or {}
        for deplacement in ordonnees.get("moves") or ():
            _note(str(deplacement.get("unit_id", "")), instant, ordres, actifs, active=True)
        for attaque in ordonnees.get("attacks") or ():
            identifiant = str(attaque.get("unit_id", attaque.get("actor_id", "")))
            _note(identifiant, instant, ordres, actifs, active=True)
        for arret in ordonnees.get("halts") or ():
            _note(str(arret), instant, ordres, actifs, active=False)

    return study(
        UnitTimeline(
            unit_id=unit_id,
            first_seen_at=premieres.get(unit_id),
            first_order_at=ordres.get(unit_id),
            first_active_order_at=actifs.get(unit_id),
            first_contact_at=contacts.get(unit_id),
        )
        for unit_id in sorted(allies)
    )


def _note(
    unit_id: str,
    instant: float,
    ordres: dict[str, float],
    actifs: dict[str, float],
    *,
    active: bool,
) -> None:
    """Un ordre recu, et s'il met reellement en mouvement."""
    if not unit_id:
        return
    ordres.setdefault(unit_id, instant)
    if active:
        actifs.setdefault(unit_id, instant)
