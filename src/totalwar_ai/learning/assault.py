"""Que devient la superiorite locale entre le choix et le contact ?

**Pourquoi ceci existe.** La primitive de concentration publie un rapport local
au moment du choix — 1,50 sur `outnumbered`, pour un rapport global de 0,67. Elle
ne publiait rien du tout sur ce que ce rapport devient ensuite, et j'en ai
pourtant tire une conclusion : « ce qui manque pour convertir est probablement de
la vitesse ». Je n'avais mesure ni la vitesse ni la conversion.

Deux histoires sont possibles, elles appellent des correctifs **opposes**, et le
seul chiffre qui les separe est le rapport au contact :

* il **s'effondre** entre le choix et le contact → l'adversaire s'est ressoude
  avant notre arrivee, et c'est un probleme de mobilite : il faut un cout
  d'affectation qui integre le temps de trajet ;
* il **tient** et le secteur ne rompt pas → notre superiorite locale ne se
  transforme pas en pertes adverses, et le probleme est dans le ciblage,
  la concentration du feu ou l'achevement — pas dans la vitesse.

Ce module ne tranche pas : il rend le chiffre. Comme :mod:`elevation`, il refuse
de conclure sous un nombre de releves, et prefere la mediane a la moyenne — un
seul assaut aberrant deplace une moyenne et ne deplace pas une mediane.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from totalwar_ai.telemetry.events import Event, EventType

#: Part des assaillants au contact a partir de laquelle l'assaut est « engage ».
MAIN_CONTACT_SHARE = 0.5

#: Nombre d'assauts sous lequel on ne conclut pas.
#:
#: Trois assauts peuvent tous tomber sur la meme configuration. En dessous, la
#: mediane ne dit rien que le hasard du scenario ne dirait aussi bien.
MINIMUM_EPISODES = 5

#: Chute de rapport au-dela de laquelle l'avantage s'est « volatilise ».
#:
#: Un cinquieme perdu entre le choix et le contact reste dans le bruit d'un
#: engagement ; un tiers ne l'est plus, et designe l'approche comme coupable.
COLLAPSE_SHARE = 0.30


@dataclass(frozen=True, slots=True)
class Episode:
    """Un assaut, du choix a son issue."""

    sector: int
    attackers: int
    #: Rapport local au moment du choix.
    selected: float
    started_at: float
    #: Rapport au premier contact, et delai. `None` : le contact n'a jamais eu lieu.
    first_contact: float | None = None
    first_contact_delay: float | None = None
    #: Rapport quand la moitie des assaillants est au contact.
    main_contact: float | None = None
    main_contact_delay: float | None = None

    @property
    def reached_contact(self) -> bool:
        return self.first_contact is not None

    @property
    def kept(self) -> float | None:
        """Part du rapport initial conservee au contact principal.

        C'est **le** chiffre de ce module. Au-dessus de 1, l'assaut est arrive en
        meilleure posture qu'au depart ; nettement en dessous, il est arrive trop
        tard.
        """
        arrivee = self.main_contact if self.main_contact is not None else self.first_contact
        if arrivee is None or self.selected <= 1e-9:
            return None
        return arrivee / self.selected

    def explain(self) -> str:
        if not self.reached_contact:
            return (
                f"  secteur {self.sector} : choisi a {self.selected:.2f}, "
                f"**jamais arrive au contact**"
            )
        garde = self.kept
        return (
            f"  secteur {self.sector} : {self.selected:.2f} au choix -> "
            f"{self.first_contact:.2f} au contact"
            + (
                f" -> {self.main_contact:.2f} engage"
                if self.main_contact is not None
                else " (jamais engage a moitie)"
            )
            + (f"  [{garde:.0%} conserve]" if garde is not None else "")
        )


@dataclass
class Conversion:
    """Ce que les assauts d'une bataille ont donne."""

    episodes: list[Episode] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        return len(self.episodes) >= MINIMUM_EPISODES

    @property
    def reached(self) -> list[Episode]:
        return [item for item in self.episodes if item.reached_contact]

    @property
    def never_reached(self) -> int:
        """Assauts choisis qui n'ont jamais touche leur secteur.

        Un assaut qui n'arrive pas est un cas de mobilite pur, et il ne doit pas
        se diluer dans une mediane de rapports conserves.
        """
        return len(self.episodes) - len(self.reached)

    @property
    def kept_ratio(self) -> float | None:
        """Mediane de la part du rapport conservee jusqu'au contact."""
        parts = [item.kept for item in self.reached if item.kept is not None]
        return statistics.median(parts) if parts else None

    @property
    def median_delay(self) -> float | None:
        delais = [
            item.first_contact_delay
            for item in self.reached
            if item.first_contact_delay is not None
        ]
        return statistics.median(delais) if delais else None

    @property
    def collapses(self) -> bool:
        """L'avantage se volatilise-t-il pendant l'approche ?"""
        garde = self.kept_ratio
        return garde is not None and garde <= 1.0 - COLLAPSE_SHARE

    def render(self) -> str:
        if not self.episodes:
            return (
                "  Aucun assaut : la primitive de concentration ne s'est jamais\n"
                "  declenchee sur cette bataille."
            )
        lignes = [f"  assauts choisis          : {len(self.episodes)}"]
        lignes.append(f"  jamais arrives au contact: {self.never_reached}")
        if self.median_delay is not None:
            lignes.append(f"  delai median jusqu'au contact : {self.median_delay:.1f} s")
        garde = self.kept_ratio
        if garde is not None:
            lignes.append(f"  **rapport conserve (mediane)** : {garde:.0%}")
        lignes.append("")
        lignes += [item.explain() for item in self.episodes]

        if not self.reached:
            # **Ce cas rendait le verdict oppose aux donnees.** Sans contact,
            # `kept_ratio` vaut `None`, `collapses` vaut faux, et la lecture
            # tombait dans « l'avantage tient jusqu'au contact » alors qu'aucun
            # contact n'avait eu lieu. Un instrument qui conclut sur une case
            # vide est exactement ce que cette mesure existe pour eviter.
            lignes += [
                "",
                "**Aucun assaut n'a atteint son secteur.** Ce n'est ni un probleme",
                "  de mobilite ni un probleme de conversion : la manoeuvre est",
                "  choisie puis abandonnee avant d'aboutir. Chercher pourquoi elle",
                "  est relachee, avant de toucher a la vitesse ou au ciblage.",
            ]
        elif not self.measured:
            lignes += ["", "  Trop peu d'assauts pour conclure."]
        elif self.collapses:
            lignes += [
                "",
                "**L'avantage se volatilise pendant l'approche.** L'adversaire se",
                "  ressoude avant notre arrivee : c'est un probleme de mobilite, et",
                "  le correctif porte sur le cout d'affectation, pas sur le ciblage.",
            ]
        else:
            lignes += [
                "",
                "  L'avantage tient jusqu'au contact. Si le secteur ne rompt pas,",
                "  la cause est en aval — ciblage, concentration du feu, achevement —",
                "  et **pas** la vitesse d'arrivee.",
            ]
        return "\n".join(lignes)


