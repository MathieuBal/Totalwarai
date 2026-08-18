"""Banc de scenarios et detection de regressions.

Section « Evaluation et prevention des regressions » du README : le fait qu'un
agent ait gagne sa derniere bataille ne prouve rien. On rejoue donc le banc
complet, a graines fixes, et on compare a une reference enregistree.

Le banc tourne **sans memoire** : aucune doctrine apprise n'est appliquee, de
sorte que deux executions du meme code donnent exactement les memes chiffres.
C'est la condition pour qu'une difference soit imputable au changement de code
et a rien d'autre.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from totalwar_ai.config import AppConfig, load_config
from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.simulation.scenarios import Scenario, ScenarioCatalog

#: Graines par defaut du banc. Fixes : un banc qui bouge ne mesure rien.
DEFAULT_SEEDS: tuple[int, ...] = (11, 23, 37)


def widen_seeds(count: int) -> tuple[int, ...]:
    """Les `count` premieres graines du banc, elargies de facon deterministe.

    **Une seule regle d'elargissement, partagee.** Le banc et la sonde de
    secteurs doivent tirer les memes graines, sans quoi leurs chiffres ne se
    comparent pas — et deux regles de meme intention finissent toujours par
    deriver l'une de l'autre (ADR 0019).

    Les pools reserves (`9001+`, `9101+`, `9201+`) ne sont jamais atteints : ils
    ne se tirent que par `--hidden`, et jamais pendant le developpement.
    """
    if count <= len(DEFAULT_SEEDS):
        return tuple(DEFAULT_SEEDS[:count]) or DEFAULT_SEEDS
    return tuple(DEFAULT_SEEDS) + tuple(101 + index for index in range(count - len(DEFAULT_SEEDS)))


#: Graines **reservees**, jouees seulement au moment de valider une etape.
#:
#: .. warning::
#:
#:    **Ne jamais les jouer pendant le developpement.** Ni pour regler un seuil,
#:    ni pour diagnostiquer, ni « juste pour voir ». Une graine qui a servi a
#:    choisir n'est plus une graine de controle : elle mesure la recherche qu'on
#:    a faite dessus.
#:
#: L'ADR 0013 a coute la lecon : un ecart de +4 points mesure sur les graines de
#: reglage n'en valait plus qu'un seul sur des graines inedites. Les +4 etaient
#: du bruit qu'on avait appris par coeur.
#:
#: Volontairement hors de portee de `bench --seeds N`, qui prolonge la serie par
#: 101, 102, … — plage deja brulee par ce meme ADR. Elles ne sont atteignables
#: que par `bench --hidden`, et ce nom est la pour qu'on ne les tape pas par
#: distraction.
#:
#: .. rubric:: Une reserve lue est brulee
#:
#: **Un seul pool ne suffit pas, et c'est le piege suivant.** Le jour ou l'on
#: joue `B1`, ou l'on regarde le score, ou l'on corrige le code en consequence,
#: `B1` a servi a choisir : ce n'est plus un jeu de controle, c'est devenu un jeu
#: de developpement, et le rejouer ne mesurerait que la correction qu'il a lui-
#: meme inspiree. D'ou trois pools, consommes dans l'ordre — chacun ne juge
#: qu'une fois.
HIDDEN_SEED_POOLS: tuple[tuple[int, ...], ...] = (
    tuple(9001 + index for index in range(12)),
    tuple(9101 + index for index in range(12)),
    tuple(9201 + index for index in range(12)),
)

#: Pool de controle courant. Les precedents ont ete lus, donc brules.
HIDDEN_SEEDS: tuple[int, ...] = HIDDEN_SEED_POOLS[0]

#: Taux de victoire minimal exige par la Gate B, sur graines reservees.
#:
#: La Gate A n'en impose aucun : elle exige a la place **une victoire par
#: famille**, ce qui est plus strict qu'une moyenne. Une moyenne de 90 %
#: s'obtient tres bien avec une famille a zero.
GATE_B_MINIMUM_WIN_RATE = 0.90

#: Baisse maximale toleree avant de parler de regression.
DEFAULT_WIN_RATE_TOLERANCE = 0.10
DEFAULT_STRENGTH_TOLERANCE = 0.10

BENCHMARK_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Resultats agreges d'un scenario sur plusieurs graines."""

    scenario: str
    battles: int = 0
    win_rate: float = 0.0
    average_ally_remaining: float = 0.0
    average_enemy_remaining: float = 0.0
    average_duration: float = 0.0
    average_reward: float = 0.0
    orders_per_minute: float = 0.0
    blocked_ratio: float = 0.0
    allied_units_lost: float = 0.0
    lord_survival: float = 1.0
    #: Ecart-type des forces restantes entre graines : mesure la stabilite.
    stability: float = 0.0
    outcomes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "battles": self.battles,
            "win_rate": self.win_rate,
            "average_ally_remaining": self.average_ally_remaining,
            "average_enemy_remaining": self.average_enemy_remaining,
            "average_duration": self.average_duration,
            "average_reward": self.average_reward,
            "orders_per_minute": self.orders_per_minute,
            "blocked_ratio": self.blocked_ratio,
            "allied_units_lost": self.allied_units_lost,
            "lord_survival": self.lord_survival,
            "stability": self.stability,
            "outcomes": dict(self.outcomes),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ScenarioResult:
        return cls(
            scenario=str(raw.get("scenario", "")),
            battles=int(raw.get("battles", 0) or 0),
            win_rate=float(raw.get("win_rate", 0.0) or 0.0),
            average_ally_remaining=float(raw.get("average_ally_remaining", 0.0) or 0.0),
            average_enemy_remaining=float(raw.get("average_enemy_remaining", 0.0) or 0.0),
            average_duration=float(raw.get("average_duration", 0.0) or 0.0),
            average_reward=float(raw.get("average_reward", 0.0) or 0.0),
            orders_per_minute=float(raw.get("orders_per_minute", 0.0) or 0.0),
            blocked_ratio=float(raw.get("blocked_ratio", 0.0) or 0.0),
            allied_units_lost=float(raw.get("allied_units_lost", 0.0) or 0.0),
            lord_survival=float(raw.get("lord_survival", 1.0) or 0.0),
            stability=float(raw.get("stability", 0.0) or 0.0),
            outcomes={str(key): int(value) for key, value in (raw.get("outcomes") or {}).items()},
        )


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """Photo complete du niveau de l'agent sur le banc."""

    scenarios: tuple[ScenarioResult, ...] = ()
    seeds: tuple[int, ...] = DEFAULT_SEEDS
    version: str = BENCHMARK_VERSION
    created_at: float = field(default_factory=time.time)
    label: str = ""

    def result(self, scenario: str) -> ScenarioResult | None:
        for entry in self.scenarios:
            if entry.scenario == scenario:
                return entry
        return None

    @property
    def battles(self) -> int:
        return sum(entry.battles for entry in self.scenarios)

    @property
    def win_rate(self) -> float:
        if not self.scenarios:
            return 0.0
        return sum(entry.win_rate for entry in self.scenarios) / len(self.scenarios)

    @property
    def average_ally_remaining(self) -> float:
        if not self.scenarios:
            return 0.0
        return sum(entry.average_ally_remaining for entry in self.scenarios) / len(self.scenarios)

    @property
    def average_reward(self) -> float:
        if not self.scenarios:
            return 0.0
        return sum(entry.average_reward for entry in self.scenarios) / len(self.scenarios)

    @property
    def lord_survival(self) -> float:
        if not self.scenarios:
            return 1.0
        return sum(entry.lord_survival for entry in self.scenarios) / len(self.scenarios)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "label": self.label,
            "seeds": list(self.seeds),
            "overall": {
                "battles": self.battles,
                "win_rate": self.win_rate,
                "average_ally_remaining": self.average_ally_remaining,
                "average_reward": self.average_reward,
                "lord_survival": self.lord_survival,
            },
            "scenarios": [entry.to_dict() for entry in self.scenarios],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BenchmarkReport:
        entries = raw.get("scenarios") or []
        return cls(
            scenarios=tuple(ScenarioResult.from_dict(item) for item in entries),
            seeds=tuple(int(seed) for seed in (raw.get("seeds") or DEFAULT_SEEDS)),
            version=str(raw.get("version", BENCHMARK_VERSION)),
            created_at=float(raw.get("created_at", 0.0) or 0.0),
            label=str(raw.get("label", "")),
        )


