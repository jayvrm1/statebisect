"""Data models and schemas for Phase 10 Evidence-Grounded AI Explanation Layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from statebisect.models import utc_now_iso


class ProviderStatus(str, Enum):
    """Operational readiness state of an explanation provider."""

    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"
    READY = "READY"
    ERROR = "ERROR"


class DataClassification(str, Enum):
    """Privacy boundary classification indicating where evidence data is processed."""

    LOCAL = "LOCAL"
    REMOTE = "REMOTE"


class ExplanationStatus(str, Enum):
    """Outcome status of an explanation generation attempt."""

    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    REMOTE_UNCONFIRMED = "REMOTE_UNCONFIRMED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    REFUSED = "REFUSED"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"


@dataclass
class EvidenceReference:
    """A verified citation pointing to a stable StateBisect evidence item."""

    evidence_id: str
    evidence_type: str  # 'investigation', 'candidate', 'trial', 'verification', 'coverage_gap', 'diff'
    summary: str
    is_valid: bool = True
    validation_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceObservation:
    """A factual statement strictly supported by cited evidence."""

    statement: str
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Hypothesis:
    """A clearly labeled hypothesis derived from (but not proved by) observations."""

    statement: str
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    confidence_label: str = "UNVERIFIED_HYPOTHESIS"  # Always explicitly qualified

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextPreview:
    """Exact preview of the evidence payload that would be supplied to an AI provider."""

    investigation_id: str
    provider_id: str
    data_classification: DataClassification
    evidence_ids: list[str]
    context_hash: str
    total_evidence_items: int
    omitted_items_count: int
    redacted_items_count: int
    approximate_tokens: int
    byte_size: int
    payload_preview: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["data_classification"] = self.data_classification.value
        return d


@dataclass
class ExplanationResponse:
    """Structured, evidence-grounded answer to a user investigation query."""

    investigation_id: str
    question: str
    short_answer: str
    observations: list[EvidenceObservation] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    suggested_safe_questions: list[str] = field(default_factory=list)
    evidence_references: list[EvidenceReference] = field(default_factory=list)
    disclaimer: str = (
        "AI explanations are evidence summaries and unverified hypotheses. "
        "The deterministic StateBisect engine remains the sole authority for verified findings."
    )
    is_deterministic_fallback: bool = False
    validation_errors: list[str] = field(default_factory=list)
    provider_id: str = "mock"
    model_id: str = "mock-grounded-v1"
    status: ExplanationStatus = ExplanationStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "investigation_id": self.investigation_id,
            "question": self.question,
            "short_answer": self.short_answer,
            "observations": [o.to_dict() for o in self.observations],
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "unknowns": self.unknowns,
            "suggested_safe_questions": self.suggested_safe_questions,
            "evidence_references": [r.to_dict() for r in self.evidence_references],
            "disclaimer": self.disclaimer,
            "is_deterministic_fallback": self.is_deterministic_fallback,
            "validation_errors": self.validation_errors,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "status": self.status.value,
        }


@dataclass
class AiExplanationRecord:
    """Immutable audit record of an AI explanation query and result."""

    explanation_id: str
    investigation_id: str
    provider_id: str
    model_id: str
    api_version: str
    prompt_template_version: str
    question: str
    context_hash: str
    evidence_ids: list[str]
    is_remote: bool
    user_confirmed_remote: bool
    status: ExplanationStatus
    short_answer: str
    structured_response: dict[str, Any]
    duration_ms: float
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d
