"""Interface abstraite d'un pont vers le jeu.

Seule cette abstraction est connue de l'agent. Aujourd'hui elle n'a qu'une
implementation de test (:class:`~totalwar_ai.bridge.mock_bridge.MockBridge`) ;
le pont reel (fichiers locaux ou autre) sera ajoute une fois le spike Lua de la
Phase 0 termine, sans toucher au cœur de l'agent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from types import TracebackType

from totalwar_ai.domain.actions import ActionResult, AgentAction
from totalwar_ai.domain.battle_state import BattleState


class BridgeError(RuntimeError):
    """Echec de communication avec le jeu."""


class Bridge(ABC):
    """Canal bidirectionnel etat <-> actions."""

    @abstractmethod
    def receive_state(self) -> BattleState | None:
        """Prochain etat disponible, ou `None` si la bataille est terminee."""

    @abstractmethod
    def send_actions(self, actions: Sequence[AgentAction]) -> None:
        """Transmet un lot d'actions validees."""

    @abstractmethod
    def poll_results(self) -> list[ActionResult]:
        """Recupere les accuses d'execution disponibles."""

    def close(self) -> None:
        """Libere les ressources.

        Volontairement non abstraite : un pont sans ressource a liberer n'a rien
        a implementer.
        """
        return None

    def __enter__(self) -> Bridge:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
