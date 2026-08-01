"""Adaptation de la doctrine a partir de l'historique.

C'est le livrable de la Phase 4 du README : « comportement influence par les
batailles precedentes ». Aucun reseau de neurones — l'agent regarde comment se
sont terminees les batailles de composition comparable et ajuste quelques
reglages, dans des bornes fixes, avec une justification lisible.

Trois garde-fous, dans l'esprit du README :

* seuls des reglages **tunables** bougent (distances, taille de reserve) ; les
  regles de securite ne sont jamais assouplies par l'apprentissage ;
* chaque ajustement est **borne** : meme un historique aberrant ne peut pas
  produire une doctrine absurde ;
* chaque ajustement porte une **raison en clair**, reprise dans le rapport.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.memory.models import BattleSummary

#: Version du format de profil, enregistree dans les checkpoints.
PROFILE_VERSION = "0.1.0"

#: Nombre minimal de batailles comparables avant d'oser ajuster quoi que ce soit.
DEFAULT_MIN_SAMPLES = 2


@dataclass(frozen=True, slots=True)
class Adjustable:
    """Reglage modifiable par l'apprentissage, avec ses bornes."""

    name: str
    minimum: float
    maximum: float
    integer: bool = False

    def clamp(self, value: float) -> float:
        bounded = max(self.minimum, min(self.maximum, value))
        return float(round(bounded)) if self.integer else bounded


#: Seuls ces reglages peuvent bouger. Tout le reste est hors de portee de
#: l'adaptation — en particulier les interdits de `safety_rules`.
ADJUSTABLES: dict[str, Adjustable] = {
    "ranged_threat_radius": Adjustable("ranged_threat_radius", 50.0, 110.0),
    "engagement_distance": Adjustable("engagement_distance", 35.0, 80.0),
    "reserve_units": Adjustable("reserve_units", 0.0, 3.0, integer=True),
    "pursuit_power_ratio": Adjustable("pursuit_power_ratio", 1.2, 2.5),
    "line_spacing": Adjustable("line_spacing", 30.0, 60.0),
}


