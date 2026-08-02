"""Messages du prototype d'integration au jeu.

Ce protocole est **volontairement plus pauvre** que celui de
:mod:`totalwar_ai.bridge.protocol` : il ne transporte qu'une unite et un ordre
de deplacement. C'est une sonde de faisabilite, pas l'adaptateur final — son
seul but est de prouver l'aller-retour jeu <-> Python sur le plus petit
perimetre possible.

Les deux protocoles cohabitent tant que la sonde n'a pas repondu a la question
« que peut-on reellement observer et commander ? ». `ProbeUnitState.to_unit_state`
montre le raccord vers le domaine complet, une fois cette reponse obtenue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from totalwar_ai.bridge.protocol import PROTOCOL_VERSION, check_version
from totalwar_ai.domain.battle_state import BattlePhase, BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.serialization import (
    SchemaError,
    as_bool,
    as_enum,
    as_int,
    as_optional_str,
    as_str,
    require_mapping,
)
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState

#: Precision des coordonnees ecrites vers le Lua, en decimales.
#:
#: L'analyseur du script Lua lit les nombres avec le motif `(-?%d+%.?%d*)`, qui
#: ne comprend pas la notation scientifique : `1e-05` y serait lu comme `1`,
#: silencieusement et faussement. Arrondir avant d'ecrire elimine le cas —
#: le millimetre est de toute facon sans objet sur un champ de bataille.
COORDINATE_PRECISION = 3


def jsonable_vector(vector: Vector3) -> dict[str, float]:
    """Vecteur serialisable sans risque de notation scientifique."""
    return {
        "x": round(vector.x, COORDINATE_PRECISION),
        "y": round(vector.y, COORDINATE_PRECISION),
        "z": round(vector.z, COORDINATE_PRECISION),
    }


class ProbeMessageType(StrEnum):
    """Types de messages echanges par la sonde."""

    UNIT_STATE = "unit_state"
    #: Bataille entiere : toutes les unites alliees, et tout ce qui ne l'est pas.
    BATTLE_STATE = "battle_state"
    MOVE_UNIT = "move_unit"
    #: Deplacement groupe : une armee se deploie d'un bloc ou pas du tout.
    MOVE_UNITS = "move_units"
    ACTION_RESULT = "action_result"
    #: Ordre d'arret : le Lua libere toutes ses unites et cesse de lire.
    ABORT = "abort"


class ProbeStatus(StrEnum):
    """Sort d'une commande, du point de vue du Lua."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class ProbeUnitState:
    """Etat d'une unite, tel que le Lua l'observe."""

    unit_id: str
    position: Vector3
    unit_type: str = ""
    controllable: bool = False
    sequence: int = 0
    game_time_ms: int = 0
    #: Phase de bataille annoncee par le jeu au moment de l'observation.
    #: Vide si la sonde ne l'a pas encore transmise (protocole anterieur).
    phase: str = ""
    protocol_version: str = PROTOCOL_VERSION

    @property
    def orders_take_effect(self) -> bool:
        """Un ordre emis maintenant peut-il produire un deplacement ?

        Avant `Deployed`, le moteur accepte l'ordre et l'acquitte, mais l'unite
        ne bouge pas — constate en jeu, immobile 33 s durant apres un ordre
        accepte. Une phase inconnue est traitee comme jouable : c'est le cas
        d'une sonde plus ancienne, ou l'on ne veut pas bloquer a tort.
        """
        return self.phase in ("", "unknown", "Deployed", "Complete")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.UNIT_STATE.value,
            "sequence": self.sequence,
            "game_time_ms": self.game_time_ms,
            "phase": self.phase,
            "unit": {
                "id": self.unit_id,
                "type": self.unit_type,
                "position": jsonable_vector(self.position),
                "controllable": self.controllable,
            },
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeUnitState:
        data = require_mapping(raw, "ProbeUnitState")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.UNIT_STATE)
        unit = require_mapping(data.get("unit"), "unit")
        return cls(
            unit_id=as_str(unit, "id"),
            position=Vector3.from_dict(unit.get("position", {})),
            unit_type=as_str(unit, "type", default=""),
            controllable=as_bool(unit, "controllable", default=False),
            sequence=as_int(data, "sequence", default=0),
            game_time_ms=as_int(data, "game_time_ms", default=0),
            phase=as_str(data, "phase", default=""),
            protocol_version=version,
        )

    def to_unit_state(self) -> UnitState:
        """Traduction vers le domaine complet.

        La sonde n'observe qu'une fraction de `UnitState` ; le reste garde ses
        valeurs par defaut. C'est ce raccord qui permettra a l'agent existant de
        consommer un jour les donnees du vrai jeu sans etre modifie.
        """
        return UnitState(
            id=self.unit_id,
            side=Side.ALLY,
            role=UnitRole.UNKNOWN,
            position=self.position,
            unit_key=self.unit_type,
            metadata={"controllable": self.controllable, "source": "probe"},
        )


