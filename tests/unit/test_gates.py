"""Les contrats de validation, qui decident de ce que le projet garde.

Une porte definie apres l'experience ne definit rien : l'ambiguite se resout
toujours dans le sens qui arrange le resultat. Ces tests figent le contrat.
"""

from __future__ import annotations

from totalwar_ai.learning.evaluation import (
    GATE_B_MINIMUM_WIN_RATE,
    HIDDEN_SEED_POOLS,
    BenchmarkReport,
    ScenarioResult,
    gate_verdict,
)


def _banc(*couples: tuple[str, float]) -> BenchmarkReport:
    return BenchmarkReport(
        scenarios=tuple(
            ScenarioResult(scenario=nom, battles=3, win_rate=taux) for nom, taux in couples
        )
    )


def test_une_famille_sans_victoire_bloque_la_porte() -> None:
    """C'est le contrat que l'ADR 0016 melangeait.

    Il exigeait « aucun scenario a 0 % » **et** admettait qu'un nul contre un
    ennemi passif ne soit pas un echec. Les deux ne tiennent pas ensemble. Le
    critere retenu est le plus strict : une superiorite locale mesuree valide la
    primitive qui la produit, jamais l'issue de la bataille.
    """
    verdict = gate_verdict(_banc(("facile", 1.0), ("dur", 0.0)), name="GATE A")
    assert not verdict.passed
    assert verdict.winless == ("dur",)


def test_une_moyenne_flatteuse_ne_rachete_pas_une_famille_a_zero() -> None:
    """Neuf familles a 100 % et une a zero font 90 % de moyenne.

    C'est exactement le chiffre que la Gate B demande, et c'est pourquoi la
    Gate A n'impose aucune moyenne : elle exige une victoire **partout**.
    """
    banc = _banc(*[(f"s{index}", 1.0) for index in range(9)], ("dur", 0.0))
    assert banc.win_rate >= GATE_B_MINIMUM_WIN_RATE
    assert not gate_verdict(banc, name="GATE A").passed


def test_la_porte_est_franchie_quand_chaque_famille_gagne() -> None:
    verdict = gate_verdict(_banc(("facile", 1.0), ("dur", 0.34)), name="GATE A")
    assert verdict.passed
    assert not verdict.winless


def test_un_scenario_sans_bataille_n_est_pas_compte_comme_perdu() -> None:
    """Un scenario filtre par `--scenario` ne doit pas faire echouer la porte.

    Il est absent, pas perdu — et c'est `missing` qui porte ce cas, alimente par
    la comparaison a la reference.
    """
    banc = BenchmarkReport(scenarios=(ScenarioResult(scenario="jamais_joue", battles=0),))
    assert gate_verdict(banc, name="GATE A").passed


def test_la_gate_b_exige_aussi_une_moyenne() -> None:
    banc = _banc(("a", 1.0), ("b", 0.5))
    assert gate_verdict(banc, name="GATE B", minimum_win_rate=GATE_B_MINIMUM_WIN_RATE).passed is (
        banc.win_rate >= GATE_B_MINIMUM_WIN_RATE
    )
    assert not gate_verdict(banc, name="GATE B", minimum_win_rate=0.90).passed


def test_les_pools_reserves_sont_distincts_et_hors_de_portee() -> None:
    """Trois pools, sans recouvrement, et hors de ce que `--seeds N` peut atteindre.

    `bench --seeds N` prolonge la serie par 101, 102, … — plage deja brulee par
    l'ADR 0013. Un pool de controle qui la croiserait ne serait pas un controle.
    """
    assert len(HIDDEN_SEED_POOLS) >= 3
    vues: set[int] = set()
    for pool in HIDDEN_SEED_POOLS:
        assert len(pool) >= 12
        assert not vues & set(pool), "deux pools partagent une graine"
        assert min(pool) > 1000, "un pool croise la plage atteignable par --seeds"
        vues |= set(pool)
