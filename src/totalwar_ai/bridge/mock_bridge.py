"""Pont factice : rejoue une suite d'etats et enregistre les actions recues.

Il sert a tester la boucle complete etat -> decision -> resultat sans lancer le
jeu, y compris les chemins d'erreur (action refusee, message tronque).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from totalwar_ai.bridge.base import Bridge
from totalwar_ai.bridge.protocol import (
    ActionResultMessage,
    AgentActionsMessage,
    BattleStateMessage,
    decode_message,
)
from totalwar_ai.domain.actions import ActionResult, ActionStatus, AgentAction
from totalwar_ai.domain.battle_state import BattleState

#: Politique de reponse : decide du sort de chaque action soumise.
ResultPolicy = Callable[[AgentAction], ActionResult]


def always_accept(action: AgentAction) -> ActionResult:
    return ActionResult(action_id=action.action_id, status=ActionStatus.ACCEPTED)


def always_reject(reason: str = "refus simule") -> ResultPolicy:
    def policy(action: AgentAction) -> ActionResult:
        return ActionResult(action_id=action.action_id, status=ActionStatus.REJECTED, error=reason)

    return policy


@dataclass
class MockBridge(Bridge):
    """Pont scripte pour les tests et le developpement hors jeu.

    Les etats sont consommes un par un ; chaque envoi d'actions est journalise
    dans `sent_actions` et transforme en accuses via `result_policy`.
    """

    states: list[BattleState] = field(default_factory=list)
    result_policy: ResultPolicy = always_accept
    sent_actions: list[AgentAction] = field(default_factory=list)
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False
    _cursor: int = 0
    _pending_results: list[ActionResult] = field(default_factory=list)

    @classmethod
    def from_states(cls, states: Iterable[BattleState], **kwargs: Any) -> MockBridge:
        return cls(states=list(states), **kwargs)

    @classmethod
    def from_messages(cls, messages: Iterable[dict[str, Any]], **kwargs: Any) -> MockBridge:
        """Construit le pont a partir de messages bruts (test du protocole).

        Les messages qui ne sont pas des etats de bataille sont ignores, comme
        le ferait un consommateur tolerant face a un flux mixte.
        """
        states: list[BattleState] = []
        for message in messages:
            decoded = decode_message(message)
            if isinstance(decoded, BattleStateMessage):
                states.append(decoded.state)
        return cls(states=states, **kwargs)

    # --- interface Bridge ----------------------------------------------------

    def receive_state(self) -> BattleState | None:
        if self._cursor >= len(self.states):
            return None
        state = self.states[self._cursor]
        self._cursor += 1
        return state

    def send_actions(self, actions: Sequence[AgentAction]) -> None:
        if self.closed:
            raise RuntimeError("Le pont est ferme")
        if not actions:
            return
        battle_id = self.states[max(0, self._cursor - 1)].battle_id if self.states else "unknown"
        sequence = self._cursor
        message = AgentActionsMessage(
            battle_id=battle_id, sequence=sequence, actions=tuple(actions)
        )
        self.sent_messages.append(message.to_dict())
        for action in actions:
            self.sent_actions.append(action)
            self._pending_results.append(self.result_policy(action))

    def poll_results(self) -> list[ActionResult]:
        results = list(self._pending_results)
        self._pending_results.clear()
        return results

    def close(self) -> None:
        self.closed = True

    # --- aides de test -------------------------------------------------------

    @property
    def remaining_states(self) -> int:
        return max(0, len(self.states) - self._cursor)

    def result_messages(self, battle_id: str) -> list[dict[str, Any]]:
        """Rejoue les accuses au format protocole (utile pour les tests d'integration)."""
        return [
            ActionResultMessage(battle_id=battle_id, result=result).to_dict()
            for result in self._pending_results
        ]

    def reset(self) -> None:
        self._cursor = 0
        self.sent_actions.clear()
        self.sent_messages.clear()
        self._pending_results.clear()
        self.closed = False
