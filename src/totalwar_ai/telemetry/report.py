"""Rapport post-bataille.

Objectif du README : « obtenir a la fin un rapport indiquant ce que l'agent a
tente, ce qui a fonctionne, ce qui a echoue et ce qu'il conservera en memoire ».
Le rapport est un fichier Markdown lisible tel quel dans un terminal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from totalwar_ai.agent.explainability import Decision, describe_action
from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.learning.adaptation import DoctrineProfile
from totalwar_ai.learning.rewards import RewardBreakdown
from totalwar_ai.telemetry.events import Event, EventType

if TYPE_CHECKING:
    # `memory` importe `telemetry.events` : garder cet import au niveau des
    # annotations evite un cycle a l'import du paquet.
    from totalwar_ai.memory.models import BattleSummary

#: Evenements repris dans la chronologie du rapport.
TIMELINE_EVENTS: tuple[EventType, ...] = (
    EventType.BATTLE_STARTED,
    EventType.UNIT_ROUTED,
    EventType.UNIT_DESTROYED,
    EventType.FLANK_ATTACK,
    EventType.EMERGENCY_STOP,
    EventType.BATTLE_FINISHED,
)

OUTCOME_LABELS: dict[BattleOutcomeKind, str] = {
    BattleOutcomeKind.VICTORY: "Victoire",
    BattleOutcomeKind.DEFEAT: "Defaite",
    BattleOutcomeKind.DRAW: "Match nul",
    BattleOutcomeKind.UNKNOWN: "Indetermine",
}


@dataclass(frozen=True, slots=True)
class ReportContext:
    """Tout ce dont le rapport a besoin, deja collecte pendant la bataille."""

    summary: BattleSummary
    events: Sequence[Event] = ()
    decisions: Sequence[Decision] = ()
    blocked: Sequence[Decision] = ()
    reward: RewardBreakdown = field(default_factory=RewardBreakdown)
    history: Sequence[BattleSummary] = ()
    profile: DoctrineProfile = field(default_factory=DoctrineProfile)
    #: Suite (temps, part alliee restante, part ennemie restante).
    strength_series: Sequence[tuple[float, float, float]] = ()


def render_report(context: ReportContext) -> str:
    """Produit le rapport Markdown complet."""
    summary = context.summary
    lines: list[str] = [
        f"# Rapport de bataille — {summary.scenario or summary.battle_id}",
        "",
        f"- **Issue** : {OUTCOME_LABELS.get(summary.outcome, summary.outcome.value)}",
        f"- **Duree** : {summary.duration:.0f} s de temps de jeu",
        f"- **Forces restantes** : alliees {summary.ally_remaining:.0%}, "
        f"ennemies {summary.enemy_remaining:.0%}",
        f"- **Recompense totale** : {summary.total_reward:.1f}",
        f"- **Identifiant** : `{summary.battle_id}`",
        "",
        "## Ce que l'agent a tente",
        "",
    ]

    lines.extend(_action_summary(context.decisions))
    lines.extend(["", "## Ce qui a ete refuse par la securite", ""])
    lines.extend(_blocked_summary(context.blocked))
    lines.extend(["", "## Decisions marquantes", ""])
    lines.extend(_highlight_decisions(context.decisions))
    lines.extend(["", "## Deroulement", ""])
    lines.extend(_strength_chart(context.strength_series))
    lines.extend(["", "## Chronologie", ""])
    lines.extend(_timeline(context.events))
    lines.extend(["", "## Ce que la memoire a change", ""])
    lines.extend(_doctrine_section(context.profile))
    lines.extend(["", "## Detail de la recompense", ""])
    lines.extend(_reward_table(context.reward))
    lines.extend(["", "## Ce qui est conserve en memoire", ""])
    lines.extend(_memory_section(context))
    lines.extend(["", "## Reproductibilite", ""])
    lines.extend(
        [
            f"- Graine : `{summary.seed}`",
            f"- Composition : `{summary.army_fingerprint or 'non renseignee'}`",
            f"- Mode de l'agent : `{summary.agent_mode}`",
            f"- Version du protocole : `{summary.protocol_version}`",
            f"- Version du bareme de recompense : `{summary.reward_version}`",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(context: ReportContext, directory: str | Path) -> Path:
    """Ecrit le rapport dans `directory` et renvoie son chemin."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{context.summary.battle_id}.md"
    path.write_text(render_report(context), encoding="utf-8")
    return path


# --- sections ----------------------------------------------------------------


def _action_summary(decisions: Sequence[Decision]) -> list[str]:
    if not decisions:
        return ["_Aucun ordre emis._"]
    counts: dict[str, int] = {}
    for decision in decisions:
        key = decision.action.type.value
        counts[key] = counts.get(key, 0) + 1
    lines = [f"{len(decisions)} ordres emis :", ""]
    lines.extend(
        f"- `{name}` : {count}" for name, count in sorted(counts.items(), key=lambda item: -item[1])
    )
    return lines


def _blocked_summary(blocked: Sequence[Decision]) -> list[str]:
    if not blocked:
        return ["_Aucune action bloquee : le planificateur est reste dans les clous._"]
    counts: dict[str, int] = {}
    for decision in blocked:
        rule = decision.blocked_by or "inconnue"
        counts[rule] = counts.get(rule, 0) + 1
    lines = [f"{len(blocked)} actions bloquees :", ""]
    lines.extend(
        f"- `{rule}` : {count}" for rule, count in sorted(counts.items(), key=lambda item: -item[1])
    )
    lines.append("")
    lines.append("Exemples :")
    lines.append("")
    for decision in blocked[:3]:
        lines.append(f"- {describe_action(decision.action)} — {decision.cause}")
    return lines


