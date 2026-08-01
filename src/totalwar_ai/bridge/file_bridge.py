"""Pont par fichiers locaux entre Python et le Lua de bataille.

Trois fichiers, trois roles, une seule direction chacun :

===========================  =========  ========  ============================
fichier                      ecrit par  lu par    forme
===========================  =========  ========  ============================
`totalwar_ai_state.jsonl`    Lua        Python    une ligne JSON par etat
`totalwar_ai_command.json`   Python     Lua       un objet JSON, remplace
`totalwar_ai_ack.jsonl`      Lua        Python    une ligne JSON par accuse
===========================  =========  ========  ============================

Deux precautions gouvernent toute la conception :

* **Ecriture atomique.** Python ecrit la commande dans un fichier temporaire,
  le ferme, puis le publie avec :func:`os.replace`. Le Lua ne peut donc jamais
  lire une commande a moitie ecrite — il voit l'ancienne ou la nouvelle.
* **Lecture tolerante.** Une ligne tronquee cote Lua (ecriture interrompue par
  un plantage, par exemple) est ignoree, pas fatale. Le flux continue.

Ce pont n'implemente volontairement pas :class:`totalwar_ai.bridge.base.Bridge` :
il parle le protocole reduit de la sonde, pas celui de l'agent complet. Le jour
ou la sonde aura repondu, l'adaptateur definitif pourra hériter de `Bridge`.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from totalwar_ai.bridge.command_models import (
    ProbeAbortCommand,
    ProbeAck,
    ProbeCommand,
    ProbeMoveCommand,
    ProbeUnitState,
)
from totalwar_ai.bridge.paths import BridgePaths
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.serialization import SchemaError

LOGGER = logging.getLogger("totalwar_ai.bridge.file")


@dataclass(frozen=True, slots=True)
class MalformedLine:
    """Ligne illisible rencontree dans un flux, conservee pour diagnostic."""

    path: Path
    line_number: int
    content: str
    error: str


@dataclass
class FileBridge:
    """Cote Python du pont par fichiers.

    Le pont retient sa position de lecture dans chaque flux append-only : deux
    appels successifs ne relivrent pas les memes etats.
    """

    paths: BridgePaths
    #: Lignes illisibles rencontrees, exposees pour la telemetrie.
    malformed: list[MalformedLine] = field(default_factory=list)
    _state_offset: int = 0
    _ack_offset: int = 0
    _next_sequence: int = 1
    #: Taille du fichier d'accuses au moment de la derniere commande publiee.
    _ack_watermark: int = 0

    @classmethod
    def open(cls, directory: str | Path | None = None, *, create: bool = True) -> FileBridge:
        """Ouvre le pont sur un repertoire d'echange.

        Le compteur de sequence **reprend** ou la session precedente s'est
        arretee. Sans cela, chaque processus repartirait de 1 et le Lua, qui
        refuse a juste titre une sequence deja traitee, rejetterait toutes les
        commandes suivant la premiere — defaut constate en bataille reelle.
        """
        paths = BridgePaths.resolve(directory, create=create)
        bridge = cls(paths=paths)
        bridge._next_sequence = _highest_sequence_on_disk(paths) + 1
        return bridge

    # --- Python -> Lua -------------------------------------------------------

    def send_command(self, command: ProbeCommand) -> Path:
        """Publie une commande de facon atomique.

        Le fichier temporaire est cree **dans le repertoire de destination** :
        `os.replace` n'est atomique qu'au sein d'un meme systeme de fichiers.
        """
        self.paths.ensure()
        payload = json.dumps(command.to_dict(), ensure_ascii=False, indent=2) + "\n"

        # Un accuse ecrit *avant* la commande ne peut pas etre le sien. On note
        # la taille du flux pour que `wait_for_ack` ne prenne pas un vieil
        # accuse pour une reponse — c'est ainsi que le CLI a annonce `accepted`
        # pour une commande que le Lua avait refusee.
        self._ack_watermark = self.paths.ack.stat().st_size if self.paths.ack.exists() else 0

        descriptor, raw_path = tempfile.mkstemp(
            dir=self.paths.directory, prefix=".totalwar_ai_command-", suffix=".tmp"
        )
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.paths.command)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

        self._next_sequence = max(self._next_sequence, command.sequence + 1)
        LOGGER.debug("commande publiee : %s", payload.strip())
        return self.paths.command

    def move_unit(
        self,
        unit_id: str,
        destination: Vector3,
        *,
        sequence: int | None = None,
        release_after_ms: int = 5000,
    ) -> ProbeMoveCommand:
        """Raccourci : construit et publie un ordre de deplacement."""
        command = ProbeMoveCommand(
            unit_id=unit_id,
            destination=destination,
            sequence=sequence if sequence is not None else self._next_sequence,
            release_after_ms=release_after_ms,
        )
        self.send_command(command)
        return command

    def abort(self, reason: str = "arret demande par l'operateur") -> ProbeAbortCommand:
        """Arret d'urgence.

        Deux canaux volontairement redondants : une commande `abort`, et un
        fichier sentinelle. Si le Lua n'arrive plus a analyser les commandes, la
        seule presence du fichier suffit a le faire tout relacher.
        """
        self.paths.ensure()
        command = ProbeAbortCommand(sequence=self._next_sequence, reason=reason)
        self.paths.stop.write_text(reason + "\n", encoding="utf-8")
        self.send_command(command)
        return command

    def clear_stop(self) -> None:
        """Retire la sentinelle d'arret."""
        self.paths.stop.unlink(missing_ok=True)

    @property
    def stop_requested(self) -> bool:
        return self.paths.stop.exists()

    # --- Lua -> Python -------------------------------------------------------

    def read_states(self) -> list[ProbeUnitState]:
        """Nouveaux etats depuis le dernier appel."""
        return [
            ProbeUnitState.from_dict(payload)
            for payload in self._read_new_lines(self.paths.state, "state")
        ]

    def read_acks(self) -> list[ProbeAck]:
        """Nouveaux accuses depuis le dernier appel."""
        return [
            ProbeAck.from_dict(payload) for payload in self._read_new_lines(self.paths.ack, "ack")
        ]

    def wait_for_ack(
        self,
        sequence: int,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.25,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> ProbeAck | None:
        """Attend l'accuse d'une sequence donnee.

        Seuls les accuses ecrits **apres** la derniere commande publiee sont
        pris en compte : un accuse anterieur repond forcement a autre chose,
        meme s'il porte le meme numero. Les accuses en attente qui precedent la
        commande sont donc sautes — les lire etait au demandeur.

        `sleep` et `monotonic` sont injectables pour que les tests n'aient pas a
        attendre reellement.
        """
        if self._ack_offset < self._ack_watermark:
            self._ack_offset = self._ack_watermark
        deadline = monotonic() + timeout
        seen: list[ProbeAck] = []
        while True:
            seen.extend(self.read_acks())
            for ack in seen:
                if ack.sequence == sequence:
                    return ack
            if monotonic() >= deadline:
                return None
            sleep(poll_interval)

    def wait_for_state(
        self,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.25,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> ProbeUnitState | None:
        """Attend le prochain etat publie par le Lua."""
        deadline = monotonic() + timeout
        while True:
            states = self.read_states()
            if states:
                return states[-1]
            if monotonic() >= deadline:
                return None
            sleep(poll_interval)

    # --- entretien -----------------------------------------------------------

    def reset(self) -> None:
        """Vide les flux et repart de zero. A n'utiliser qu'entre deux essais."""
        self.paths.ensure()
        for path in (self.paths.state, self.paths.ack, self.paths.command):
            path.unlink(missing_ok=True)
        self.clear_stop()
        self._state_offset = 0
        self._ack_offset = 0
        self._next_sequence = 1
        self._ack_watermark = 0
        self.malformed.clear()

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    # --- interne -------------------------------------------------------------

    def _read_new_lines(self, path: Path, stream: str) -> Iterator[dict[str, Any]]:
        """Lit les lignes ajoutees depuis la derniere lecture de ce flux.

        Une ligne incomplete — le Lua ecrivait encore — n'est pas consommee :
        l'offset ne progresse que sur les lignes terminees par un saut de ligne,
        de sorte que la prochaine lecture la reprendra entiere.
        """
        if not path.exists():
            return
        offset = self._state_offset if stream == "state" else self._ack_offset
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            consumed = offset
            number = 0
            for raw_line in handle:
                number += 1
                if not raw_line.endswith("\n"):
                    break  # ligne encore en cours d'ecriture cote Lua
                consumed += len(raw_line.encode("utf-8"))
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as error:
                    self._record_malformed(path, number, line, str(error))
                    continue
                if not isinstance(payload, dict):
                    self._record_malformed(path, number, line, "objet JSON attendu")
                    continue
                yield payload

        if stream == "state":
            self._state_offset = consumed
        else:
            self._ack_offset = consumed

    def _record_malformed(self, path: Path, number: int, line: str, error: str) -> None:
        LOGGER.warning("ligne illisible dans %s (%s) : %s", path.name, error, line[:120])
        self.malformed.append(
            MalformedLine(path=path, line_number=number, content=line, error=error)
        )


def _sequence_in(path: Path) -> int:
    """Plus grand numero de sequence lisible dans un fichier du protocole.

    Tolerant par construction : ce fichier a pu etre tronque, ou etre en cours
    d'ecriture par le Lua. Une ligne illisible ne doit pas empecher de repartir
    au bon numero — au pire on repart un peu trop haut, ce qui est sans danger.
    """
    if not path.exists():
        return 0
    highest = 0
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            value = payload.get("sequence")
            if isinstance(value, int):
                highest = max(highest, value)
    return highest


def _highest_sequence_on_disk(paths: BridgePaths) -> int:
    """Derniere sequence connue, cote commande comme cote accuse.

    Le fichier de commande ne contient qu'un objet — la derniere commande —
    mais il n'est pas de la meme forme que les flux append-only : on le lit
    donc entier, ce que `_sequence_in` fait sans distinction.
    """
    highest = max(_sequence_in(paths.command), _sequence_in(paths.ack))
    if highest:
        return highest
    # Le fichier de commande est indente sur plusieurs lignes : si l'analyse
    # ligne a ligne n'a rien donne, tenter l'objet entier.
    if paths.command.exists():
        try:
            payload = json.loads(paths.command.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return 0
        if isinstance(payload, dict) and isinstance(payload.get("sequence"), int):
            return int(payload["sequence"])
    return 0


def read_states_from(path: str | Path) -> list[ProbeUnitState]:
    """Relit un fichier d'etats complet, hors de toute session de pont.

    Utile pour analyser apres coup ce qu'une bataille a produit.
    """
    target = Path(path)
    if not target.exists():
        return []
    states: list[ProbeUnitState] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            states.append(ProbeUnitState.from_dict(json.loads(stripped)))
        except (json.JSONDecodeError, SchemaError):
            continue
    return states


def summarise(states: Sequence[ProbeUnitState]) -> str:
    """Resume lisible d'une suite d'etats, pour le journal de faisabilite."""
    if not states:
        return "aucun etat recu"
    first, last = states[0], states[-1]
    travelled = first.position.distance_2d(last.position)
    return (
        f"{len(states)} etats pour l'unite {last.unit_id} "
        f"({last.unit_type or 'type inconnu'}), "
        f"de ({first.position.x:.1f}, {first.position.z:.1f}) "
        f"a ({last.position.x:.1f}, {last.position.z:.1f}) "
        f"soit {travelled:.1f} m parcourus"
    )
