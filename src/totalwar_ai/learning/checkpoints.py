"""Sauvegarde et rechargement des doctrines apprises.

Un checkpoint est un simple fichier JSON par composition d'armee. C'est ce qui
permet a une doctrine ajustee de survivre a un redemarrage, et de rester
inspectable a la main — exigence d'explicabilite du README.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path

from totalwar_ai.learning.adaptation import DoctrineProfile

#: Prefixe des fichiers de doctrine dans `data/models/`.
CHECKPOINT_PREFIX = "doctrine"

_SAFE_NAME = re.compile(r"[^a-z0-9]+")


def checkpoint_name(fingerprint: str) -> str:
    """Nom de fichier stable et lisible pour une composition.

    On garde un fragment lisible de l'empreinte pour pouvoir retrouver un
    checkpoint a l'œil, plus un hachage court pour eviter les collisions.
    """
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:10]
    readable = _SAFE_NAME.sub("-", fingerprint.lower()).strip("-")[:40] or "inconnu"
    return f"{CHECKPOINT_PREFIX}-{readable}-{digest}.json"


class CheckpointStore:
    """Repertoire de doctrines apprises."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def path_for(self, fingerprint: str) -> Path:
        return self.directory / checkpoint_name(fingerprint)

    def save(self, profile: DoctrineProfile) -> Path:
        """Ecrit le profil et renvoie son chemin."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(profile.fingerprint)
        path.write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def load(self, fingerprint: str) -> DoctrineProfile | None:
        """Relit un profil, ou `None` s'il n'existe pas.

        Un fichier illisible est traite comme absent : une doctrine corrompue ne
        doit jamais empecher une bataille de se jouer avec les reglages par defaut.
        """
        path = self.path_for(fingerprint)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(raw, dict):
            return None
        return DoctrineProfile.from_dict(raw)

    def all_profiles(self) -> Iterator[DoctrineProfile]:
        """Parcourt les doctrines enregistrees, la plus recente d'abord."""
        if not self.directory.exists():
            return
        paths = sorted(
            self.directory.glob(f"{CHECKPOINT_PREFIX}-*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for path in paths:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(raw, dict):
                yield DoctrineProfile.from_dict(raw)
