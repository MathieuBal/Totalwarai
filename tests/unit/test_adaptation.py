"""Adaptation de la doctrine : deduction, bornes et application."""

from __future__ import annotations

from pathlib import Path

import pytest

from totalwar_ai.agent.doctrine import apply_to_planner, apply_to_safety, baseline_values
from totalwar_ai.agent.planner import PlannerSettings
from totalwar_ai.agent.safety_rules import SafetySettings
from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.learning.adaptation import (
    ADJUSTABLES,
    DoctrineProfile,
    HistoryStats,
    derive_profile,
)
from totalwar_ai.learning.checkpoints import CheckpointStore, checkpoint_name
from totalwar_ai.memory.models import BattleSummary

FINGERPRINT = "ally:melee_infantryx3|enemy:melee_infantryx5"


def _battle(
    outcome: BattleOutcomeKind,
    *,
    ally_remaining: float = 0.5,
    routs: int = 0,
    ranged_engaged: int = 0,
    index: int = 0,
) -> BattleSummary:
    return BattleSummary(
        battle_id=f"b{index}",
        scenario="test",
        outcome=outcome,
        ally_remaining=ally_remaining,
        army_fingerprint=FINGERPRINT,
        metrics={
            "allied_routs": routs,
            "allied_ranged_engaged": ranged_engaged,
            "allied_units_lost": 1,
        },
    )


def test_historique_trop_maigre_ne_change_rien() -> None:
    profile = derive_profile(FINGERPRINT, [_battle(BattleOutcomeKind.DEFEAT)])
    assert profile.is_empty
    assert profile.stats.sample_size == 1


def test_defaites_repetees_rendent_prudent() -> None:
    history = [_battle(BattleOutcomeKind.DEFEAT, index=index) for index in range(3)]
    profile = derive_profile(FINGERPRINT, history)
    baseline = PlannerSettings()
    assert profile.adjustments["engagement_distance"] < baseline.engagement_distance
    assert profile.adjustments["reserve_units"] > baseline.reserve_units
    assert any("taux de victoire" in reason for reason in profile.rationale)


def test_victoires_nettes_rendent_mordant() -> None:
    history = [
        _battle(BattleOutcomeKind.VICTORY, ally_remaining=0.9, index=index) for index in range(4)
    ]
    profile = derive_profile(FINGERPRINT, history)
    baseline = PlannerSettings()
    assert profile.adjustments["engagement_distance"] > baseline.engagement_distance
    assert profile.adjustments["pursuit_power_ratio"] < baseline.pursuit_power_ratio


def test_tireurs_pris_en_melee_augmentent_le_rayon_de_menace() -> None:
    history = [
        _battle(BattleOutcomeKind.VICTORY, ally_remaining=0.6, ranged_engaged=2, index=index)
        for index in range(2)
    ]
    profile = derive_profile(FINGERPRINT, history)
    assert profile.adjustments["ranged_threat_radius"] > PlannerSettings().ranged_threat_radius
    assert any("tireurs pris en melee" in reason for reason in profile.rationale)


def test_deroutes_repetees_resserrent_la_ligne() -> None:
    history = [
        _battle(BattleOutcomeKind.VICTORY, ally_remaining=0.5, routs=2, index=index)
        for index in range(2)
    ]
    profile = derive_profile(FINGERPRINT, history)
    assert profile.adjustments["line_spacing"] < PlannerSettings().line_spacing


def test_ajustements_toujours_dans_les_bornes() -> None:
    """Meme un historique caricatural ne doit pas produire de doctrine absurde."""
    history = [
        _battle(BattleOutcomeKind.DEFEAT, ally_remaining=0.0, routs=50, ranged_engaged=50, index=i)
        for i in range(30)
    ]
    profile = derive_profile(FINGERPRINT, history)
    # On applique plusieurs fois de suite : la derive doit s'arreter aux bornes.
    settings = PlannerSettings()
    for _ in range(10):
        profile = derive_profile(FINGERPRINT, history, baseline=baseline_values(settings))
        settings = apply_to_planner(settings, profile)
    for name, adjustable in ADJUSTABLES.items():
        value = float(getattr(settings, name))
        assert adjustable.minimum <= value <= adjustable.maximum, name


