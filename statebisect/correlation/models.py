"""Data models for Phase 6 candidate change generation and evidence correlation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from statebisect.monitor.models import ProcessProvenance


class CandidateEvidenceType(str, Enum):
    SNAPSHOT_DIFF = "SNAPSHOT_DIFF"
    MONITORING_EVENT = "MONITORING_EVENT"
    CORROBORATED = "CORROBORATED"


class CandidateReviewStatus(str, Enum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"


class CandidateReversibility(str, Enum):
    SIMULATABLE = "SIMULATABLE"
    MANUAL_REVERSION_ONLY = "MANUAL_REVERSION_ONLY"
    UNKNOWN = "UNKNOWN"


@dataclass
class CorrelationWindow:
    """Configurable, bounded time window around user-reported symptom onset."""

    onset_time_utc: str | None = None
    onset_time_user: str | None = None
    onset_timezone: str = "UTC"
    pre_onset_sec: int = 3600
    post_onset_sec: int = 300
    window_start_utc: str | None = None
    window_end_utc: str | None = None
    is_untimed_fallback: bool = False


@dataclass
class ScoreBreakdown:
    """Transparent and explainable breakdown of candidate priority score components."""

    evidence_type_score: float = 0.0
    proximity_score: float = 0.0
    target_relevance_score: float = 0.0
    provenance_score: float = 0.0
    corroboration_score: float = 0.0
    gap_penalty: float = 0.0
    total_score: float = 0.0
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_type_score": self.evidence_type_score,
            "proximity_score": self.proximity_score,
            "target_relevance_score": self.target_relevance_score,
            "provenance_score": self.provenance_score,
            "corroboration_score": self.corroboration_score,
            "gap_penalty": self.gap_penalty,
            "total_score": round(self.total_score, 2),
            "rationale": self.rationale,
        }


@dataclass
class CandidateChange:
    """A grounded candidate configuration change linked to observed telemetry."""

    candidate_id: str
    investigation_id: str
    evidence_type: CandidateEvidenceType
    category: str
    object_key: str
    display_name: str
    description: str
    first_observed_utc: str
    last_observed_utc: str
    temporal_offset_sec: float | None = None
    before_value: Any | None = None
    after_value: Any | None = None
    process_id: int | None = None
    process_name: str | None = None
    process_provenance: ProcessProvenance = ProcessProvenance.UNAVAILABLE
    source_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    coverage_gaps: list[dict[str, Any]] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    rank: int = 0
    review_status: CandidateReviewStatus = CandidateReviewStatus.INCLUDED
    is_active: bool = True
    reversibility: CandidateReversibility = CandidateReversibility.UNKNOWN
    inclusion_reason: str = ""
    uncertainty_note: str = ""


@dataclass
class CandidateReviewRecord:
    """Audit entry recording a user's decision to include or exclude a candidate."""

    review_id: str
    candidate_id: str
    investigation_id: str
    decision: str
    previous_status: CandidateReviewStatus
    new_status: CandidateReviewStatus
    reason: str
    reviewed_at: str