def run_benchmark(
    scenarios: Sequence[Scenario] | None = None,
    *,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    config: AppConfig | None = None,
    label: str = "",
    on_battle: Callable[[str, int], None] | None = None,
    battle_runner: Callable[..., Any] | None = None,
) -> BenchmarkReport:
    """Rejoue le banc et agrege les metriques.

    Import local de `run_battle` : `simulation` depend de `learning`, l'inverse
    ne doit exister qu'au moment de l'appel.

    `battle_runner` remplace la fonction qui joue une bataille. C'est ce qui
    permet de mesurer autre chose que notre agent — la doublure de l'IA du
    moteur, supervisee ou non — **avec la meme agregation et la meme detection
    de regression**. Deux chemins de mesure differents ne produiraient pas deux
    chiffres comparables.
    """
    from totalwar_ai.simulation.runner import run_battle

    jouer = battle_runner or run_battle
    resolved_config = config or load_config()
    catalog = list(scenarios) if scenarios is not None else ScenarioCatalog().all()
    results: list[ScenarioResult] = []

    for scenario in catalog:
        remaining: list[float] = []
        enemy_remaining: list[float] = []
        durations: list[float] = []
        rewards: list[float] = []
        orders: list[float] = []
        blocked: list[float] = []
        losses: list[float] = []
        lords: list[float] = []
        outcomes: dict[str, int] = {}

        for seed in seeds:
            if on_battle is not None:
                on_battle(scenario.name, seed)
            result = jouer(
                scenario,
                config=resolved_config,
                seed=seed,
                repository=None,  # banc sans memoire : chiffres reproductibles
                generate_report=False,
            )
            summary = result.summary
            metrics = summary.metrics
            remaining.append(summary.ally_remaining)
            enemy_remaining.append(summary.enemy_remaining)
            durations.append(summary.duration)
            rewards.append(summary.total_reward)
            orders.append(float(metrics.get("orders_per_minute", 0.0)))
            blocked.append(float(metrics.get("blocked_ratio", 0.0)))
            losses.append(float(metrics.get("allied_units_lost", 0.0)))
            lords.append(1.0 if metrics.get("lord_survived", True) else 0.0)
            key = summary.outcome.value
            outcomes[key] = outcomes.get(key, 0) + 1

        battles = len(seeds)
        results.append(
            ScenarioResult(
                scenario=scenario.name,
                battles=battles,
                win_rate=outcomes.get(BattleOutcomeKind.VICTORY.value, 0) / max(1, battles),
                average_ally_remaining=_mean(remaining),
                average_enemy_remaining=_mean(enemy_remaining),
                average_duration=_mean(durations),
                average_reward=_mean(rewards),
                orders_per_minute=_mean(orders),
                blocked_ratio=_mean(blocked),
                allied_units_lost=_mean(losses),
                lord_survival=_mean(lords),
                stability=statistics.pstdev(remaining) if len(remaining) > 1 else 0.0,
                outcomes=outcomes,
            )
        )

    return BenchmarkReport(scenarios=tuple(results), seeds=tuple(seeds), label=label)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass(frozen=True, slots=True)