@dataclass(frozen=True, slots=True)
class ProbeUnitObservation:
    """Une unite vue par la sonde, au sein d'un etat de bataille.

    **Les champs optionnels valent `None` quand le jeu ne les expose pas.**
    C'est deliberement different de zero : le recensement mene en bataille a
    montre que `unary_morale` et toutes les formes de fatigue sont absentes du
    bac a sable Lua. Un moral a zero se confondrait avec une unite qui rompt.
    """

    unit_id: str
    position: Vector3
    unit_type: str = ""
    controllable: bool = False
    alive: bool = True
    routing: bool = False
    shattered: bool = False
    in_melee: bool = False
    hidden: bool = False
    can_fly: bool = False
    #: Sante normalisee 0–1. `None` si le jeu ne la donne pas.
    hitpoints: float | None = None
    men_alive: int | None = None
    bearing: float | None = None
    ammo: int | None = None
    missile_range: float | None = None

    @property
    def is_ranged(self) -> bool:
        """Unite capable de tirer, d'apres ce que le jeu expose.

        `missile_range` vaut 0 sur une unite de melee — verifie en bataille sur
        un prince demon. Sans la donnee, on ne suppose rien.
        """
        return self.missile_range is not None and self.missile_range > 0.0

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeUnitObservation:
        data = require_mapping(raw, "ProbeUnitObservation")
        return cls(
            unit_id=as_str(data, "id"),
            position=Vector3.from_dict(require_mapping(data.get("position"), "position")),
            unit_type=as_str(data, "type", default=""),
            controllable=as_bool(data, "controllable", default=False),
            alive=as_bool(data, "alive", default=True),
            routing=as_bool(data, "routing", default=False),
            shattered=as_bool(data, "shattered", default=False),
            in_melee=as_bool(data, "in_melee", default=False),
            hidden=as_bool(data, "hidden", default=False),
            can_fly=as_bool(data, "can_fly", default=False),
            hitpoints=_optional_float(data, "hitpoints"),
            men_alive=_optional_int(data, "men_alive"),
            bearing=_optional_float(data, "bearing"),
            ammo=_optional_int(data, "ammo"),
            missile_range=_optional_float(data, "missile_range"),
        )

    def to_unit_state(self, side: Side) -> UnitState:
        """Traduction vers le domaine de l'agent.

        Le role reste `UNKNOWN` : c'est au classifieur de le deduire de la cle
        d'unite, en appliquant les regles de `config/unit_roles.yaml`.
        """
        metadata: dict[str, Any] = {
            "source": "probe",
            "controllable": self.controllable,
            "shattered": self.shattered,
            # Le jeu n'expose ni le moral ni la fatigue : `UnitState` les laisse
            # a 50 et 0, valeurs qui ressemblent a des mesures. On marque donc
            # explicitement qu'elles n'en sont pas — toute regle de l'agent qui
            # s'en sert doit d'abord verifier ces drapeaux.
            "morale_available": False,
            "fatigue_available": False,
        }
        for name in ("hitpoints", "men_alive", "bearing", "ammo", "missile_range"):
            value = getattr(self, name)
            if value is not None:
                metadata[name] = value
        return UnitState(
            id=self.unit_id,
            side=side,
            role=UnitRole.UNKNOWN,
            position=self.position,
            heading=self.bearing if self.bearing is not None else 0.0,
            unit_key=self.unit_type,
            health_ratio=self.hitpoints if self.hitpoints is not None else 1.0,
            # `number_of_men` est absent du bac a sable : impossible de calculer
            # un vrai rapport d'effectifs. Laisser `entity_ratio` a 1 fausserait
            # `effective_strength`, qui multiplie les deux — une unite exsangue
            # y vaudrait encore 0,5. `unary_hitpoints` agrege la sante de toutes
            # les entites d'une unite : c'est l'approximation la plus proche.
            entity_ratio=self.hitpoints if self.hitpoints is not None else 1.0,
            is_routing=self.routing or self.shattered,
            is_engaged=self.in_melee,
            is_hidden=self.hidden,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class ProbeBattleState:
    """La bataille entiere, telle que la sonde la voit a un instant donne."""

    allies: tuple[ProbeUnitObservation, ...] = ()
    enemies: tuple[ProbeUnitObservation, ...] = ()
    sequence: int = 0
    game_time_ms: int = 0
    phase: str = ""
    protocol_version: str = PROTOCOL_VERSION

    @property
    def orders_take_effect(self) -> bool:
        """Voir `ProbeUnitState.orders_take_effect`."""
        return self.phase in ("", "unknown", "Deployed", "Complete")

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeBattleState:
        data = require_mapping(raw, "ProbeBattleState")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.BATTLE_STATE)
        return cls(
            allies=tuple(ProbeUnitObservation.from_dict(item) for item in _as_list(data, "allies")),
            enemies=tuple(
                ProbeUnitObservation.from_dict(item) for item in _as_list(data, "enemies")
            ),
            sequence=as_int(data, "sequence", default=0),
            game_time_ms=as_int(data, "game_time_ms", default=0),
            phase=as_str(data, "phase", default=""),
            protocol_version=version,
        )

    def to_battle_state(self, battle_id: str = "live") -> BattleState:
        """Etat de bataille consommable par l'agent existant.

        Les unites mortes sont ecartees : l'agent n'a rien a decider a leur
        sujet, et les garder fausserait les barycentres et rapports de force.
        """
        units = [
            observation.to_unit_state(side)
            for side, group in ((Side.ALLY, self.allies), (Side.ENEMY, self.enemies))
            for observation in group
            if observation.alive
        ]
        return BattleState(
            battle_id=battle_id,
            sequence=self.sequence,
            game_time=self.game_time_ms / 1000.0,
            phase=_domain_phase(self.phase),
            units=tuple(units),
            metadata={"game_phase": self.phase, "source": "probe"},
        )


