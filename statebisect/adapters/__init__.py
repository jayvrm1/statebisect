"""Package exports for Phase 7 isolated adapters and trial execution."""

from .base import BaseEnvironmentAdapter
from .fake_adapter import FakeAdapter
from .hyperv_adapter import HyperVAdapter
from .models import (
    AdapterOperationResult,
    AdapterType,
    BaselineRecord,
    EnvironmentRecord,
    EnvironmentStatus,
    IsolatedTrialRecord,
    RegisteredChangeAction,
    TrialLifecycleState,
)
from .registry import AdapterRegistry, RegisteredActionRegistry
from .runner import IsolatedTrialRunner
from .sandbox_evaluator import WindowsSandboxEvaluation
from .simulation_adapter import SimulationAdapter

__all__ = [
    "AdapterOperationResult",
    "AdapterRegistry",
    "AdapterType",
    "BaselineRecord",
    "BaseEnvironmentAdapter",
    "EnvironmentRecord",
    "EnvironmentStatus",
    "FakeAdapter",
    "HyperVAdapter",
    "IsolatedTrialRecord",
    "IsolatedTrialRunner",
    "RegisteredActionRegistry",
    "RegisteredChangeAction",
    "SimulationAdapter",
    "TrialLifecycleState",
    "WindowsSandboxEvaluation",
]
