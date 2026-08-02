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
from totalwar_ai.domain.geometry import Vector3

LOGGER = logging.getLogger("totalwar_ai.bridge.live")

#: Distance en deca de laquelle un nouvel ordre de deplacement est superflu.
#:
#: Constate en bataille : le seigneur faisait des allers-retours. Sa position de
#: soutien se calcule par rapport au barycentre de l'armee, dont il fait partie
#: — il bouge, le barycentre bouge, sa cible bouge, il rebouge. La deduplication
#: de l'agent ne l'attrape pas : la destination change de quelques metres a
#: chaque fois, donc l'ordre n'est jamais tout a fait le meme.
#:
#: Vingt metres : en deca, le deplacement ne vaut ni l'ordre ni la saccade.
MIN_REORDER_DISTANCE = 20.0


@dataclass(frozen=True, slots=True)
class LiveStep:
    """Compte rendu d'un tour de boucle, lisible tel quel par l'operateur."""

    #: `None` quand aucun etat n'etait disponible.
    state: ProbeBattleState | None = None
    #: Raison pour laquelle rien n'a ete emis, le cas echeant.
    skipped: str | None = None
    #: Decisions **retenues** par l'agent, expliquees.
    decisions: tuple[str, ...] = ()
    #: Decisions **refusees** par les regles de securite, avec leur motif.
    #:
    #: Distinguees des precedentes : les afficher ensemble laissait croire que
    #: neuf actions avaient ete prises la ou deux ordres seulement etaient
    #: partis, les sept autres ayant ete bloquees.
    blocked: tuple[str, ...] = ()
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

        detail = []
        if self.translation.moves:
            detail.append(f"{len(self.translation.moves)} deplacement(s)")
        if self.translation.attacks:
            detail.append(f"{len(self.translation.attacks)} attaque(s)")
        if self.translation.halts:
            detail.append(f"{len(self.translation.halts)} arret(s)")

        # Les actions perdues se disent **toujours**, y compris quand d'autres
        # sont parties. Ne les montrer qu'en l'absence d'ordre les a fait passer
        # inapercues en bataille : deux deplacements masquaient trois
        # contournements abandonnes, et le compte rendu paraissait normal.
        if self.translation.untranslated:
            noms = ", ".join(sorted({item[0].value for item in self.translation.untranslated}))
            detail.append(f"{len(self.translation.untranslated)} action(s) perdue(s) : {noms}")

        # Un refus de securite est une decision, pas un silence : il doit se
        # voir au meme titre qu'un ordre emis.
        if self.blocked:
            detail.append(f"{len(self.blocked)} refusee(s) par la securite")

        return f"{entete} — " + (", ".join(detail) if detail else "rien a faire")


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
    #: Derniere destination envoyee a chaque unite, pour ne pas la faire osciller.
    _last_destination: dict[str, Vector3] = field(default_factory=dict)

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
        translation = self._drop_micro_moves(translation)

        prises = tuple(decision.explain() for decision in tour.decisions)
        refusees = tuple(decision.explain() for decision in tour.blocked)

        if translation.is_empty:
            return LiveStep(
                state=state,
                decisions=prises,
                blocked=refusees,
                translation=translation,
            )

        # Un seul message : deux commandes successives se perdraient, le
        # fichier etant remplace et relu toutes les 500 ms seulement.
        self.bridge.send_orders(
            translation.moves,
            translation.attacks,
            translation.halts,
            release_after_ms=self.release_after_ms,
        )
        return LiveStep(
            state=state,
            decisions=prises,
            blocked=refusees,
            translation=translation,
            sent=translation.order_count,
        )

    def _drop_micro_moves(self, translation: Translation) -> Translation:
        """Ecarte les deplacements trop courts pour valoir un ordre.

        Sans cela une unite dont la destination se recalcule a chaque tour
        oscille sur place — constate en jeu sur le seigneur, qui faisait des
        allers-retours. Une destination reellement nouvelle passe ; une
        correction de quelques metres est ignoree, et la memoire n'est pas
        mise a jour, pour que la derive lente finisse par franchir le seuil.
        """
        gardes: list[tuple[str, Vector3]] = []
        for unit_id, point in translation.moves:
            precedente = self._last_destination.get(unit_id)
            if precedente is not None and precedente.distance_2d(point) < MIN_REORDER_DISTANCE:
                LOGGER.debug("deplacement ignore pour %s : trop proche du precedent", unit_id)
                continue
            self._last_destination[unit_id] = point
            gardes.append((unit_id, point))

        # Une unite qu'on arrete ou qu'on lance a l'attaque n'a plus de
        # destination en cours : son prochain deplacement doit repartir libre.
        for unit_id in (*translation.halts, *(item.unit_id for item in translation.attacks)):
            self._last_destination.pop(unit_id, None)

        if len(gardes) == len(translation.moves):
            return translation
        return Translation(
            moves=tuple(gardes),
            attacks=translation.attacks,
            halts=translation.halts,
            untranslated=translation.untranslated,
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


#: Actions que la boucle sait rendre aujourd'hui.
#:
#: Seule `REORIENT_FRONT` reste hors de portee : elle demande un ordre
#: d'orientation que le jeu n'expose pas, et elle avait ete retiree du perimetre
#: de l'agent apres mesure (voir ADR 0004).
TRANSLATABLE_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.MOVE_GROUP,
        ActionType.RETREAT,
        ActionType.DISENGAGE,
        ActionType.FORM_RESERVE,
        ActionType.HOLD_POSITION,
        ActionType.ATTACK_TARGET,
        ActionType.FOCUS_FIRE,
        ActionType.CHASE_ROUTING,
        ActionType.FLANK,
        ActionType.PROTECT,
    }
)
