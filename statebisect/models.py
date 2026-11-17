"""Core domain models for StateBisect."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

ENGINE_VERSION = "0.3.0"


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
class HealthCheckAttempt:
    """Record of an individual health check attempt within a repeatable trial."""

    attempt_number: int
    outcome: HealthCheckOutcome
    start_time: str
    finish_time: str
    duration_ms: float
    exit_code: int | None = None
    expected_value: str = ""
    observed_value: str = ""
    bounded_evidence: str = ""
    error_reason: str = ""
    is_truncated: bool = False
    is_redacted: bool = False


@dataclass
class HealthCheckResult:
    """The aggregate result of executing a health check against a target environment."""

    outcome: HealthCheckOutcome
    output: str
    duration_ms: float = 0.0
    reason: str = ""
    attempts: list[HealthCheckAttempt] = field(default_factory=list)
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
    check_type: str = ""
    check_version: str = "1.0.0"
    parameters_hash: str = ""
    attempts: list[HealthCheckAttempt] = field(default_factory=list)


@dataclass
class Investigation:
    """Investigation lifecycle container with reproducibility metadata."""

    id: str
    symptom: str
    target_system: str
    status: InvestigationStatus = InvestigationStatus.NEW
    baseline_id: str = "baseline-001"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    coverage_limitations: str = "Simulation environment; host system not inspected."
    scenario_id: str = ""
    scenario_hash: str = ""
    scenario_snapshot: str = ""
    engine_version: str = ENGINE_VERSION
    onset_time_utc: str | None = None
    onset_time_user: str | None = None
    onset_timezone: str = "UTC"
    last_known_good_utc: str | None = None
    correlation_window_json: str = "{}"
    ranking_version: str = "1.0.0"
    user_notes: str = ""


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


@dataclass
class AuditLogEntry:
    """Structured audit trail record for investigation lifecycle events."""

    log_id: str
    investigation_id: str
    timestamp: str
    level: str
    event_type: str
    message: str
    details_json: str = "{}"


class CancellationToken:
    """Thread-safe cancellation token for halting runs between trials cleanly."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._reason: str = ""

    def cancel(self, reason: str = "User cancelled execution") -> None:
        self._reason = reason
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def reason(self) -> str:
        return self._reason
