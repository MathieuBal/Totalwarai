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

from totalwar_ai.bridge.command_models import ProbeAttack
from totalwar_ai.domain.actions import ActionType, AgentAction
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3, heading_vector
from totalwar_ai.domain.unit_state import Side

#: Espacement lateral par defaut entre deux unites d'une meme ligne, en metres.
#:
#: Le jeu ne donne pas la largeur de front d'une unite (`width` est absent du bac
#: a sable), impossible donc de calculer un espacement juste. Trente metres
#: laissent passer une unite d'infanterie sans chevauchement visible.
DEFAULT_SPACING = 30.0

#: Distance a laquelle une unite contourne sa cible, en metres.
#:
#: Assez large pour que le moteur ne rabatte pas l'unite droit sur l'ennemi —
#: un contournement trop serre se termine en charge frontale — assez court pour
#: que la manoeuvre aboutisse avant que la cible n'ait bouge.
FLANK_OFFSET = 70.0

#: Fraction du trajet menace -> protege ou se place l'intercepteur.
#:
#: Au-dela de la moitie : l'interception doit avoir lieu **avant** le contact,
#: donc plus pres de la menace que du protege.
INTERCEPT_RATIO = 0.65


@dataclass(frozen=True, slots=True)
class Translation:
    """Ce qu'une decision de l'agent devient, cote jeu."""

    #: `(identifiant, destination)` prets pour `FileBridge.send_orders`.
    moves: tuple[tuple[str, Vector3], ...] = ()
    #: Attaques pretes pour `FileBridge.send_orders`.
    attacks: tuple[ProbeAttack, ...] = ()
    #: Unites a immobiliser.
    halts: tuple[str, ...] = ()
    #: Actions qu'aucun ordre disponible ne sait rendre, avec leur motif.
    untranslated: tuple[tuple[ActionType, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.moves and not self.attacks and not self.halts

    @property
    def order_count(self) -> int:
        return len(self.moves) + len(self.attacks) + len(self.halts)


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
        attacks: list[ProbeAttack] = []
        halts: list[str] = []
        untranslated: list[tuple[ActionType, str]] = []
        deja_ordonnees: set[str] = set()

        for action in actions:
            if action.type in ATTACK_ACTIONS:
                refus = self._as_attacks(action, state, deja_ordonnees, attacks)
                if refus is not None:
                    untranslated.append((action.type, refus))
                continue

            if action.type is ActionType.FLANK:
                destination = self._flank_position(action, state)
            elif action.type is ActionType.PROTECT:
                destination = self._intercept_position(action, state)
            elif action.type is ActionType.HOLD_POSITION:
                # Ne rien envoyer laissait l'unite poursuivre ce qu'elle
                # faisait : l'agent croyait tenir sa ligne pendant que l'armee
                # continuait d'avancer. Une unite deja immobile n'a en revanche
                # pas besoin qu'on l'arrete — d'ou la lecture de `is_idle`.
                for unit_id in action.actor_ids:
                    unite = state.unit(unit_id)
                    if unite is None or unit_id in deja_ordonnees:
                        continue
                    if unite.metadata.get("idle", True):
                        continue
                    deja_ordonnees.add(unit_id)
                    halts.append(unit_id)
                continue
            else:
                key = self.destination_keys.get(action.type)
                if key is None:
                    untranslated.append((action.type, self._why(action.type)))
                    continue
                candidate = action.parameters.get(key)
                destination = candidate if isinstance(candidate, Vector3) else None
                if destination is None:
                    untranslated.append((action.type, f"parametre '{key}' absent ou invalide"))
                    continue

            if destination is None:
                untranslated.append((action.type, self._why(action.type)))
                continue

            for unit_id, point in self._spread(action, destination, state):
                # Une unite ne peut suivre qu'un ordre : le premier emis gagne,
                # les actions etant deja classees par priorite par l'agent.
                if unit_id not in deja_ordonnees:
                    deja_ordonnees.add(unit_id)
                    moves.append((unit_id, point))

        return Translation(
            moves=tuple(moves),
            attacks=tuple(attacks),
            halts=tuple(halts),
            untranslated=tuple(untranslated),
        )

    def _as_attacks(
        self,
        action: AgentAction,
        state: BattleState,
        deja_ordonnees: set[str],
        sortie: list[ProbeAttack],
    ) -> str | None:
        """Traduit une action d'engagement. Renvoie le motif d'un refus.

        Le corps a corps n'est force que pour les actions qui le demandent
        explicitement : `FOCUS_FIRE` designe un tir concentre, et imposer la
        melee a un tireur lui ferait perdre son avantage.
        """
        target_id = action.parameters.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            return "parametre 'target_id' absent ou invalide"
        if state.unit(target_id) is None:
            return f"cible {target_id} absente de l'etat"

        melee = action.type is not ActionType.FOCUS_FIRE
        vues = 0
        for unit_id in action.actor_ids:
            if state.unit(unit_id) is None or unit_id in deja_ordonnees:
                continue
            deja_ordonnees.add(unit_id)
            sortie.append(ProbeAttack(unit_id=unit_id, target_id=target_id, melee=melee))
            vues += 1
        return None if vues else "aucune unite disponible"

    def _flank_position(self, action: AgentAction, state: BattleState) -> Vector3 | None:
        """Point d'ou contourner une cible, sur son flanc.

        La manoeuvre se calcule dans le repere de l'affrontement : l'axe qui
        va de notre armee vers la cible, et sa perpendiculaire. On se porte sur
        le cote demande, a `FLANK_OFFSET` de la cible, et **legerement au-dela**
        d'elle — s'arreter a sa hauteur reviendrait a l'aborder de face.

        Renvoie `None` quand la cible a disparu : mieux vaut ne rien ordonner
        que d'envoyer la cavalerie sur un souvenir.
        """
        target_id = action.parameters.get("target_id")
        target = state.unit(target_id) if isinstance(target_id, str) else None
        if target is None:
            return None

        axe = state.centroid(Side.ALLY).direction_to(target.position)
        if axe.length() == 0.0:
            return None
        lateral = Vector3(axe.z, 0.0, -axe.x)
        # `side` vaut "left" ou "right" ; toute autre valeur passe a droite,
        # ce qui reste une manoeuvre valide plutot qu'un refus.
        signe = -1.0 if str(action.parameters.get("side", "")).lower() == "left" else 1.0
        return target.position + lateral.scaled(signe * FLANK_OFFSET) + axe.scaled(FLANK_OFFSET / 2)

    def _intercept_position(self, action: AgentAction, state: BattleState) -> Vector3 | None:
        """Point ou couper la route a la menace la plus proche d'un protege.

        Se porter sur le protege lui-meme ne le protege pas : l'escorte
        arriverait derriere lui, la menace atteignant le protege en meme temps.
        On se place donc **sur le trajet**, aux deux tiers du chemin vers la
        menace, pour l'accrocher avant le contact.
        """
        proteges = action.parameters.get("protected_ids")
        if not isinstance(proteges, list | tuple) or not proteges:
            return None
        protege = state.unit(str(proteges[0]))
        if protege is None:
            return None

        menaces = state.enemies()
        if not menaces:
            return None
        menace = min(menaces, key=lambda enemy: protege.position.distance_2d(enemy.position))
        ecart = menace.position - protege.position
        return protege.position + ecart.scaled(INTERCEPT_RATIO)

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
            ActionType.PROTECT: "protege ou menace introuvable",
            ActionType.FLANK: "cible introuvable",
            ActionType.REORIENT_FRONT: "necessite un ordre d'orientation",
        }
        return besoins.get(action_type, "aucun ordre equivalent disponible")


#: Actions rendues par un ordre d'attaque.
#:
#: Toutes designent une cible via `target_id`. Elles different par le mode :
#: `FOCUS_FIRE` veut un tir concentre, les autres un engagement au contact.
ATTACK_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.ATTACK_TARGET,
        ActionType.FOCUS_FIRE,
        ActionType.CHASE_ROUTING,
    }
)
