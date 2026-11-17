"""StateBisect: Deterministic configuration delta-debugging and investigation system."""

from .models import (
    AuditLogEntry,
    CancellationToken,
    Change,
    ExperimentTrial,
    HealthCheckOutcome,
    HealthCheckResult,
    Investigation,
    InvestigationStatus,
    MinimizationResult,
    ENGINE_VERSION,
)
from .simulation import SimulatedWorkspace, create_key_value_health_check
from .storage import Storage
from .runner import ExperimentRunner
from .delta_debugging import DeltaDebugger
from .reporting import generate_json_report, generate_text_report
from .scenarios import (
    DeclarativeScenario,
    ScenarioChange,
    ScenarioHealthCheck,
    ScenarioValidationError,
    build_declarative_health_check,
    execute_scenario,
    load_scenario_file,
    validate_and_parse_scenario,
)

__version__ = ENGINE_VERSION
__all__ = [
    "AuditLogEntry",
    "CancellationToken",
    "Change",
    "DeclarativeScenario",
    "DeltaDebugger",
    "ENGINE_VERSION",
    "ExperimentRunner",
    "ExperimentTrial",
    "HealthCheckOutcome",
    "HealthCheckResult",
    "Investigation",
    "InvestigationStatus",
    "MinimizationResult",
    "ScenarioChange",
    "ScenarioHealthCheck",
    "ScenarioValidationError",
    "SimulatedWorkspace",
    "Storage",
    "build_declarative_health_check",
    "create_key_value_health_check",
    "execute_scenario",
    "generate_json_report",
    "generate_text_report",
    "load_scenario_file",
    "validate_and_parse_scenario",
]
