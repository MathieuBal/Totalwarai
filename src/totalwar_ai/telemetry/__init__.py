"""Telemetrie : evenements structures et journal de bataille.

`totalwar_ai.telemetry.report` n'est volontairement pas re-exporte ici : il
depend de `learning`, qui depend lui-meme de `telemetry.events`. Importer le
rapport depuis ce module creerait un cycle au chargement du paquet. Les
consommateurs importent donc `totalwar_ai.telemetry.report` directement.
"""

from totalwar_ai.telemetry.battle_logger import BattleLogger, configure_logging, detect_data_issues
from totalwar_ai.telemetry.events import Event, EventType, unit_event

__all__ = [
    "BattleLogger",
    "Event",
    "EventType",
    "configure_logging",
    "detect_data_issues",
    "unit_event",
]
