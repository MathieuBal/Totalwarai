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

from totalwar_ai.bridge.command_models import ProbeBattleState
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

    #: Un objet JSON par tour, dans l'ordre.
    entries: list[dict[str, Any]] = field(default_factory=list)
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
        """Enregistre un tour. Un tour sans etat n'en est pas un."""
        if step.state is None:
            return

        self.turns += 1
        if self._first_state is None:
            self._first_state = step.state
            self._initial_allies = len(step.state.allies)
            self._initial_enemies = len(step.state.enemies)
        self._last_state = step.state

        self.orders_sent += step.sent
        self.orders_blocked += len(step.blocked)
        self.actions_lost += len(step.translation.untranslated)

        entry = {
            "turn": self.turns,
            "game_time_ms": step.state.game_time_ms,
            "phase": step.state.phase,
            "allies": len(step.state.allies),
            "enemies": len(step.state.enemies),
            "ally_strength": _strength(step.state, "allies"),
            "enemy_strength": _strength(step.state, "enemies"),
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
        self.entries.append(entry)
        if self._handle is not None:
            self._handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._handle.flush()

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
        if not self._last_state.enemies:
            return BattleOutcomeKind.VICTORY
        if not self._last_state.allies:
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
