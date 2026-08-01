"""Simulateur tactique et scenarios reproductibles."""

from totalwar_ai.simulation.environment import (
    SimulationEnvironment,
    StepResult,
    UnitSpec,
)
from totalwar_ai.simulation.runner import BattleResult, run_battle
from totalwar_ai.simulation.scenarios import (
    SCENARIOS,
    Scenario,
    ScenarioCatalog,
    get_scenario,
    scenario_names,
)
from totalwar_ai.simulation.unit_templates import (
    SimulationParameters,
    SimulationRules,
    UnitTemplate,
)

__all__ = [
    "SCENARIOS",
    "BattleResult",
    "Scenario",
    "ScenarioCatalog",
    "SimulationEnvironment",
    "SimulationParameters",
    "SimulationRules",
    "StepResult",
    "UnitSpec",
    "UnitTemplate",
    "get_scenario",
    "run_battle",
    "scenario_names",
]
