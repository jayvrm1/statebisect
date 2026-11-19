"""Data models and enums for Phase 8 Verification, Reproduction, and Differential Validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from statebisect.models import HealthCheckOutcome, utc_now_iso


class VerificationVerdict(str, Enum):
    """Overall verdict of the verification protocol execution."""

    VERIFIED_SET = "VERIFIED_SET"
    VERIFIED_INTERACTION = "VERIFIED_INTERACTION"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INVALID_BASELINE = "INVALID_BASELINE"
    ENVIRONMENT_CORRUPTED = "ENVIRONMENT_CORRUPTED"
    CANCELLED = "CANCELLED"


class TrialRole(str, Enum):
    """The functional role of an individual trial within the verification protocol."""

    BASELINE_CONTROL = "BASELINE_CONTROL"
    CANDIDATE_SET = "CANDIDATE_SET"
    NECESSITY_TEST = "NECESSITY_TEST"
    REVERSAL_CHECK = "REVERSAL_CHECK"
    DIFFERENTIAL_BASELINE = "DIFFERENTIAL_BASELINE"


@dataclass
class VerificationProtocolConfig:
    """Configuration governing the execution of the verification protocol."""

    protocol_version: str = "1.0.0"
    repeat_count: int = 1
    required_consistency: str = "ALL_MATCH"
    enable_reversal: bool = False
    differential_baseline_ids: list[str] = field(default_factory=list)
    timeout_sec: float = 60.0

    def __post_init__(self) -> None:
        if self.repeat_count < 1:
            raise ValueError("repeat_count must be at least 1")
        if self.repeat_count > 5:
            raise ValueError("repeat_count must not exceed 5 for bounded verification")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationTrialRecord:
    """Detailed record of a single trial executed within a verification run."""

    trial_id: str
    verification_id: str
    trial_role: TrialRole
    target_change_ids: list[str]
    omitted_change_id: str | None = None
    attempt_number: int = 1
    environment_id: str = ""
    baseline_id: str = ""
    outcome: HealthCheckOutcome = HealthCheckOutcome.INCONCLUSIVE
    lifecycle_state: str = ""
    restore_status: str = ""
    cleanup_status: str = ""
    output_bounded: str = ""
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trial_role"] = self.trial_role.value
        d["outcome"] = self.outcome.value
        return d


@dataclass
class VerificationNecessityDetail:
    """Necessity assessment for a specific change within the candidate set."""

    change_id: str
    is_necessary: bool
    outcome_without_change: HealthCheckOutcome
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "is_necessary": self.is_necessary,
            "outcome_without_change": self.outcome_without_change.value,
            "explanation": self.explanation,
        }


@dataclass
class VerificationRunRecord:
    """Complete summary and audit trail of a verification run."""

    verification_id: str
    investigation_id: str
    environment_id: str
    baseline_id: str
    verdict: VerificationVerdict
    protocol_version: str = "1.0.0"
    created_at: str = field(default_factory=utc_now_iso)
    finished_at: str = ""
    candidate_ids: list[str] = field(default_factory=list)
    is_1_minimal: bool = False
    is_interaction: bool = False
    baseline_control_passed: bool = False
    candidate_set_reproduced: bool = False
    reversal_tested: bool = False
    reversal_passed: bool | None = None
    necessity_details: list[VerificationNecessityDetail] = field(default_factory=list)
    differential_results: dict[str, Any] = field(default_factory=dict)
    trials: list[VerificationTrialRecord] = field(default_factory=list)
    confidence_rationale: str = ""
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "investigation_id": self.investigation_id,
            "environment_id": self.environment_id,
            "baseline_id": self.baseline_id,
            "verdict": self.verdict.value,
            "protocol_version": self.protocol_version,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "candidate_ids": self.candidate_ids,
            "is_1_minimal": self.is_1_minimal,
            "is_interaction": self.is_interaction,
            "baseline_control_passed": self.baseline_control_passed,
            "candidate_set_reproduced": self.candidate_set_reproduced,
            "reversal_tested": self.reversal_tested,
            "reversal_passed": self.reversal_passed,
            "necessity_details": [n.to_dict() for n in self.necessity_details],
            "differential_results": self.differential_results,
            "trials": [t.to_dict() for t in self.trials],
            "confidence_rationale": self.confidence_rationale,
            "limitations": self.limitations,
        }
