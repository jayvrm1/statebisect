"""Core domain models for StateBisect."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class HealthCheckOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class InvestigationStatus(str, Enum):
    NEW = "NEW"
    COLLECTING = "COLLECTING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFIED = "VERIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Change:
    """Represents an observed or synthetic system configuration change."""

    id: str
    category: str
    description: str
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    reversible: bool = True
    eligible: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthCheckResult:
    """The result of executing a health check against a target environment."""

    outcome: HealthCheckOutcome
    output: str
    duration_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentTrial:
    """Record of a single experiment execution with a candidate change set."""

    trial_id: str
    investigation_id: str
    selected_change_ids: list[str]
    start_time: str
    finish_time: str
    outcome: HealthCheckOutcome
    health_output: str
    reset_status: str
    duration_ms: float
    notes: str = ""


@dataclass
class Investigation:
    """Investigation lifecycle container."""

    id: str
    symptom: str
    target_system: str
    status: InvestigationStatus = InvestigationStatus.NEW
    baseline_id: str = "baseline-001"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    coverage_limitations: str = "Simulation environment; host system not inspected."


@dataclass
class MinimizationResult:
    """Result of running Delta Debugging to isolate failure-inducing changes."""

    investigation_id: str
    suspected_set: list[str]
    is_1_minimal: bool
    total_trials: int
    verdict: str
    rationale: str
    trials: list[ExperimentTrial] = field(default_factory=list)