@dataclass(frozen=True, slots=True)
class ProbeMoveCommand:
    """Ordre de deplacement envoye par Python."""

    unit_id: str
    destination: Vector3
    sequence: int = 1
    #: Duree au bout de laquelle le Lua rend la main, meme si l'unite marche encore.
    release_after_ms: int = 5000
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not self.unit_id:
            raise SchemaError("Une commande de deplacement doit designer une unite")
        if self.sequence < 1:
            raise SchemaError("Le numero de sequence doit etre superieur ou egal a 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.MOVE_UNIT.value,
            "sequence": self.sequence,
            "unit_id": self.unit_id,
            "destination": jsonable_vector(self.destination),
            "release_after_ms": self.release_after_ms,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeMoveCommand:
        data = require_mapping(raw, "ProbeMoveCommand")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.MOVE_UNIT)
        return cls(
            unit_id=as_str(data, "unit_id"),
            destination=Vector3.from_dict(require_mapping(data.get("destination"), "destination")),
            sequence=as_int(data, "sequence", default=1),
            release_after_ms=as_int(data, "release_after_ms", default=5000),
            protocol_version=version,
        )


@dataclass(frozen=True, slots=True)
class ProbeMoveGroupCommand:
    """Ordre de deplacement portant sur plusieurs unites a la fois.

    Un ordre par unite obligerait a autant d'allers-retours par fichier, chacun
    avec sa sequence et son accuse : deplacer vingt unites prendrait vingt
    secondes, et l'armee arriverait en file indienne. Chaque unite garde sa
    propre destination — c'est ce qui distingue une formation d'un troupeau.
    """

    #: `(identifiant, destination)`, dans l'ordre voulu par l'appelant.
    moves: tuple[tuple[str, Vector3], ...]
    sequence: int = 1
    release_after_ms: int = 5000
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not self.moves:
            raise SchemaError("Un deplacement de groupe doit porter sur au moins une unite")
        if self.sequence < 1:
            raise SchemaError("Le numero de sequence doit etre superieur ou egal a 1")
        if any(not unit_id for unit_id, _ in self.moves):
            raise SchemaError("Chaque deplacement doit designer une unite")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.MOVE_UNITS.value,
            "sequence": self.sequence,
            "release_after_ms": self.release_after_ms,
            "moves": [
                {"unit_id": unit_id, "destination": jsonable_vector(destination)}
                for unit_id, destination in self.moves
            ],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeMoveGroupCommand:
        data = require_mapping(raw, "ProbeMoveGroupCommand")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.MOVE_UNITS)
        moves = []
        for item in _as_list(data, "moves"):
            entry = require_mapping(item, "move")
            moves.append(
                (
                    as_str(entry, "unit_id"),
                    Vector3.from_dict(require_mapping(entry.get("destination"), "destination")),
                )
            )
        return cls(
            moves=tuple(moves),
            sequence=as_int(data, "sequence", default=1),
            release_after_ms=as_int(data, "release_after_ms", default=5000),
            protocol_version=version,
        )


