"""Chargement et fusion de la configuration YAML.

Point d'entree unique : tous les modules obtiennent leurs reglages via
:func:`load_config`, jamais en lisant un fichier YAML directement. Cela garantit
qu'une valeur absente retombe toujours sur :data:`DEFAULT_CONFIG` et que les
chemins sont resolus de la meme facon partout.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_ENV_VAR = "TOTALWAR_AI_CONFIG"
DATA_DIR_ENV_VAR = "TOTALWAR_AI_DATA_DIR"

#: Valeurs par defaut completes : le projet doit demarrer sans aucun fichier YAML.
DEFAULT_CONFIG: dict[str, Any] = {
    "agent": {
        "mode": "deterministic",
        "decision_interval_seconds": 2.0,
        "strategic_interval_seconds": 10.0,
        "confidence_threshold": 0.55,
        "allow_learning": True,
        "allow_model_promotion": False,
        "line_spacing": 45.0,
        "missile_offset": 25.0,
        "artillery_offset": 55.0,
        "command_offset": 35.0,
        "reserve_offset": 60.0,
        "cavalry_wing_offset": 40.0,
        "engagement_distance": 55.0,
        "pursuit_power_ratio": 1.5,
        "reserve_units": 1,
        "min_line_for_reserve": 4,
    },
    "safety": {
        "emergency_stop_key": "F10",
        "max_orders_per_minute": 90,
        "protect_lord": True,
        "prevent_ranged_melee": True,
        "prevent_artillery_charge": True,
        "ranged_threat_radius": 70.0,
        "min_local_power_ratio_for_charge": 0.6,
        "lord_retreat_health_ratio": 0.35,
    },
    "memory": {
        "database_path": "data/totalwar_ai.sqlite3",
        "replay_capacity": 250000,
        "keep_raw_battles": True,
    },
    "telemetry": {
        "level": "INFO",
        "write_jsonl": True,
        "generate_report": True,
        "battles_dir": "data/battles",
        "reports_dir": "data/reports",
    },
    "simulation": {
        "tick_seconds": 0.5,
        "max_battle_seconds": 900.0,
        "field_width": 400.0,
        "field_depth": 400.0,
    },
}


class ConfigError(RuntimeError):
    """Configuration introuvable, illisible ou structurellement invalide."""


def project_root() -> Path:
    """Racine du depot, deduite de l'emplacement du paquet installe en mode editable.

    En cas d'installation classique (hors depot), on retombe sur le repertoire
    de travail courant : les chemins de donnees restent alors relatifs a l'endroit
    depuis lequel le CLI est lance.
    """
    package_dir = Path(__file__).resolve().parent
    for candidate in (package_dir, *package_dir.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


def config_dir() -> Path:
    """Repertoire `config/` du depot."""
    return project_root() / "config"


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Fusionne recursivement `override` dans `base` sans muter les entrees."""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def read_yaml(path: Path) -> dict[str, Any]:
    """Lit un fichier YAML et garantit qu'il contient bien un mapping."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - message specifique
        raise ConfigError(f"Fichier de configuration introuvable : {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide dans {path} : {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Le fichier {path} doit contenir un mapping, pas {type(raw).__name__}")
    return raw


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Configuration applicative resolue.

    `values` contient l'arbre complet fusionne ; les accesseurs types evitent de
    disperser des `config["safety"]["..."]` dans tout le code.
    """

    values: dict[str, Any]
    root: Path
    source: Path | None = None

    @property
    def agent(self) -> dict[str, Any]:
        return dict(self.values["agent"])

    @property
    def safety(self) -> dict[str, Any]:
        return dict(self.values["safety"])

    @property
    def memory(self) -> dict[str, Any]:
        return dict(self.values["memory"])

    @property
    def telemetry(self) -> dict[str, Any]:
        return dict(self.values["telemetry"])

    @property
    def simulation(self) -> dict[str, Any]:
        return dict(self.values["simulation"])

    def path(self, *keys: str) -> Path:
        """Resout une valeur de configuration en chemin absolu.

        Exemple : ``config.path("memory", "database_path")``. Un chemin relatif
        est ancre sur la racine du projet, jamais sur le repertoire courant.
        """
        node: Any = self.values
        for key in keys:
            if not isinstance(node, Mapping) or key not in node:
                raise ConfigError(f"Cle de configuration inconnue : {'.'.join(keys)}")
            node = node[key]
        if not isinstance(node, str):
            raise ConfigError(f"La cle {'.'.join(keys)} ne contient pas un chemin")
        candidate = Path(node)
        return candidate if candidate.is_absolute() else self.root / candidate


def load_config(
    path: str | Path | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
    data_dir: str | Path | None = None,
) -> AppConfig:
    """Charge la configuration effective.

    Ordre de priorite croissant : :data:`DEFAULT_CONFIG`, fichier YAML,
    `overrides` fournis par l'appelant. Le fichier est cherche dans cet ordre :
    argument explicite, variable d'environnement ``TOTALWAR_AI_CONFIG``,
    puis ``config/default.yaml`` s'il existe.
    """
    root = project_root()
    source: Path | None = None

    if path is not None:
        source = Path(path)
        if not source.is_absolute():
            source = root / source
        if not source.exists():
            raise ConfigError(f"Fichier de configuration introuvable : {source}")
    else:
        env_path = os.environ.get(CONFIG_ENV_VAR)
        if env_path:
            candidate = Path(env_path)
            source = candidate if candidate.is_absolute() else root / candidate
            if not source.exists():
                raise ConfigError(f"{CONFIG_ENV_VAR} pointe vers un fichier absent : {source}")
        elif (config_dir() / "default.yaml").exists():
            source = config_dir() / "default.yaml"

    values = dict(DEFAULT_CONFIG)
    if source is not None:
        values = deep_merge(values, read_yaml(source))
    if overrides:
        values = deep_merge(values, overrides)

    resolved_data_dir = data_dir or os.environ.get(DATA_DIR_ENV_VAR)
    if resolved_data_dir is not None:
        values = _relocate_data_dir(values, Path(resolved_data_dir))

    return AppConfig(values=values, root=root, source=source)


def _relocate_data_dir(values: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Reancre les chemins de donnees sur `data_dir` (utile pour les tests)."""
    return deep_merge(
        values,
        {
            "memory": {"database_path": str(data_dir / "totalwar_ai.sqlite3")},
            "telemetry": {
                "battles_dir": str(data_dir / "battles"),
                "reports_dir": str(data_dir / "reports"),
            },
        },
    )


def load_named_config(name: str) -> dict[str, Any]:
    """Charge un fichier annexe de `config/` (`rewards`, `unit_roles`, `simulation`).

    Renvoie un dictionnaire vide si le fichier n'existe pas : chaque consommateur
    possede ses propres valeurs par defaut et doit rester fonctionnel sans YAML.
    """
    path = config_dir() / f"{name}.yaml"
    if not path.exists():
        return {}
    return read_yaml(path)
