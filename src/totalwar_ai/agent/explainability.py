"""Explicabilite des decisions.

Exigence du README : « Chaque decision importante doit pouvoir etre resumee ».
Le format de sortie est celui documente — Action / Cause / Objectif / Confiance —
et il est reutilise tel quel par les journaux et le rapport post-bataille.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from totalwar_ai.domain.actions import ActionType, AgentAction

#: Verbalisation francaise de chaque type d'action.
ACTION_LABELS: dict[ActionType, str] = {
    ActionType.HOLD_POSITION: "tenir la position",
    ActionType.MOVE_GROUP: "deplacer le groupe",
    ActionType.ATTACK_TARGET: "engager la cible",
    ActionType.FOCUS_FIRE: "concentrer le tir",
    ActionType.PROTECT: "proteger le groupe",
    ActionType.FLANK: "prendre a revers",
    ActionType.RETREAT: "replier",
    ActionType.DISENGAGE: "rompre le combat",
    ActionType.CHASE_ROUTING: "poursuivre les fuyards",
    ActionType.FORM_RESERVE: "constituer une reserve",
    ActionType.REORIENT_FRONT: "reorienter le front",
}


def format_confidence(confidence: float) -> str:
    """Confiance au format francais, deux decimales (`0,82`)."""
    return f"{confidence:.2f}".replace(".", ",")


def describe_action(action: AgentAction) -> str:
    """Phrase courte decrivant l'action et ses unites."""
    label = ACTION_LABELS.get(action.type, action.type.value)
    actors = ", ".join(action.actor_ids)
    target = action.target_id
    if target:
        return f"{label} ({actors} -> {target})"
    return f"{label} ({actors})"


@dataclass(frozen=True, slots=True)
class Decision:
    """Action accompagnee de sa justification.

    `blocked_by` est renseigne lorsqu'une regle de securite a refuse l'action ;
    `replacement` porte alors l'action de substitution effectivement emise.
    """

    action: AgentAction
    cause: str
    objective: str
    blocked_by: str | None = None
    replacement: AgentAction | None = None

    @property
    def confidence(self) -> float:
        return self.action.confidence

    @property
    def is_blocked(self) -> bool:
        return self.blocked_by is not None

    def explain(self) -> str:
        """Resume lisible au format du README."""
        lines = [
            f"Action : {describe_action(self.action)}",
            f"Cause : {self.cause}",
            f"Objectif : {self.objective}",
            f"Confiance : {format_confidence(self.confidence)}",
        ]
        if self.blocked_by:
            lines.append(f"Bloquee par : {self.blocked_by}")
        if self.replacement is not None:
            lines.append(f"Remplacee par : {describe_action(self.replacement)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "cause": self.cause,
            "objective": self.objective,
            "blocked_by": self.blocked_by,
            "replacement": self.replacement.to_dict() if self.replacement else None,
        }


def decide(
    action: AgentAction, cause: str, objective: str, *, confidence: float | None = None
) -> Decision:
    """Fabrique une :class:`Decision`, en synchronisant `reason` et la cause.

    Le champ `reason` de l'action est ce qui part vers le jeu ; la cause reste
    disponible cote agent pour les journaux.
    """
    enriched = action if action.reason else action.with_reason(cause)
    if confidence is not None:
        enriched = AgentAction(
            type=enriched.type,
            actor_ids=enriched.actor_ids,
            parameters=dict(enriched.parameters),
            reason=enriched.reason,
            confidence=confidence,
            action_id=enriched.action_id,
        )
    return Decision(action=enriched, cause=cause, objective=objective)
