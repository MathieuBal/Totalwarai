"""Enregistrement des batailles reellement pilotees dans le jeu.

**Pourquoi ceci existe.** Tout le reglage tactique s'est fait jusqu'ici contre
un simulateur ecrit ici meme. Deux corrections mesurees comme benefiques en jeu
se sont revelees nuisibles au banc, et inversement — sans qu'on puisse dire
lequel des deux juges dit vrai (voir `docs/decisions/0005`). Departager demande
des batailles reelles enregistrees dans le meme format que les simulees.

Ce que l'enregistrement retient, et ce qu'il refuse de pretendre :

* **retenu** : chaque etat observe, chaque ordre emis, chaque refus de securite,
  chaque action perdue faute de traduction, et l'evolution des effectifs ;
* **refuse** : l'issue de la bataille. Le jeu ne la dit pas — nous quittons la
  boucle avant la fin, ou l'operateur reprend la main. Une issue devinee depuis
  les forces restantes polluerait les statistiques d'apprentissage. Elle reste
  `unknown` tant que la phase `Complete` n'a pas ete observee.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.live import LiveStep
from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.memory.models import BattleSummary, Episode

#: Nom de scenario donne aux batailles jouees dans le jeu.
#:
#: Distinct de tout scenario simule, pour qu'une comparaison ne melange jamais
#: les deux sources : `totalwar-ai history --scenario live` les isole.
LIVE_SCENARIO = "live"


@dataclass
class BattleRecorder:
    """Accumule ce qu'une session de pilotage produit, tour apres tour."""

    battle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    directory: Path | None = None
    #: Conserver l'etat de **chaque unite**, a chaque tour.
    #:
    #: Sans cela, une bataille enregistree ne dit que des comptes et des sommes
    #: — dix-huit allies, quatorze de force — et rien de ce que les unites ont
    #: fait. C'est suffisant pour comparer deux issues, et totalement
    #: insuffisant pour apprendre en regardant jouer l'IA du moteur : on ne voit
    #: pas ses decisions.
    #:
    #: Cout mesure : **un mega-octet par bataille**, pour quarante unites et
    #: quatre minutes de combat — soit une trentaine de mega-octets pour le
    #: corpus d'apprentissage vise. Sans l'inventaire ecrit a part, ce serait le
    #: double : le type, le camp et la portee de tir ne changent jamais, et les
    #: repeter a chaque tour pesait plus lourd que tout le reste.
    record_units: bool = True

    #: Un objet JSON par tour, dans l'ordre.
    entries: list[dict[str, Any]] = field(default_factory=list)
    #: Inventaire des unites rencontrees : identifiant -> ce qui ne change pas.
    roster: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Etats publies par le jeu et enregistres. Distinct de `turns`, qui compte
    #: les tours de decision : le jeu publie plus souvent qu'on ne decide.
    observations: int = 0
    orders_sent: int = 0
    orders_blocked: int = 0
    actions_lost: int = 0
    turns: int = 0

    _first_state: ProbeBattleState | None = None
    _last_state: ProbeBattleState | None = None
    _initial_allies: int = 0
    _initial_enemies: int = 0
    _handle: Any = None

    def __post_init__(self) -> None:
        if self.directory is not None:
            base = Path(self.directory)
            base.mkdir(parents=True, exist_ok=True)
            self.path: Path | None = base / f"{self.battle_id}.jsonl"
            self._handle = self.path.open("w", encoding="utf-8")
        else:
            self.path = None

    # --- collecte ------------------------------------------------------------

    def observe(self, step: LiveStep) -> None:
        """Enregistre un tour. Un tour sans etat n'en est pas un.

        **Tous** les etats publies pendant le tour sont enregistres, pas
        seulement celui sur lequel on a decide : le jeu publie plus souvent que
        la boucle ne decide, et un etat jete ne se retrouve jamais.

        Seul l'etat de decision porte les ordres et les motifs — les autres sont
        de pures observations. `turns` continue de compter les decisions, parce
        que c'est ce que l'operateur lit ; `observations` compte les etats.
        """
        if step.state is None:
            return

        self.turns += 1
        self.orders_sent += step.sent
        self.orders_blocked += len(step.blocked)
        self.actions_lost += len(step.translation.untranslated)

        # `observed` est vide chez les appelants qui ne le remplissent pas :
        # on retombe alors sur le seul etat de decision.
        for state in step.observed or (step.state,):
            self._record(state, step if state is step.state else None)

    def _record(self, state: ProbeBattleState, step: LiveStep | None) -> None:
        """Un etat publie. `step` n'est fourni que pour l'etat de decision."""
        self.observations += 1
        if self._first_state is None:
            self._first_state = state
            self._initial_allies = len(state.allies)
            self._initial_enemies = len(state.enemies)
        self._last_state = state

        entry: dict[str, Any] = {
            "turn": self.turns,
            # Le numero de la sonde, distinct de notre compteur de tours. Sans
            # lui un trou dans le flux est indetectable, et rien ne distingue
            # une bataille exploitable d'une bataille trouee.
            "sequence": state.sequence,
            "game_time_ms": state.game_time_ms,
            "phase": state.phase,
            "allies": len(state.allies),
            "enemies": len(state.enemies),
            "ally_strength": _strength(state, "allies"),
            "enemy_strength": _strength(state, "enemies"),
        }
        if step is not None:
            entry.update(
                {
                    # Marque le tour ou l'on a decide. Les autres entrees sont
                    # de pures observations : l'absence d'`orders` y est une
                    # information, pas un oubli.
                    "decision": True,
                    "skipped": step.skipped,
                    "orders": {
                        "moves": [
                            {"unit_id": unit_id, "destination": point.to_dict()}
                            for unit_id, point in step.translation.moves
                        ],
                        "attacks": [attack.to_dict() for attack in step.translation.attacks],
                        "halts": list(step.translation.halts),
                    },
                    "decisions": list(step.decisions),
                    "blocked": list(step.blocked),
                    "untranslated": [
                        {"action": action.value, "reason": reason}
                        for action, reason in step.translation.untranslated
                    ],
                }
            )
        if self.record_units:
            self._refresh_roster(state)
            entry["units"] = [
                _unit_entry(observation)
                for groupe in (state.allies, state.enemies)
                for observation in groupe
            ]
        self.entries.append(entry)
        if self._handle is not None:
            self._handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._handle.flush()

    def _refresh_roster(self, state: ProbeBattleState) -> None:
        """Publie l'inventaire des unites, et le republie si de nouvelles arrivent.

        Une bataille de campagne peut recevoir des renforts en cours de route :
        un inventaire ecrit une fois pour toutes laisserait ces unites sans
        type ni camp, donc inexploitables.
        """
        nouvelles = {
            observation.unit_id: _roster_entry(observation, camp)
            for camp, groupe in (("ally", state.allies), ("enemy", state.enemies))
            for observation in groupe
            if observation.unit_id not in self.roster
        }
        if not nouvelles:
            return
        self.roster.update(nouvelles)
        if self._handle is not None:
            ligne = {"turn": self.turns, "roster": nouvelles}
            self._handle.write(json.dumps(ligne, ensure_ascii=False) + "\n")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    # --- restitution ---------------------------------------------------------

    @property
    def outcome(self) -> BattleOutcomeKind:
        """Issue de la bataille — `unknown` sauf si le jeu l'a annoncee.

        Deviner depuis les forces restantes reviendrait a inventer une donnee
        d'apprentissage. Une session interrompue par l'operateur, ou une armee
        en bonne posture au moment ou l'on cesse d'observer, ne sont pas des
        victoires.
        """
        if self._last_state is None or self._last_state.phase != "Complete":
            return BattleOutcomeKind.UNKNOWN

        # **Seules les unites vivantes comptent** — precaution, non correction
        # d'un defaut constate. La premiere bataille menee a son terme a bien
        # renvoye `victory` : a la phase `Complete`, le camp vaincu ne figurait
        # plus du tout dans les listes du jeu. Rien ne garantit qu'il en aille
        # toujours ainsi, et une unite detruite mais encore listee ferait
        # basculer un aneantissement en match nul.
        allies = [unite for unite in self._last_state.allies if unite.alive]
        enemies = [unite for unite in self._last_state.enemies if unite.alive]
        if not enemies and allies:
            return BattleOutcomeKind.VICTORY
        if not allies and enemies:
            return BattleOutcomeKind.DEFEAT
        return BattleOutcomeKind.DRAW

    def summary(self) -> BattleSummary:
        """Fiche comparable a celle d'une bataille simulee."""
        debut = self._first_state.game_time_ms if self._first_state else 0
        fin = self._last_state.game_time_ms if self._last_state else 0
        return BattleSummary(
            battle_id=self.battle_id,
            scenario=LIVE_SCENARIO,
            outcome=self.outcome,
            duration=max(0.0, (fin - debut) / 1000.0),
            ally_remaining=_final_share(self._last_state, "allies", self._initial_allies),
            enemy_remaining=_final_share(self._last_state, "enemies", self._initial_enemies),
            actions_sent=self.orders_sent,
            actions_blocked=self.orders_blocked,
            agent_mode="deterministic-live",
            army_fingerprint=_fingerprint(self._first_state),
            created_at=time.time(),
            metrics={
                "turns": self.turns,
                "actions_lost": self.actions_lost,
                "source": "game",
                "initial_allies": self._initial_allies,
                "initial_enemies": self._initial_enemies,
                "final_phase": self._last_state.phase if self._last_state else "",
            },
        )

    def episode(self) -> Episode:
        """Episode enregistrable en memoire.

        **Sans transitions ni recompenses.** Le calcul de recompense repose sur
        des evenements que le jeu ne fournit pas — qui a tire sur qui, quelles
        pertes a produit quel ordre. Les fabriquer ferait entrer du bruit dans
        la memoire d'apprentissage sous les traits d'une mesure.
        """
        return Episode(summary=self.summary())


