"""Telemetrie : evenements structures, journal de bataille et rapport."""

from totalwar_ai.telemetry.battle_logger import BattleLogger, configure_logging, detect_data_issues
from totalwar_ai.telemetry.events import Event, EventType, unit_event
from totalwar_ai.telemetry.report import ReportContext, render_report, write_report

__all__ = [
    "BattleLogger",
    "Event",
    "EventType",
    "ReportContext",
    "configure_logging",
    "detect_data_issues",
    "render_report",
    "unit_event",
    "write_report",
]
