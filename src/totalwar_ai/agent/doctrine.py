"""Application d'une doctrine apprise aux reglages de l'agent.

Sens de la dependance : `agent` connait `learning`, jamais l'inverse.
:mod:`totalwar_ai.learning.adaptation` produit des chiffres et des raisons sans
rien savoir de la facon dont l'agent est construit ; c'est ici qu'ils
deviennent des reglages concrets.
"""

from __future__ import annotations

from totalwar_ai.agent.planner import PlannerSettings
from totalwar_ai.agent.safety_rules import SafetySettings
from totalwar_ai.learning.adaptation import ADJUSTABLES, DoctrineProfile


def baseline_values(settings: PlannerSettings) -> dict[str, float]:
    """Valeurs actuelles des reglages ajustables, pour alimenter l'analyse."""
    return {name: float(getattr(settings, name)) for name in ADJUSTABLES}


def apply_to_planner(settings: PlannerSettings, profile: DoctrineProfile) -> PlannerSettings:
    """Doctrine du planificateur, ajustee par le profil."""
    if profile.is_empty:
        return settings
    return PlannerSettings(
        line_spacing=_value(profile, "line_spacing", settings.line_spacing),
        missile_offset=settings.missile_offset,
        artillery_offset=settings.artillery_offset,
        command_offset=settings.command_offset,
        reserve_offset=settings.reserve_offset,
        cavalry_wing_offset=settings.cavalry_wing_offset,
        engagement_distance=_value(profile, "engagement_distance", settings.engagement_distance),
        ranged_threat_radius=_value(profile, "ranged_threat_radius", settings.ranged_threat_radius),
        pursuit_power_ratio=_value(profile, "pursuit_power_ratio", settings.pursuit_power_ratio),
        reserve_units=int(_value(profile, "reserve_units", float(settings.reserve_units))),
        min_line_for_reserve=settings.min_line_for_reserve,
    )


def apply_to_safety(settings: SafetySettings, profile: DoctrineProfile) -> SafetySettings:
    """Reglages de securite, ajustes par le profil.

    Un seul reglage bouge, et uniquement dans le sens de la prudence : le rayon
    de menace au-dela duquel un tireur se replie. Les interdits eux-memes
    (artillerie qui charge, seigneur sacrifie, charge suicidaire) restent hors
    de portee de l'apprentissage.
    """
    if "ranged_threat_radius" not in profile.adjustments:
        return settings
    radius = max(
        settings.ranged_threat_radius,
        _value(profile, "ranged_threat_radius", settings.ranged_threat_radius),
    )
    return SafetySettings(
        max_orders_per_minute=settings.max_orders_per_minute,
        protect_lord=settings.protect_lord,
        prevent_ranged_melee=settings.prevent_ranged_melee,
        prevent_artillery_charge=settings.prevent_artillery_charge,
        ranged_threat_radius=radius,
        min_local_power_ratio_for_charge=settings.min_local_power_ratio_for_charge,
        lord_retreat_health_ratio=settings.lord_retreat_health_ratio,
        max_pursuit_distance=settings.max_pursuit_distance,
        local_power_radius=settings.local_power_radius,
    )


def _value(profile: DoctrineProfile, name: str, fallback: float) -> float:
    return profile.adjustments.get(name, fallback)