class MetricChange:
    """Ecart d'une metrique entre reference et candidat."""

    scenario: str
    metric: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before

    def describe(self) -> str:
        return (
            f"{self.scenario} — {self.metric} : "
            f"{self.before:.0%} -> {self.after:.0%} ({self.delta:+.0%})"
        )


@dataclass(frozen=True, slots=True)
class Comparison:
    """Verdict d'une comparaison banc de reference / banc candidat."""

    regressions: tuple[MetricChange, ...] = ()
    improvements: tuple[MetricChange, ...] = ()
    win_rate_before: float = 0.0
    win_rate_after: float = 0.0
    strength_before: float = 0.0
    strength_after: float = 0.0
    missing_scenarios: tuple[str, ...] = ()

    @property
    def acceptable(self) -> bool:
        """Un candidat n'est acceptable que sans regression **et sans manque**.

        **Un scenario absent n'est pas un scenario reussi.** `missing_scenarios`
        etait calcule, stocke, et jamais consulte : supprimer par megarde le
        scenario le plus dur rendait le candidat « acceptable », puisqu'il ne
        restait plus rien pour y regresser.

        C'est le banc qui decide de ce que le projet garde. Une comparaison qui
        peut valider un candidat ampute ne decide de rien.
        """
        return not self.regressions and not self.missing_scenarios

    def summary_line(self) -> str:
        if self.regressions:
            verdict = f"{len(self.regressions)} regression(s)"
        elif self.missing_scenarios:
            verdict = f"{len(self.missing_scenarios)} scenario(s) manquant(s)"
        else:
            verdict = "aucune regression"
        return (
            f"taux de victoire {self.win_rate_before:.0%} -> {self.win_rate_after:.0%}, "
            f"forces restantes {self.strength_before:.0%} -> {self.strength_after:.0%} "
            f"({verdict})"
        )


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Verdict d'une porte de validation, rendu sans interpretation.

    **Une porte se definit avant l'experience, ou elle ne definit rien.** Cette
    classe existe parce que le contrat de la Gate A, tel qu'il avait ete ecrit
    dans l'ADR 0016, se contredisait : il exigeait « aucun scenario a 0 % » et
    admettait dans la meme phrase qu'un nul contre un ennemi passif ne soit pas
    un echec. Les deux ne tiennent pas ensemble, et l'ambiguite se serait
    resolue — apres coup — dans le sens qui arrangeait le resultat.

    Le critere retenu est le plus strict des deux : **chaque famille doit
    produire au moins une victoire**. Une superiorite locale mesuree valide la
    primitive qui la produit ; elle ne valide pas l'issue de la bataille.
    """

    name: str
    #: Familles n'ayant pas produit une seule victoire, toutes graines confondues.
    winless: tuple[str, ...] = ()
    #: Familles attendues et absentes du rapport.
    missing: tuple[str, ...] = ()
    #: Regressions relevees face a la reference, quand il y en a une.
    regressions: tuple[str, ...] = ()
    win_rate: float = 0.0
    minimum_win_rate: float = 0.0

    @property
    def passed(self) -> bool:
        return (
            not self.winless
            and not self.missing
            and not self.regressions
            and self.win_rate >= self.minimum_win_rate
        )

    def render(self) -> str:
        lignes = [f"--- {self.name} ---"]
        if self.minimum_win_rate > 0.0:
            lignes.append(
                f"  taux de victoire {self.win_rate:.0%} (minimum {self.minimum_win_rate:.0%})"
            )
        else:
            lignes.append(f"  taux de victoire {self.win_rate:.0%}")
        if self.winless:
            lignes.append(f"  sans aucune victoire : {', '.join(self.winless)}")
        if self.missing:
            lignes.append(f"  absents du banc     : {', '.join(self.missing)}")
        if self.regressions:
            lignes.append(f"  regressions         : {len(self.regressions)}")
        lignes.append("  -> " + ("FRANCHIE" if self.passed else "NON FRANCHIE"))
        return "\n".join(lignes)


def gate_verdict(
    report: BenchmarkReport,
    *,
    name: str,
    comparison: Comparison | None = None,
    minimum_win_rate: float = 0.0,
) -> GateVerdict:
    """Juge un banc contre un contrat de porte.

    Chaque scenario compte pour une famille : le banc n'en regroupe aucun, et
    inventer des familles ici reviendrait a choisir apres coup lesquelles
    peuvent se permettre de ne jamais gagner.
    """
    return GateVerdict(
        name=name,
        winless=tuple(
            entry.scenario for entry in report.scenarios if entry.battles and not entry.win_rate
        ),
        missing=tuple(comparison.missing_scenarios) if comparison is not None else (),
        regressions=(
            tuple(item.describe() for item in comparison.regressions)
            if comparison is not None
            else ()
        ),
        win_rate=report.win_rate,
        minimum_win_rate=minimum_win_rate,
    )


def compare(
    baseline: BenchmarkReport,
    candidate: BenchmarkReport,
    *,
    win_rate_tolerance: float = DEFAULT_WIN_RATE_TOLERANCE,
    strength_tolerance: float = DEFAULT_STRENGTH_TOLERANCE,
) -> Comparison:
    """Compare deux bancs, scenario par scenario.

    Une amelioration globale ne rachete pas l'effondrement d'un scenario : le
    README demande explicitement qu'aucun scenario critique ne regresse au-dela
    d'un seuil. La comparaison est donc faite par scenario, pas seulement en
    moyenne.
    """
    regressions: list[MetricChange] = []
    improvements: list[MetricChange] = []
    missing: list[str] = []

    for reference in baseline.scenarios:
        current = candidate.result(reference.scenario)
        if current is None:
            missing.append(reference.scenario)
            continue

        for metric, before, after, tolerance in (
            (
                "taux de victoire",
                reference.win_rate,
                current.win_rate,
                win_rate_tolerance,
            ),
            (
                "forces restantes",
                reference.average_ally_remaining,
                current.average_ally_remaining,
                strength_tolerance,
            ),
            (
                "survie du seigneur",
                reference.lord_survival,
                current.lord_survival,
                0.0,
            ),
        ):
            change = MetricChange(reference.scenario, metric, before, after)
            if after < before - tolerance:
                regressions.append(change)
            elif after > before + tolerance:
                improvements.append(change)

    return Comparison(
        regressions=tuple(regressions),
        improvements=tuple(improvements),
        win_rate_before=baseline.win_rate,
        win_rate_after=candidate.win_rate,
        strength_before=baseline.average_ally_remaining,
        strength_after=candidate.average_ally_remaining,
        missing_scenarios=tuple(missing),
    )


#: Abreviation de chaque issue dans le tableau du banc.
#:
#: **Le tableau abregeait par la premiere lettre, et `defeat` comme `draw`
#: donnaient « d ».** Les deux issues etaient donc indiscernables a la lecture,
#: sur la seule colonne qui dit qui a gagne.
#:
#: Ce n'est pas un detail cosmetique : `skirmish_standoff` est passe du nul a la
#: defaite sur ses trois graines sans que la colonne bouge d'un caractere — il a
#: fallu un script separe pour s'en apercevoir. Une ligne « 2d, 1d » ne se lit
#: meme pas comme une anomalie, alors qu'elle annonce deux defaites et un nul.
OUTCOME_TAGS = {"victory": "v", "draw": "n", "defeat": "d"}


def render_table(report: BenchmarkReport) -> str:
    """Tableau texte du banc, lisible dans un terminal."""
    header = (
        f"{'scenario':24} {'issues':>18} {'victoires':>10} {'allies':>8} "
        f"{'ennemis':>8} {'duree':>8} {'stab.':>7} {'ordres/min':>11}"
    )
    lines = [header, "-" * len(header)]
    for entry in report.scenarios:
        outcomes = ", ".join(
            f"{count}{OUTCOME_TAGS.get(name, name[0])}"
            for name, count in sorted(entry.outcomes.items())
        )
        lines.append(
            f"{entry.scenario:24} {outcomes:>18} {entry.win_rate:>9.0%} "
            f"{entry.average_ally_remaining:>7.0%} {entry.average_enemy_remaining:>7.0%} "
            f"{entry.average_duration:>7.0f}s {entry.stability:>6.2f} "
            f"{entry.orders_per_minute:>10.1f}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'ensemble':24} {str(report.battles) + ' batailles':>18} {report.win_rate:>9.0%} "
        f"{report.average_ally_remaining:>7.0%} {'':>8} {'':>8} {'':>7}"
    )
    lines.append(
        f"survie du seigneur {report.lord_survival:.0%}, "
        f"recompense moyenne {report.average_reward:+.0f}"
    )
    return "\n".join(lines)