def test_application_au_planificateur() -> None:
    profile = DoctrineProfile(
        fingerprint=FINGERPRINT,
        adjustments={"engagement_distance": 40.0, "reserve_units": 2.0},
        rationale=("test",),
    )
    settings = apply_to_planner(PlannerSettings(), profile)
    assert settings.engagement_distance == pytest.approx(40.0)
    assert settings.reserve_units == 2
    # Les reglages non concernes ne bougent pas.
    assert settings.missile_offset == PlannerSettings().missile_offset


def test_profil_vide_ne_touche_a_rien() -> None:
    settings = PlannerSettings()
    assert apply_to_planner(settings, DoctrineProfile()) is settings
    safety = SafetySettings()
    assert apply_to_safety(safety, DoctrineProfile()) is safety


def test_la_securite_ne_peut_qu_etre_renforcee() -> None:
    """L'apprentissage n'a pas le droit d'assouplir un garde-fou."""
    laxiste = DoctrineProfile(
        fingerprint=FINGERPRINT,
        adjustments={"ranged_threat_radius": 50.0},
        rationale=("test",),
    )
    settings = SafetySettings(ranged_threat_radius=70.0)
    assert apply_to_safety(settings, laxiste).ranged_threat_radius == pytest.approx(70.0)

    prudent = DoctrineProfile(
        fingerprint=FINGERPRINT,
        adjustments={"ranged_threat_radius": 95.0},
        rationale=("test",),
    )
    assert apply_to_safety(settings, prudent).ranged_threat_radius == pytest.approx(95.0)


def test_les_interdits_restent_hors_de_portee() -> None:
    """Aucun reglage booleen de securite n'est ajustable."""
    interdits = {
        "protect_lord",
        "prevent_ranged_melee",
        "prevent_artillery_charge",
        "min_local_power_ratio_for_charge",
        "lord_retreat_health_ratio",
        "max_orders_per_minute",
    }
    assert interdits.isdisjoint(ADJUSTABLES)


def test_statistiques_d_historique() -> None:
    history = [
        _battle(BattleOutcomeKind.VICTORY, ally_remaining=0.8, index=0),
        _battle(BattleOutcomeKind.DEFEAT, ally_remaining=0.2, routs=2, index=1),
    ]
    stats = HistoryStats.from_battles(history)
    assert stats.sample_size == 2
    assert stats.win_rate == pytest.approx(0.5)
    assert stats.average_ally_remaining == pytest.approx(0.5)
    assert stats.average_allied_routs == pytest.approx(1.0)


def test_checkpoint_aller_retour(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path)
    profile = derive_profile(
        FINGERPRINT, [_battle(BattleOutcomeKind.DEFEAT, index=i) for i in range(3)]
    )
    path = store.save(profile)
    assert path.exists()

    restored = store.load(FINGERPRINT)
    assert restored is not None
    assert restored.adjustments == profile.adjustments
    assert restored.rationale == profile.rationale
    assert restored.stats.sample_size == profile.stats.sample_size


def test_checkpoint_absent(tmp_path: Path) -> None:
    assert CheckpointStore(tmp_path).load("inconnu") is None


def test_checkpoint_corrompu_est_ignore(tmp_path: Path) -> None:
    """Une doctrine illisible ne doit pas empecher de jouer."""
    store = CheckpointStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    store.path_for(FINGERPRINT).write_text("{ ceci n'est pas du JSON", encoding="utf-8")
    assert store.load(FINGERPRINT) is None
    assert list(store.all_profiles()) == []


def test_reglage_inconnu_ignore_a_la_relecture() -> None:
    """Un checkpoint ecrit par une version plus recente reste exploitable."""
    profile = DoctrineProfile.from_dict(
        {
            "fingerprint": FINGERPRINT,
            "adjustments": {"engagement_distance": 42.0, "telepathie": 3.0},
            "rationale": ["test"],
        }
    )
    assert profile.adjustments == {"engagement_distance": 42.0}


def test_valeur_hors_bornes_est_ramenee_a_la_relecture() -> None:
    profile = DoctrineProfile.from_dict(
        {"fingerprint": FINGERPRINT, "adjustments": {"engagement_distance": 5000.0}}
    )
    assert profile.adjustments["engagement_distance"] == ADJUSTABLES["engagement_distance"].maximum


def test_nom_de_checkpoint_stable_et_lisible() -> None:
    name = checkpoint_name(FINGERPRINT)
    assert name.startswith("doctrine-")
    assert name.endswith(".json")
    assert name == checkpoint_name(FINGERPRINT)
    assert checkpoint_name("autre") != name