@dataclass(frozen=True, slots=True)
class HistoryStats:
    """Ce que l'historique dit d'une composition donnee."""

    sample_size: int = 0
    win_rate: float = 0.0
    average_ally_remaining: float = 0.0
    average_allied_routs: float = 0.0
    average_ranged_engaged: float = 0.0
    average_allied_units_lost: float = 0.0

    @classmethod
    def from_battles(cls, battles: Sequence[BattleSummary]) -> HistoryStats:
        if not battles:
            return cls()
        count = len(battles)
        victories = sum(1 for battle in battles if battle.outcome is BattleOutcomeKind.VICTORY)
        return cls(
            sample_size=count,
            win_rate=victories / count,
            average_ally_remaining=sum(battle.ally_remaining for battle in battles) / count,
            average_allied_routs=_average_metric(battles, "allied_routs"),
            average_ranged_engaged=_average_metric(battles, "allied_ranged_engaged"),
            average_allied_units_lost=_average_metric(battles, "allied_units_lost"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "win_rate": self.win_rate,
            "average_ally_remaining": self.average_ally_remaining,
            "average_allied_routs": self.average_allied_routs,
            "average_ranged_engaged": self.average_ranged_engaged,
            "average_allied_units_lost": self.average_allied_units_lost,
        }


def _average_metric(battles: Sequence[BattleSummary], key: str) -> float:
    values = [float(battle.metrics.get(key, 0.0) or 0.0) for battle in battles]
    return sum(values) / len(values) if values else 0.0


@dataclass(frozen=True, slots=True)
class DoctrineProfile:
    """Doctrine ajustee pour une composition d'armee donnee."""

    fingerprint: str = ""
    adjustments: dict[str, float] = field(default_factory=dict)
    rationale: tuple[str, ...] = ()
    stats: HistoryStats = field(default_factory=HistoryStats)
    version: str = PROFILE_VERSION
    updated_at: float = field(default_factory=time.time)

    @property
    def is_empty(self) -> bool:
        return not self.adjustments

    def describe(self) -> list[str]:
        """Lignes lisibles pour le rapport et le CLI."""
        if self.is_empty:
            return ["Aucun ajustement : historique insuffisant ou doctrine deja adaptee."]
        return list(self.rationale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fingerprint": self.fingerprint,
            "adjustments": dict(self.adjustments),
            "rationale": list(self.rationale),
            "stats": self.stats.to_dict(),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DoctrineProfile:
        """Relecture tolerante : un reglage inconnu d'une version future est ignore."""
        raw_adjustments = raw.get("adjustments") or {}
        adjustments: dict[str, float] = {}
        if isinstance(raw_adjustments, Mapping):
            for name, value in raw_adjustments.items():
                adjustable = ADJUSTABLES.get(str(name))
                if adjustable is None or not isinstance(value, int | float):
                    continue
                adjustments[str(name)] = adjustable.clamp(float(value))
        raw_stats = raw.get("stats") or {}
        stats = HistoryStats(
            sample_size=int(raw_stats.get("sample_size", 0) or 0),
            win_rate=float(raw_stats.get("win_rate", 0.0) or 0.0),
            average_ally_remaining=float(raw_stats.get("average_ally_remaining", 0.0) or 0.0),
            average_allied_routs=float(raw_stats.get("average_allied_routs", 0.0) or 0.0),
            average_ranged_engaged=float(raw_stats.get("average_ranged_engaged", 0.0) or 0.0),
            average_allied_units_lost=float(raw_stats.get("average_allied_units_lost", 0.0) or 0.0),
        )
        rationale = raw.get("rationale") or []
        return cls(
            fingerprint=str(raw.get("fingerprint", "")),
            adjustments=adjustments,
            rationale=tuple(str(item) for item in rationale),
            stats=stats,
            version=str(raw.get("version", PROFILE_VERSION)),
            updated_at=float(raw.get("updated_at", 0.0) or 0.0),
        )


#: Valeurs de reference utilisees si l'appelant n'en fournit pas.
DEFAULT_BASELINE: dict[str, float] = {
    "ranged_threat_radius": 70.0,
    "engagement_distance": 55.0,
    "reserve_units": 1.0,
    "pursuit_power_ratio": 1.5,
    "line_spacing": 45.0,
}


def derive_profile(
    fingerprint: str,
    history: Sequence[BattleSummary],
    *,
    baseline: Mapping[str, float] | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> DoctrineProfile:
    """Deduit une doctrine ajustee de l'historique d'une composition.

    `baseline` donne les valeurs actuelles des reglages ajustables (voir
    :data:`ADJUSTABLES`). Ce module ignore volontairement comment l'agent est
    construit : il produit des chiffres et des raisons, l'agent decide quoi en
    faire.

    Renvoie un profil vide tant que l'historique est trop maigre : mieux vaut la
    doctrine par defaut qu'une conclusion tiree d'une seule bataille.
    """
    stats = HistoryStats.from_battles(history)
    reference = {**DEFAULT_BASELINE, **(baseline or {})}
    if stats.sample_size < min_samples:
        return DoctrineProfile(fingerprint=fingerprint, stats=stats)

    adjustments: dict[str, float] = {}
    rationale: list[str] = []

    def adjust(name: str, value: float, reason: str) -> None:
        adjustable = ADJUSTABLES[name]
        bounded = adjustable.clamp(value)
        current = reference[name]
        if abs(bounded - float(current)) < 1e-6:
            return  # deja a la bonne valeur, ou bloque par une borne
        adjustments[name] = bounded
        rationale.append(reason)

    # Des tireurs pris au corps a corps signifient qu'on les replie trop tard.
    if stats.average_ranged_engaged >= 0.5:
        before = reference["ranged_threat_radius"]
        after = ADJUSTABLES["ranged_threat_radius"].clamp(before * 1.25)
        adjust(
            "ranged_threat_radius",
            after,
            f"tireurs pris en melee dans l'historique ({stats.average_ranged_engaged:.1f} par "
            f"bataille) : replier plus tot ({before:.0f} m -> {after:.0f} m)",
        )

    if stats.win_rate < 0.5:
        # Trop de defaites : engager plus tard et garder plus de monde en reserve.
        adjust(
            "engagement_distance",
            reference["engagement_distance"] * 0.8,
            f"taux de victoire faible ({stats.win_rate:.0%} sur {stats.sample_size} batailles) : "
            "laisser l'ennemi venir plus pres avant d'engager",
        )
        adjust(
            "reserve_units",
            reference["reserve_units"] + 1,
            "taux de victoire faible : conserver une reserve plus fournie",
        )
    elif stats.win_rate >= 0.99 and stats.average_ally_remaining >= 0.7:
        # Victoires nettes et peu couteuses : on peut se permettre d'etre plus mordant.
        adjust(
            "engagement_distance",
            reference["engagement_distance"] * 1.15,
            f"victoires nettes ({stats.average_ally_remaining:.0%} de forces restantes en "
            "moyenne) : engager plus tot",
        )
        adjust(
            "pursuit_power_ratio",
            reference["pursuit_power_ratio"] - 0.2,
            "victoires nettes : poursuivre les fuyards avec un avantage moindre",
        )

    # Des deroutes repetees trahissent une ligne trop etiree.
    if stats.average_allied_routs >= 1.0:
        adjust(
            "line_spacing",
            reference["line_spacing"] * 0.85,
            f"deroutes repetees ({stats.average_allied_routs:.1f} par bataille) : "
            "resserrer la ligne pour que les unites se soutiennent",
        )

    return DoctrineProfile(
        fingerprint=fingerprint,
        adjustments=adjustments,
        rationale=tuple(rationale),
        stats=stats,
    )
