"""Phase 10: Evidence-Grounded AI Explanation Layer."""

from .models import (
    AiExplanationRecord,
    ContextPreview,
    DataClassification,
    EvidenceObservation,
    EvidenceReference,
    ExplanationResponse,
    ExplanationStatus,
    Hypothesis,
    ProviderStatus,
)
from .providers.base import ExplanationProvider
from .providers.mock_provider import MockExplanationProvider
from .providers.openai_provider import OpenAIExplanationProvider
from .service import ExplanationService

__all__ = [
    "AiExplanationRecord",
    "ContextPreview",
    "DataClassification",
    "EvidenceObservation",
    "EvidenceReference",
    "ExplanationResponse",
    "ExplanationStatus",
    "Hypothesis",
    "ProviderStatus",
    "ExplanationProvider",
    "MockExplanationProvider",
    "OpenAIExplanationProvider",
    "ExplanationService",
]
