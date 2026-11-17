"""Data models for Phase 7 isolated experiment adapters and trial execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from statebisect.models import HealthCheckOutcome, utc_now_iso


class AdapterType(str, Enum):
    """Supported or evaluated virtualization adapter types."""

    SIMULATION = "SIMULATION"
    FAKE = "FAKE"
    HYPERV = "HYPERV"
    WINDOWS_SANDBOX = "WINDOWS_SANDBOX"


class EnvironmentStatus(str, Enum):
    """Lifecycle and integrity status of a registered experiment environment."""

    REGISTERED = "REGISTERED"
    VERIFIED = "VERIFIED"
    CORRUPTED = "CORRUPTED"
    UNAVAILABLE = "UNAVAILABLE"


class TrialLifecycleState(str, Enum):
    """Strict state machine states for an isolated trial."""

    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    RESTORING = "RESTORING"
    BASELINE_VERIFIED = "BASELINE_VERIFIED"
    APPLYING = "APPLYING"
    TESTING = "TESTING"
    CLEANING_UP = "CLEANING_UP"
    COMPLETED = "COMPLETED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    ENVIRONMENT_CORRUPTED = "ENVIRONMENT_CORRUPTED"


@dataclass
class EnvironmentRecord:
    """Explicitly registered environment for running experiment trials."""

    environment_id: str
    adapter_type: AdapterType
    display_name: str
    is_disposable: bool
    is_host: bool = False
    status: EnvironmentStatus = EnvironmentStatus.REGISTERED
    baseline_ids: list[str] = field(default_factory=list)
    configuration: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def validate_for_execution(self) -> None:
        """Enforce core safety invariants before running any experiment."""
        if self.is_host:
            raise PermissionError(
                f"Safety Violation: Environment '{self.environment_id}' represents the host system. "
                "Experiments must never execute against the host machine."
            )
        if not self.is_disposable:
            raise PermissionError(
                f"Safety Violation: Environment '{self.environment_id}' is not marked disposable. "
                "Experiments may only run in environments explicitly confirmed to be disposable."
            )
        if self.status == EnvironmentStatus.CORRUPTED:
            raise RuntimeError(
                f"Environment '{self.environment_id}' is marked CORRUPTED due to previous reset/cleanup failures. "
                "Further trials are blocked until baseline integrity is restored and verified by the user."
            )

    @property
    def is_simulation(self) -> bool:
        return self.adapter_type in (AdapterType.SIMULATION, AdapterType.FAKE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment_id": self.environment_id,
            "adapter_type": self.adapter_type.value,
            "display_name": self.display_name,
            "is_disposable": self.is_disposable,
            "is_host": self.is_host,
            "is_simulation": self.is_simulation,
            "status": self.status.value,
            "baseline_ids": self.baseline_ids,
            "configuration": self.configuration,
            "created_at": self.created_at,
        }


@dataclass
class BaselineRecord:
    """Registered baseline state / checkpoint for an experiment environment."""

    baseline_id: str
    environment_id: str
    display_name: str
    is_verified: bool = False
    checksum_or_state: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "environment_id": self.environment_id,
            "display_name": self.display_name,
            "is_verified": self.is_verified,
            "checksum_or_state": self.checksum_or_state,
            "created_at": self.created_at,
        }


@dataclass
class AdapterOperationResult:
    """Structured result of an adapter action (restore, apply, verify, cleanup)."""

    success: bool
    duration_ms: float
    environment_id: str
    baseline_id: str | None = None
    output: str = ""
    error_reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegisteredChangeAction:
    """Allowlisted configuration change action executable inside an isolated environment."""

    action_id: str
    name: str
    description: str
    risk_level: str  # e.g., "LOW"
    allowed_parameters: list[str]
    reversibility: str = "AUTOMATIC"


@dataclass
class IsolatedTrialRecord:
    """Complete audit record of an isolated trial execution."""

    trial_id: str
    investigation_id: str
    environment_id: str
    baseline_id: str
    adapter_type: AdapterType
    lifecycle_state: TrialLifecycleState
    is_simulation: bool
    selected_change_ids: list[str]
    start_time: str
    finish_time: str
    outcome: HealthCheckOutcome
    output_bounded: str
    duration_ms: float
    restore_status: str = "UNKNOWN"
    verify_status: str = "UNKNOWN"
    apply_status: str = "UNKNOWN"
    test_status: str = "UNKNOWN"
    cleanup_status: str = "UNKNOWN"
    error_reason: str | None = None
    evidence_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "investigation_id": self.investigation_id,
            "environment_id": self.environment_id,
            "baseline_id": self.baseline_id,
            "adapter_type": self.adapter_type.value,
            "lifecycle_state": self.lifecycle_state.value,
            "is_simulation": self.is_simulation,
            "selected_change_ids": self.selected_change_ids,
            "start_time": self.start_time,
            "finish_time": self.finish_time,
            "outcome": self.outcome.value,
            "output_bounded": self.output_bounded,
            "duration_ms": self.duration_ms,
            "restore_status": self.restore_status,
            "verify_status": self.verify_status,
            "apply_status": self.apply_status,
            "test_status": self.test_status,
            "cleanup_status": self.cleanup_status,
            "error_reason": self.error_reason,
            "evidence_data": self.evidence_data,
        }
