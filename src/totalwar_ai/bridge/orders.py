"""Traduction des intentions de l'agent en ordres que le jeu comprend.

L'agent raisonne en actions tactiques — `MOVE_GROUP`, `RETREAT`, `FLANK` — dont
la plupart designent un groupe et une destination unique. Le jeu, lui, ne
connait que des unites et des points. Ce module fait le raccord.

**Une action non traduisible n'est pas approximee.** Envoyer une unite « vers »
sa cible en guise d'attaque produirait un comportement qui ressemble a l'ordre
demande sans en etre un : l'unite avancerait sans engager, et le journal
affirmerait qu'elle attaque. Les actions sans equivalent sont donc rendues telles
quelles a l'appelant, qui les compte et les nomme.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from totalwar_ai.domain.actions import ActionType, AgentAction
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3, heading_vector

#: Espacement lateral par defaut entre deux unites d'une meme ligne, en metres.
#:
#: Le jeu ne donne pas la largeur de front d'une unite (`width` est absent du bac
#: a sable), impossible donc de calculer un espacement juste. Trente metres
#: laissent passer une unite d'infanterie sans chevauchement visible.
DEFAULT_SPACING = 30.0


@dataclass(frozen=True, slots=True)
class Translation:
    """Ce qu'une decision de l'agent devient, cote jeu."""

    #: `(identifiant, destination)` prets pour `FileBridge.move_units`.
    moves: tuple[tuple[str, Vector3], ...] = ()
    #: Actions qu'aucun ordre disponible ne sait rendre, avec leur motif.
    untranslated: tuple[tuple[ActionType, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.moves


@dataclass
class OrderTranslator:
    """Traduit les actions de l'agent en deplacements."""

    spacing: float = DEFAULT_SPACING

    #: Actions dont la destination se lit directement dans les parametres.
    destination_keys: dict[ActionType, str] = field(
        default_factory=lambda: {
            ActionType.MOVE_GROUP: "destination",
            ActionType.RETREAT: "destination",
            ActionType.DISENGAGE: "destination",
            ActionType.FORM_RESERVE: "rally_point",
        }
    )

    def translate(self, actions: tuple[AgentAction, ...], state: BattleState) -> Translation:
        """Traduit un tour de l'agent, sans jamais inventer d'equivalence."""
        moves: list[tuple[str, Vector3]] = []
        untranslated: list[tuple[ActionType, str]] = []
        deja_ordonnees: set[str] = set()

        for action in actions:
            key = self.destination_keys.get(action.type)
            if key is None:
                if action.type is not ActionType.HOLD_POSITION:
                    untranslated.append((action.type, self._why(action.type)))
                # `HOLD_POSITION` se traduit par l'absence d'ordre : ne rien
                # envoyer est exactement ce qu'elle demande.
                continue

            destination = action.parameters.get(key)
            if not isinstance(destination, Vector3):
                untranslated.append((action.type, f"parametre '{key}' absent ou invalide"))
                continue

            for unit_id, point in self._spread(action, destination, state):
                # Une unite ne peut suivre qu'un ordre : le premier emis gagne,
                # les actions etant deja classees par priorite par l'agent.
                if unit_id not in deja_ordonnees:
                    deja_ordonnees.add(unit_id)
                    moves.append((unit_id, point))

        return Translation(moves=tuple(moves), untranslated=tuple(untranslated))

    def _spread(
        self,
        action: AgentAction,
        destination: Vector3,
        state: BattleState,
    ) -> list[tuple[str, Vector3]]:
        """Repartit un groupe en ligne autour de sa destination.

        Envoyer toutes les unites au meme point produirait un tas : le moteur
        les empilerait, et la notion meme de formation disparaitrait. La ligne
        est perpendiculaire au cap demande, centree sur la destination.
        """
        unit_ids = [unit_id for unit_id in action.actor_ids if state.unit(unit_id) is not None]
        if not unit_ids:
            return []
        if len(unit_ids) == 1:
            return [(unit_ids[0], destination)]

        spacing = float(action.parameters.get("spacing") or self.spacing)
        heading = action.parameters.get("heading")
        facing = heading_vector(float(heading)) if isinstance(heading, int | float) else None
        # Perpendiculaire au cap, dans le plan du terrain.
        lateral = Vector3(facing.z, 0.0, -facing.x) if facing else Vector3(1.0, 0.0, 0.0)

        milieu = (len(unit_ids) - 1) / 2.0
        return [
            (unit_id, destination + lateral.scaled((index - milieu) * spacing))
            for index, unit_id in enumerate(unit_ids)
        ]

    @staticmethod
    def _why(action_type: ActionType) -> str:
        """Pourquoi cette action n'a pas d'equivalent aujourd'hui."""
        besoins = {
            ActionType.ATTACK_TARGET: "necessite uc:attack_unit",
            ActionType.FOCUS_FIRE: "necessite uc:attack_unit",
            ActionType.CHASE_ROUTING: "necessite uc:attack_unit",
            ActionType.PROTECT: "necessite une position d'interception calculee",
            ActionType.FLANK: "necessite une position de contournement calculee",
            ActionType.REORIENT_FRONT: "necessite un ordre d'orientation",
        }
        return besoins.get(action_type, "aucun ordre equivalent disponible")