def _highlight_decisions(decisions: Sequence[Decision], limit: int = 5) -> list[str]:
    """Une decision par type d'action, la plus confiante d'abord."""
    if not decisions:
        return ["_Aucune decision a expliquer._"]
    seen: set[str] = set()
    selected: list[Decision] = []
    for decision in sorted(decisions, key=lambda item: -item.confidence):
        key = decision.action.type.value
        if key in seen:
            continue
        seen.add(key)
        selected.append(decision)
        if len(selected) >= limit:
            break
    lines: list[str] = []
    for decision in selected:
        lines.append("```text")
        lines.append(decision.explain())
        lines.append("```")
        lines.append("")
    return lines[:-1] if lines else lines


def _strength_chart(
    series: Sequence[tuple[float, float, float]], bands: int = 12, width: int = 20
) -> list[str]:
    """Courbe d'usure des deux armees, en barres de texte.

    Volontairement en texte : le rapport doit rester lisible dans un terminal,
    dans un diff Git et dans un ticket, sans dependance graphique.
    """
    if not series:
        return ["_Aucune mesure d'effectifs._"]
    samples = _sample(series, bands)
    lines = ["```text", f"{'temps':>8}  {'allies':<{width}}  {'ennemis':<{width}}"]
    for game_time, ally, enemy in samples:
        lines.append(
            f"{game_time:7.0f}s  {_bar(ally, width)} {ally:4.0%}  {_bar(enemy, width)} {enemy:4.0%}"
        )
    lines.append("```")
    return lines


def _sample(
    series: Sequence[tuple[float, float, float]], bands: int
) -> list[tuple[float, float, float]]:
    """Reduit la serie a `bands` points repartis regulierement, fin incluse."""
    if len(series) <= bands:
        return list(series)
    step = (len(series) - 1) / (bands - 1)
    indices = sorted({round(index * step) for index in range(bands)} | {len(series) - 1})
    return [series[index] for index in indices]


def _bar(ratio: float, width: int) -> str:
    filled = max(0, min(width, round(ratio * width)))
    return "█" * filled + "░" * (width - filled)


def _doctrine_section(profile: DoctrineProfile) -> list[str]:
    """Ce que l'historique a change dans la doctrine de cette bataille."""
    stats = profile.stats
    if profile.is_empty:
        if stats.sample_size == 0:
            return ["_Aucune bataille comparable en memoire : doctrine par defaut._"]
        return [
            f"_{stats.sample_size} bataille(s) comparable(s) en memoire, "
            "mais aucun ajustement declenche : doctrine par defaut._"
        ]
    lines = [
        f"Doctrine ajustee d'apres {stats.sample_size} bataille(s) comparable(s) "
        f"(taux de victoire {stats.win_rate:.0%}, "
        f"forces restantes {stats.average_ally_remaining:.0%} en moyenne) :",
        "",
    ]
    lines.extend(f"- {reason}" for reason in profile.rationale)
    lines.append("")
    lines.append("| Reglage | Valeur retenue |")
    lines.append("| --- | ---: |")
    for name, value in sorted(profile.adjustments.items()):
        lines.append(f"| `{name}` | {value:g} |")
    return lines


def _timeline(events: Sequence[Event]) -> list[str]:
    notable = [event for event in events if event.type in TIMELINE_EVENTS]
    if not notable:
        return ["_Aucun evenement notable._"]
    lines = []
    for event in notable:
        detail = _event_detail(event)
        lines.append(f"- `{event.game_time:7.1f}s` **{event.type.value}** {detail}")
    return lines


def _event_detail(event: Event) -> str:
    payload = event.payload
    unit_id = payload.get("unit_id")
    if unit_id:
        side = payload.get("side", "?")
        role = payload.get("role", "?")
        extra = " (sortie du champ)" if payload.get("fled") else ""
        target = payload.get("target_id")
        suffix = f" sur `{target}`" if target else ""
        return f"`{unit_id}` [{side}/{role}]{suffix}{extra}"
    if event.type is EventType.BATTLE_FINISHED:
        return (
            f"issue `{payload.get('outcome')}`, "
            f"restant allie {float(payload.get('ally_remaining', 0.0)):.0%}"
        )
    return ""


def _reward_table(reward: RewardBreakdown) -> list[str]:
    if not reward.components:
        return [f"Total : **{reward.total:.1f}** (aucune composante detaillee)."]
    lines = ["| Composante | Valeur |", "| --- | ---: |"]
    for name, value in sorted(reward.components.items(), key=lambda item: -abs(item[1])):
        lines.append(f"| `{name}` | {value:+.1f} |")
    lines.append(f"| **Total** | **{reward.total:+.1f}** |")
    return lines


def _memory_section(context: ReportContext) -> list[str]:
    summary = context.summary
    lines = [
        f"- Transitions enregistrees : {summary.metrics.get('transitions', 0)}",
        f"- Evenements journalises : {len(context.events)}",
        f"- Ordres envoyes : {summary.actions_sent}, bloques : {summary.actions_blocked}, "
        f"refuses par l'adaptateur : {summary.actions_rejected}",
    ]
    if not context.history:
        lines.append("- Aucune bataille comparable en memoire : c'est la premiere du genre.")
        return lines
    lines.append("")
    lines.append("Batailles comparables deja en memoire :")
    lines.append("")
    for previous in context.history:
        lines.append(
            f"- `{previous.battle_id[:8]}` — {OUTCOME_LABELS.get(previous.outcome, '?')}, "
            f"recompense {previous.total_reward:+.0f}, "
            f"forces alliees restantes {previous.ally_remaining:.0%}"
        )
    return lines