@dataclass(frozen=True, slots=True)
class ProbeAbortCommand:
    """Arret d'urgence : le Lua libere tout et cesse de lire les commandes."""

    sequence: int = 1
    reason: str = ""
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.ABORT.value,
            "sequence": self.sequence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeAbortCommand:
        data = require_mapping(raw, "ProbeAbortCommand")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.ABORT)
        return cls(
            sequence=as_int(data, "sequence", default=1),
            reason=as_str(data, "reason", default=""),
            protocol_version=version,
        )


@dataclass(frozen=True, slots=True)
class ProbeAck:
    """Accuse d'execution renvoye par le Lua."""

    sequence: int
    status: ProbeStatus
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    protocol_version: str = PROTOCOL_VERSION

    @property
    def accepted(self) -> bool:
        return self.status in (ProbeStatus.ACCEPTED, ProbeStatus.COMPLETED, ProbeStatus.RELEASED)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "type": ProbeMessageType.ACTION_RESULT.value,
            "sequence": self.sequence,
            "status": self.status.value,
            "error": self.error,
        }
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> ProbeAck:
        data = require_mapping(raw, "ProbeAck")
        version = as_str(data, "protocol_version")
        check_version(version)
        _require_type(data, ProbeMessageType.ACTION_RESULT)
        return cls(
            sequence=as_int(data, "sequence"),
            status=as_enum(data, "status", ProbeStatus),
            error=as_optional_str(data, "error"),
            detail=dict(data.get("detail") or {}),
            protocol_version=version,
        )


ProbeCommand = ProbeMoveCommand | ProbeMoveGroupCommand | ProbeAbortCommand


def decode_command(raw: Any) -> ProbeCommand:
    """Decode une commande quelconque en s'appuyant sur son champ `type`."""
    data = require_mapping(raw, "commande")
    message_type = as_str(data, "type")
    if message_type == ProbeMessageType.MOVE_UNIT.value:
        return ProbeMoveCommand.from_dict(data)
    if message_type == ProbeMessageType.MOVE_UNITS.value:
        return ProbeMoveGroupCommand.from_dict(data)
    if message_type == ProbeMessageType.ABORT.value:
        return ProbeAbortCommand.from_dict(data)
    raise SchemaError(f"Type de commande inconnu : {message_type!r}")


def _require_type(data: Any, expected: ProbeMessageType) -> None:
    actual = as_str(data, "type")
    if actual != expected.value:
        raise SchemaError(f"Message de type {actual!r} la ou {expected.value!r} etait attendu")


def _as_list(data: Any, key: str) -> list[Any]:
    """Liste sous une cle, tolerante a son absence."""
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise SchemaError(f"Le champ '{key}' doit etre une liste")
    return value


def _optional_float(data: Any, key: str) -> float | None:
    """Nombre optionnel : absent signifie « le jeu ne l'expose pas »."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"Le champ '{key}' doit etre un nombre")
    return float(value)


def _optional_int(data: Any, key: str) -> int | None:
    value = _optional_float(data, key)
    return None if value is None else int(value)


#: Correspondance entre les phases annoncees par le jeu et celles du domaine.
#:
#: Le jeu en compte davantage, et son `Deployed` marque le debut du combat, pas
#: l'engagement : c'est donc `approach`. L'agent affinera ensuite lui-meme
#: d'apres ce qu'il observe — le jeu ne dit pas quand la melee commence.
_GAME_PHASES: dict[str, BattlePhase] = {
    "Startup": BattlePhase.DEPLOYMENT,
    "PrebattleWeather": BattlePhase.DEPLOYMENT,
    "PrebattleCinematic": BattlePhase.DEPLOYMENT,
    "Deployment": BattlePhase.DEPLOYMENT,
    "Deployed": BattlePhase.APPROACH,
    "VictoryCountdown": BattlePhase.ENGAGEMENT,
    "Complete": BattlePhase.FINISHED,
}


def _domain_phase(game_phase: str) -> BattlePhase:
    """Phase du domaine, `deployment` par defaut faute de mieux."""
    return _GAME_PHASES.get(game_phase, BattlePhase.DEPLOYMENT)
