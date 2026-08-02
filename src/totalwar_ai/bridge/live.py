"""Boucle de pilotage : l'agent decide, le jeu execute.

C'est le raccord des quatre morceaux construits separement — observation de la
bataille, memoire des effectifs, agent tactique, commande de groupe. Chaque tour
suit toujours le meme enchainement :

    lire l'etat -> memoriser les effectifs -> traduire vers le domaine
    -> decider -> traduire en ordres -> publier

**Ce que cette boucle refuse de faire**, et qui compte autant que ce qu'elle
fait :

* elle n'emet rien avant que la bataille ne soit engagee — un ordre donne en
  deploiement est acquitte par le moteur mais ne produit aucun deplacement ;
* elle n'emet rien si l'arret d'urgence est demande ;
* elle ne traduit pas approximativement une action sans equivalent : elle la
  compte et la nomme.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.file_bridge import FileBridge
from totalwar_ai.bridge.orders import OrderTranslator, Translation
from totalwar_ai.bridge.roster import RosterMemory
from totalwar_ai.domain.actions import ActionType

LOGGER = logging.getLogger("totalwar_ai.bridge.live")


@dataclass(frozen=True, slots=True)
class LiveStep:
    """Compte rendu d'un tour de boucle, lisible tel quel par l'operateur."""

    #: `None` quand aucun etat n'etait disponible.
    state: ProbeBattleState | None = None
    #: Raison pour laquelle rien n'a ete emis, le cas echeant.
    skipped: str | None = None
    decisions: tuple[str, ...] = ()
    translation: Translation = field(default_factory=Translation)
    sent: int = 0

    @property
    def acted(self) -> bool:
        return self.sent > 0

    def summary(self) -> str:
        """Une ligne, dans la langue de l'operateur."""
        if self.state is None:
            return "aucun etat recu du jeu"
        entete = (
            f"t={self.state.game_time_ms / 1000:6.1f}s "
            f"{len(self.state.allies):2d} allies / {len(self.state.enemies):2d} ennemis"
        )
        if self.skipped:
            return f"{entete} — {self.skipped}"
        if not self.sent:
            reste = self.translation.untranslated
            if reste:
                noms = ", ".join(sorted({item[0].value for item in reste}))
                return f"{entete} — aucun ordre traduisible ({noms})"
            return f"{entete} — rien a faire"
        return f"{entete} — {self.sent} unite(s) en mouvement"


@dataclass
class LiveSession:
    """Pilote une bataille en cours, un tour a la fois.

    La session ne dort jamais et ne boucle pas d'elle-meme : `step()` fait un
    tour et rend la main. L'appelant garde ainsi le controle de la cadence, et
    la boucle reste testable sans horloge.
    """

    bridge: FileBridge
    agent: DeterministicTacticalAgent
    roster: RosterMemory = field(default_factory=RosterMemory)
    translator: OrderTranslator = field(default_factory=OrderTranslator)
    battle_id: str = "live"
    #: Duree au bout de laquelle le jeu rend la main sur chaque unite prise.
    #:
    #: Volontairement courte : le joueur doit pouvoir reprendre une unite sans
    #: attendre, et un ordre perime vaut mieux qu'une unite confisquee.
    release_after_ms: int = 5000

    def step(self) -> LiveStep:
        """Un tour complet. Ne leve pas : un tour rate ne doit pas tout arreter."""
        state = self.bridge.latest_battle_state()
        if state is None:
            return LiveStep()

        self.roster.observe(state)

        if self.bridge.stop_requested:
            return LiveStep(state=state, skipped="arret d'urgence demande")
        if self.agent.emergency_stopped:
            return LiveStep(state=state, skipped="agent en arret d'urgence")
        if not state.orders_take_effect:
            return LiveStep(
                state=state,
                skipped=f"phase {state.phase} : un ordre n'aurait aucun effet",
            )

        domaine = state.to_battle_state(
            self.battle_id,
            entity_ratios=self._ratios(state, self.roster.entity_ratio),
            ammo_ratios=self._ratios(state, self.roster.ammo_ratio),
        )
        tour = self.agent.decide(domaine)
        if tour.skipped_reason:
            return LiveStep(state=state, skipped=tour.skipped_reason)

        translation = self.translator.translate(tour.actions, domaine)
        for action_type, motif in translation.untranslated:
            LOGGER.debug("action non traduite : %s (%s)", action_type.value, motif)

        if translation.is_empty:
            return LiveStep(
                state=state,
                decisions=tuple(tour.explanations()),
                translation=translation,
            )

        self.bridge.move_units(translation.moves, release_after_ms=self.release_after_ms)
        return LiveStep(
            state=state,
            decisions=tuple(tour.explanations()),
            translation=translation,
            sent=len(translation.moves),
        )

    def stop(self) -> None:
        """Arret d'urgence : le jeu libere tout et le joueur reprend la main."""
        self.agent.trigger_emergency_stop()
        self.bridge.abort("arret demande par la boucle de pilotage")

    @staticmethod
    def _ratios(
        state: ProbeBattleState,
        mesure: Callable[[ProbeUnitObservation], float | None],
    ) -> dict[str, float]:
        """Applique une mesure de `RosterMemory` a toutes les unites vues.

        Les unites pour lesquelles la mesure ne conclut pas sont **absentes** du
        resultat, et non presentes a zero : l'appelant retombe alors sur le
        defaut du domaine plutot que sur un chiffre fabrique.
        """
        resultats: dict[str, float] = {}
        for observation in (*state.allies, *state.enemies):
            valeur = mesure(observation)
            if valeur is not None:
                resultats[observation.unit_id] = valeur
        return resultats


#: Actions que la boucle sait rendre aujourd'hui. Le reste attend le point 3.
TRANSLATABLE_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.MOVE_GROUP,
        ActionType.RETREAT,
        ActionType.DISENGAGE,
        ActionType.FORM_RESERVE,
        ActionType.HOLD_POSITION,
    }
)
