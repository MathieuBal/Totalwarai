"""Banc de scenarios : reproductibilite et detection de regressions."""

from __future__ import annotations

import pytest

from totalwar_ai.config import AppConfig
from totalwar_ai.learning.evaluation import (
    BenchmarkReport,
    ScenarioResult,
    compare,
    render_table,
    run_benchmark,
)
from totalwar_ai.simulation.scenarios import ScenarioCatalog, get_scenario

#: Scenarios courts, pour que la suite de tests reste rapide.
RAPIDES = ("cavalry_flank_threat", "rout_pursuit")


def _bench(config: AppConfig, **kwargs: object) -> BenchmarkReport:
    scenarios = [get_scenario(name) for name in RAPIDES]
    return run_benchmark(scenarios, seeds=(11, 23), config=config, **kwargs)  # type: ignore[arg-type]


def test_le_banc_est_reproductible(config: AppConfig) -> None:
    """Sans memoire, deux executions du meme code donnent les memes chiffres."""
    first = _bench(config)
    second = _bench(config)
    assert [entry.to_dict() for entry in first.scenarios] == [
        entry.to_dict() for entry in second.scenarios
    ]


def test_le_banc_couvre_les_scenarios_demandes(config: AppConfig) -> None:
    report = _bench(config)
    assert {entry.scenario for entry in report.scenarios} == set(RAPIDES)
    assert report.battles == len(RAPIDES) * 2
    for entry in report.scenarios:
        assert entry.battles == 2
        assert sum(entry.outcomes.values()) == 2


def test_les_dix_scenarios_de_reference_existent() -> None:
    """Le README liste dix situations pour le banc : elles doivent toutes exister."""
    assert len(ScenarioCatalog().names()) >= 10


def test_le_banc_complet_ne_regresse_pas(config: AppConfig) -> None:
    """Garde-fou global : l'agent doit rester au-dessus du niveau attendu."""
    report = run_benchmark(seeds=(11,), config=config)
    assert report.win_rate >= 0.7
    assert report.average_ally_remaining >= 0.5
    assert report.lord_survival == pytest.approx(1.0)


def test_comparaison_sans_ecart(config: AppConfig) -> None:
    report = _bench(config)
    comparison = compare(report, report)
    assert comparison.acceptable
    assert comparison.regressions == ()
    assert "aucune regression" in comparison.summary_line()


def test_comparaison_detecte_une_regression() -> None:
    baseline = BenchmarkReport(
        scenarios=(
            ScenarioResult(scenario="a", battles=3, win_rate=1.0, average_ally_remaining=0.8),
        )
    )
    candidate = BenchmarkReport(
        scenarios=(
            ScenarioResult(scenario="a", battles=3, win_rate=0.33, average_ally_remaining=0.4),
        )
    )
    comparison = compare(baseline, candidate)
    assert not comparison.acceptable
    metrics = {change.metric for change in comparison.regressions}
    assert metrics == {"taux de victoire", "forces restantes"}


def test_comparaison_detecte_une_amelioration() -> None:
    baseline = BenchmarkReport(
        scenarios=(ScenarioResult(scenario="a", win_rate=0.3, average_ally_remaining=0.3),)
    )
    candidate = BenchmarkReport(
        scenarios=(ScenarioResult(scenario="a", win_rate=1.0, average_ally_remaining=0.9),)
    )
    comparison = compare(baseline, candidate)
    assert comparison.acceptable
    assert len(comparison.improvements) == 2


def test_une_amelioration_globale_ne_rachete_pas_un_scenario_effondre() -> None:
    """Exigence du README : aucun scenario critique ne doit regresser."""
    baseline = BenchmarkReport(
        scenarios=(
            ScenarioResult(scenario="a", win_rate=1.0, average_ally_remaining=0.8),
            ScenarioResult(scenario="b", win_rate=0.0, average_ally_remaining=0.2),
        )
    )
    candidate = BenchmarkReport(
        scenarios=(
            # « a » s'effondre, « b » progresse davantage : la moyenne monte.
            ScenarioResult(scenario="a", win_rate=0.5, average_ally_remaining=0.5),
            ScenarioResult(scenario="b", win_rate=1.0, average_ally_remaining=0.9),
        )
    )
    comparison = compare(baseline, candidate)
    assert comparison.win_rate_after > comparison.win_rate_before  # meilleur en moyenne
    assert not comparison.acceptable  # mais un scenario s'est effondre


def test_la_survie_du_seigneur_ne_tolere_aucune_baisse() -> None:
    baseline = BenchmarkReport(scenarios=(ScenarioResult(scenario="a", lord_survival=1.0),))
    candidate = BenchmarkReport(scenarios=(ScenarioResult(scenario="a", lord_survival=0.9),))
    comparison = compare(baseline, candidate)
    assert not comparison.acceptable
    assert comparison.regressions[0].metric == "survie du seigneur"


def test_scenario_manquant_est_signale(config: AppConfig) -> None:
    baseline = BenchmarkReport(scenarios=(ScenarioResult(scenario="disparu", win_rate=1.0),))
    comparison = compare(baseline, _bench(config))
    assert comparison.missing_scenarios == ("disparu",)
    # **Le signaler ne suffisait pas.** `missing_scenarios` etait calcule et
    # jamais consulte par le verdict : c'est ce que la ligne suivante pince.
    assert not comparison.acceptable


def test_un_candidat_ampute_est_refuse() -> None:
    """**Un scenario absent n'est pas un scenario reussi.**

    Supprimer par megarde le scenario le plus dur rendait le candidat
    « acceptable » : il ne restait plus rien pour y regresser, et le banc — le
    mecanisme qui decide de ce que le projet garde — validait l'amputation.
    """
    baseline = BenchmarkReport(
        scenarios=(
            ScenarioResult(scenario="facile", win_rate=1.0, average_ally_remaining=0.9),
            ScenarioResult(scenario="difficile", win_rate=0.3, average_ally_remaining=0.4),
        )
    )
    # Le scenario difficile a disparu ; le reste est intact, voire meilleur.
    candidate = BenchmarkReport(
        scenarios=(ScenarioResult(scenario="facile", win_rate=1.0, average_ally_remaining=0.95),)
    )
    comparison = compare(baseline, candidate)

    assert comparison.regressions == (), "le test ne prouverait rien avec une regression"
    assert comparison.missing_scenarios == ("difficile",)
    assert not comparison.acceptable
    assert "manquant" in comparison.summary_line()


def test_aller_retour_du_rapport(config: AppConfig) -> None:
    report = _bench(config, label="test")
    restored = BenchmarkReport.from_dict(report.to_dict())
    assert restored.label == "test"
    assert restored.seeds == report.seeds
    assert [entry.to_dict() for entry in restored.scenarios] == [
        entry.to_dict() for entry in report.scenarios
    ]


def test_rendu_lisible(config: AppConfig) -> None:
    table = render_table(_bench(config))
    assert "scenario" in table
    assert "ensemble" in table
    for name in RAPIDES:
        assert name in table


def test_debit_d_ordres_n_est_pas_extrapole(config: AppConfig) -> None:
    """Une bataille de dix secondes ne doit pas afficher un debit d'une minute."""
    report = _bench(config)
    pursuit = report.result("rout_pursuit")
    assert pursuit is not None
    assert pursuit.average_duration < 60.0
    assert pursuit.orders_per_minute <= 90.0