#: Precision des coordonnees enregistrees, en decimales.
#:
#: Le metre suffit largement pour retrouver qui allait vers qui, et trois
#: decimales par unite et par tour multipliees par trente batailles font du
#: volume pour rien.
_POSITION_PRECISION = 1


def _roster_entry(observation: ProbeUnitObservation, side: str) -> dict[str, Any]:
    """Ce qui ne change pas d'un tour a l'autre : ecrit une fois, pas deux cents.

    Repeter le type, le camp et la portee de tir a chaque tour multipliait par
    trois le poids d'une bataille — deux mega-octets et demi la ou huit cents
    kilo-octets suffisent, et soixante-dix mega-octets pour un corpus de trente.
    """
    entry: dict[str, Any] = {"side": side, "type": observation.unit_type}
    if observation.commanding:
        entry["commanding"] = True
    if observation.can_fly:
        entry["can_fly"] = True
    if observation.missile_range is not None:
        entry["missile_range"] = observation.missile_range
    return entry


def _unit_entry(observation: ProbeUnitObservation) -> dict[str, Any]:
    """Ce qui bouge : position et condition, tour par tour.

    **Un champ absent veut dire que le jeu ne l'expose pas**, et jamais zero —
    c'est la meme regle que dans le protocole. Un moral a zero se confondrait
    avec une unite qui rompt ; une munition a zero, avec un carquois vide.
    """
    entry: dict[str, Any] = {
        "id": observation.unit_id,
        "x": round(observation.position.x, _POSITION_PRECISION),
        # **L'altitude est la seule donnee de terrain que le jeu nous donne.**
        # `position():get_y()` repond — verifie en bataille, entre 21 et 33 — et
        # elle etait jetee. Elle dit tout de suite qui tient la hauteur, et,
        # accumulee sur des dizaines de batailles, elle dessine le relief des
        # cartes deja jouees.
        "y": round(observation.position.y, _POSITION_PRECISION),
        "z": round(observation.position.z, _POSITION_PRECISION),
    }
    # Les booleens ne sont ecrits que lorsqu'ils sont vrais : sur une bataille
    # entiere, la plupart des unites ne sont ni au contact, ni en deroute, ni
    # cachees, et quatre `false` par ligne pesent plus que l'information.
    if not observation.alive:
        entry["dead"] = True
    if observation.in_melee:
        entry["in_melee"] = True
    if observation.idle:
        entry["idle"] = True
    if observation.routing or observation.shattered:
        entry["routing"] = True
    if observation.hidden:
        entry["hidden"] = True
    if observation.hitpoints is not None:
        entry["hitpoints"] = round(observation.hitpoints, 3)
    if observation.men_alive is not None:
        entry["men_alive"] = observation.men_alive
    if observation.bearing is not None:
        entry["bearing"] = round(observation.bearing, 1)
    if observation.ammo is not None:
        entry["ammo"] = observation.ammo
    return entry


def _strength(state: ProbeBattleState, side: str) -> float:
    """Somme des sante des unites vivantes d'un camp, ou leur nombre a defaut."""
    unites = [unite for unite in getattr(state, side) if unite.alive]
    if not unites:
        return 0.0
    connues = [unite.hitpoints for unite in unites if unite.hitpoints is not None]
    return sum(connues) if connues else float(len(unites))


def _final_share(state: ProbeBattleState | None, side: str, initial: int) -> float:
    """Part d'un camp encore debout, dans [0, 1]."""
    if state is None or initial <= 0:
        return 0.0
    vivantes = sum(1 for unite in getattr(state, side) if unite.alive)
    return min(1.0, vivantes / initial)


def _fingerprint(state: ProbeBattleState | None) -> str:
    """Empreinte de composition, pour retrouver les batailles semblables."""
    if state is None:
        return ""
    return "|".join(f"{camp}:{len(getattr(state, camp))}" for camp in ("allies", "enemies"))
