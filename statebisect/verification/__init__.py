"""StateBisect Phase 8: Verification, Reproduction, and Differential Validation."""

from .engine import VerificationEngine
from .models import (
    TrialRole,
    VerificationNecessityDetail,
    VerificationProtocolConfig,
    VerificationRunRecord,
    VerificationTrialRecord,
    VerificationVerdict,
)
from .reporting import format_verification_json, format_verification_text

__all__ = [
    "VerificationEngine",
    "VerificationVerdict",
    "TrialRole",
    "VerificationProtocolConfig",
    "VerificationTrialRecord",
    "VerificationNecessityDetail",
    "VerificationRunRecord",
    "format_verification_text",
    "format_verification_json",
]
