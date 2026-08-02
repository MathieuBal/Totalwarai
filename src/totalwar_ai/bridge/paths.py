"""Localisation du repertoire d'echange partage avec le jeu.

Le Lua de bataille ne peut ouvrir que des chemins **relatifs au repertoire de
travail du jeu**, c'est-a-dire le dossier d'installation de WARHAMMER III (c'est
ainsi qu'AI General 3 lit son propre fichier de configuration). Python, lui,
tourne ailleurs : il doit donc retrouver ce dossier.

Ordre de resolution, du plus explicite au plus devinatoire :

1. la variable d'environnement ``TOTALWAR_AI_BRIDGE_DIR`` ;
2. un chemin passe explicitement a l'appelant ;
3. les emplacements Steam usuels, s'ils existent reellement sur la machine.

Aucune magie : si rien n'est trouve, la fonction le dit au lieu de deviner.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

#: Revision du script Lua attendue par cette version de Python.
#:
#: Doit valoir `TOTALWAR_AI_PROBE_REVISION` dans
#: `lua_mod/script/battle/mod/totalwar_ai_probe.lua`. Un test verifie l'egalite,
#: pour que les deux ne puissent pas diverger en silence.
EXPECTED_PROBE_REVISION = 6

#: Variable d'environnement qui court-circuite toute la detection.
BRIDGE_DIR_ENV_VAR = "TOTALWAR_AI_BRIDGE_DIR"

#: Nom du sous-dossier cree dans le repertoire du jeu.
BRIDGE_DIR_NAME = "totalwar_ai"

#: Noms de fichiers du protocole. Fixes : le Lua les code en dur de son cote.
STATE_FILENAME = "totalwar_ai_state.jsonl"
COMMAND_FILENAME = "totalwar_ai_command.json"
ACK_FILENAME = "totalwar_ai_ack.jsonl"
STOP_FILENAME = "totalwar_ai_stop"

#: Emplacements Steam habituels de WARHAMMER III, par plateforme.
_STEAM_CANDIDATES: dict[str, tuple[str, ...]] = {
    "win32": (
        r"C:\Program Files (x86)\Steam\steamapps\common\Total War WARHAMMER III",
        r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III",
    ),
    "linux": (
        "~/.steam/steam/steamapps/common/Total War WARHAMMER III",
        "~/.local/share/Steam/steamapps/common/Total War WARHAMMER III",
    ),
    "darwin": ("~/Library/Application Support/Steam/steamapps/common/Total War WARHAMMER III",),
}


class BridgeDirectoryNotFoundError(RuntimeError):
    """Le repertoire d'echange n'a pas pu etre localise."""


def candidate_game_directories() -> Iterator[Path]:
    """Emplacements d'installation plausibles qui existent reellement."""
    for raw in _STEAM_CANDIDATES.get(sys.platform, ()):
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            yield candidate


def find_game_directory(explicit: str | Path | None = None) -> Path | None:
    """Repertoire d'installation du jeu, ou `None` s'il reste introuvable."""
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_dir() else None
    return next(candidate_game_directories(), None)


def resolve_bridge_dir(
    explicit: str | Path | None = None,
    *,
    create: bool = False,
) -> Path:
    """Repertoire d'echange effectif.

    `explicit` peut designer soit le repertoire d'echange lui-meme, soit le
    repertoire d'installation du jeu — on accepte les deux, parce que l'un est
    ce que l'utilisateur connait et l'autre ce dont le code a besoin.
    """
    from_env = os.environ.get(BRIDGE_DIR_ENV_VAR)
    if explicit is None and from_env:
        explicit = from_env

    if explicit is not None:
        candidate = Path(explicit).expanduser()
        # Un repertoire de jeu contient le sous-dossier d'echange ; un
        # repertoire d'echange se reconnait a son propre nom.
        bridge = candidate if candidate.name == BRIDGE_DIR_NAME else candidate / BRIDGE_DIR_NAME
        if create:
            bridge.mkdir(parents=True, exist_ok=True)
        return bridge

    game_dir = find_game_directory()
    if game_dir is None:
        raise BridgeDirectoryNotFoundError(
            "Repertoire d'echange introuvable. Definir "
            f"{BRIDGE_DIR_ENV_VAR}=<dossier d'installation de WARHAMMER III>, "
            "ou passer le chemin explicitement."
        )
    bridge = game_dir / BRIDGE_DIR_NAME
    if create:
        bridge.mkdir(parents=True, exist_ok=True)
    return bridge


@dataclass(frozen=True, slots=True)
class BridgePaths:
    """Les quatre chemins du protocole, derives d'un meme repertoire."""

    directory: Path

    @classmethod
    def resolve(cls, explicit: str | Path | None = None, *, create: bool = False) -> BridgePaths:
        return cls(directory=resolve_bridge_dir(explicit, create=create))

    @property
    def state(self) -> Path:
        """Etats ecrits par le Lua, lus par Python (append-only)."""
        return self.directory / STATE_FILENAME

    @property
    def command(self) -> Path:
        """Commande ecrite par Python, lue par le Lua (remplacee a chaque fois)."""
        return self.directory / COMMAND_FILENAME

    @property
    def ack(self) -> Path:
        """Accuses ecrits par le Lua, lus par Python (append-only)."""
        return self.directory / ACK_FILENAME

    @property
    def stop(self) -> Path:
        """Sentinelle d'arret d'urgence : sa presence suffit a tout stopper."""
        return self.directory / STOP_FILENAME

    def ensure(self) -> BridgePaths:
        self.directory.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def game_directory(self) -> Path:
        """Repertoire de travail du jeu, ou le Lua ecrit ses journaux."""
        return self.directory.parent

    def latest_script_log(self) -> Path | None:
        """Journal de script le plus recent produit par le jeu.

        Le jeu ecrit `script_log_<date>.txt` dans son repertoire de travail.
        C'est la seule fenetre sur ce que fait le Lua tant que l'echange par
        fichiers n'est pas etabli — et le canal de repli s'il ne l'est jamais.
        """
        logs = sorted(
            self.game_directory.glob("script_log_*.txt"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return logs[0] if logs else None