def study(events: Iterable[Event]) -> Conversion:
    """Reconstitue les episodes d'assaut a partir des plans journalises.

    Aucun evenement dedie n'est necessaire : `plan_selected` publie deja le plan
    complet a chaque recalcul, et le plan porte desormais le rapport vivant et le
    nombre d'assaillants au contact. Les phases se lisent donc dans la serie.
    """
    episodes: list[Episode] = []
    courant: dict[str, object] | None = None
    cle_precedente: tuple[int, tuple[str, ...]] | None = None

    for event in events:
        if event.type is not EventType.PLAN_SELECTED:
            continue
        assaut = event.payload.get("assault")
        if not isinstance(assaut, dict):
            if courant is not None:
                episodes.append(_close(courant))
                courant = None
            cle_precedente = None
            continue

        cle = (int(assaut.get("sector", -1)), tuple(assaut.get("targets") or ()))
        if cle != cle_precedente:
            if courant is not None:
                episodes.append(_close(courant))
            courant = {
                "sector": cle[0],
                "attackers": len(assaut.get("attackers") or ()),
                "selected": float(assaut.get("ratio", 0.0)),
                "started_at": float(assaut.get("started_at", 0.0)),
            }
            cle_precedente = cle

        if courant is None:
            continue
        contact = int(event.payload.get("assault_contact", 0) or 0)
        ratio = float(event.payload.get("assault_ratio_now", 0.0) or 0.0)
        instant = float(event.game_time)
        if contact >= 1 and "first_contact" not in courant:
            courant["first_contact"] = ratio
            courant["first_contact_delay"] = instant - float(courant["started_at"])  # type: ignore[arg-type]
        besoin = max(1, int(int(courant["attackers"]) * MAIN_CONTACT_SHARE))  # type: ignore[call-overload]
        if contact >= besoin and "main_contact" not in courant:
            courant["main_contact"] = ratio
            courant["main_contact_delay"] = instant - float(courant["started_at"])  # type: ignore[arg-type]

    if courant is not None:
        episodes.append(_close(courant))
    return Conversion(episodes=episodes)


def _close(brut: dict[str, object]) -> Episode:
    return Episode(
        sector=int(brut["sector"]),  # type: ignore[call-overload]
        attackers=int(brut["attackers"]),  # type: ignore[call-overload]
        selected=float(brut["selected"]),  # type: ignore[arg-type]
        started_at=float(brut["started_at"]),  # type: ignore[arg-type]
        first_contact=_optional(brut.get("first_contact")),
        first_contact_delay=_optional(brut.get("first_contact_delay")),
        main_contact=_optional(brut.get("main_contact")),
        main_contact_delay=_optional(brut.get("main_contact_delay")),
    )


def _optional(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def summarise(battles: Sequence[Sequence[Event]]) -> Conversion:
    """Agrege plusieurs batailles en une seule lecture."""
    tous: list[Episode] = []
    for evenements in battles:
        tous += study(evenements).episodes
    return Conversion(episodes=tous)
