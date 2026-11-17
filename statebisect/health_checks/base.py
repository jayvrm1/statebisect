"""Base interfaces, secret redaction, and evidence bounding for health checks."""

from __future__ import annotations

import abc
import hashlib
import json
import re
import time
from typing import Any

from ..models import (
    HealthCheckAttempt,
    HealthCheckOutcome,
    HealthCheckResult,
    utc_now_iso,
)
from ..simulation import SimulatedWorkspace

MAX_EVIDENCE_LENGTH = 4096

# Regex patterns for redacting sensitive values like tokens, passwords, and keys
SECRET_PATTERNS = [
    re.compile(r"(password\s*[:=]\s*)([^\s,;&\"']+)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)([^\s,;&\"']+)", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s,;&\"']+)", re.IGNORECASE),
    re.compile(r"(bearer\s+)([a-zA-Z0-9_\-\.]+)", re.IGNORECASE),
    re.compile(r"(secret\s*[:=]\s*)([^\s,;&\"']+)", re.IGNORECASE),
]


def redact_sensitive_text(text: str) -> tuple[str, bool]:
    """Redact known secret patterns from output text."""
    redacted = text
    was_redacted = False
    for pat in SECRET_PATTERNS:
        if pat.search(redacted):
            redacted = pat.sub(r"\1[REDACTED]", redacted)
            was_redacted = True
    return redacted, was_redacted


def bound_evidence(text: str, max_length: int = MAX_EVIDENCE_LENGTH) -> tuple[str, bool]:
    """Enforce maximum length bounds on output text to avoid unbounded log growth."""
    if len(text) > max_length:
        truncated = text[:max_length] + f"\n... [TRUNCATED: {len(text) - max_length} chars omitted]"
        return truncated, True
    return text, False


def sanitize_and_bound_evidence(text: str) -> tuple[str, bool, bool]:
    """Apply secret redaction and length bounding to raw evidence text."""
    redacted, was_redacted = redact_sensitive_text(text)
    bounded, was_truncated = bound_evidence(redacted)
    return bounded, was_truncated, was_redacted


class BaseHealthCheck(abc.ABC):
    """Abstract base class for all pluggable, versioned health checks."""

    def __init__(
        self,
        check_type: str,
        check_version: str = "1.0.0",
        name: str = "",
        description: str = "",
        timeout_ms: int = 5000,
        repeat_count: int = 1,
        required_consistency: str = "ALL_MATCH",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self.check_type = check_type
        self.check_version = check_version
        self.name = name or check_type
        self.description = description
        self.timeout_ms = max(100, min(timeout_ms, 60000))  # Bounded 100ms - 60s
        self.repeat_count = max(1, min(repeat_count, 10))  # Bounded 1 - 10
        self.required_consistency = required_consistency
        self.parameters = parameters or {}
        self.parameters_hash = hashlib.sha256(
            json.dumps(self.parameters, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @abc.abstractmethod
    def execute_attempt(self, ws: SimulatedWorkspace, attempt_number: int) -> HealthCheckAttempt:
        """Execute a single attempt against the workspace state."""
        raise NotImplementedError

    def run(self, ws: SimulatedWorkspace) -> HealthCheckResult:
        """Run the health check according to configured repeat and consistency policies."""
        attempts: list[HealthCheckAttempt] = []
        t0 = time.perf_counter()

        for attempt_idx in range(1, self.repeat_count + 1):
            att_t0 = time.perf_counter()
            start_iso = utc_now_iso()
            try:
                attempt = self.execute_attempt(ws, attempt_idx)
            except TimeoutError as err:
                duration_ms = (time.perf_counter() - att_t0) * 1000.0
                attempt = HealthCheckAttempt(
                    attempt_number=attempt_idx,
                    outcome=HealthCheckOutcome.INCONCLUSIVE,
                    start_time=start_iso,
                    finish_time=utc_now_iso(),
                    duration_ms=round(duration_ms, 2),
                    error_reason=f"Timeout exceeded ({self.timeout_ms}ms): {err}",
                    bounded_evidence="Execution timed out.",
                )
            except Exception as err:
                duration_ms = (time.perf_counter() - att_t0) * 1000.0
                attempt = HealthCheckAttempt(
                    attempt_number=attempt_idx,
                    outcome=HealthCheckOutcome.INCONCLUSIVE,
                    start_time=start_iso,
                    finish_time=utc_now_iso(),
                    duration_ms=round(duration_ms, 2),
                    error_reason=f"Check execution exception: {type(err).__name__}: {err}",
                    bounded_evidence=f"Exception raised: {err}",
                )

            attempts.append(attempt)

        total_duration_ms = (time.perf_counter() - t0) * 1000.0

        # Aggregation of attempts
        outcomes = [att.outcome for att in attempts]
        unique_outcomes = set(outcomes)

        if len(unique_outcomes) == 1:
            final_outcome = outcomes[0]
            if final_outcome == HealthCheckOutcome.PASS:
                reason = f"Health check passed consistently across {len(attempts)} attempt(s)."
            elif final_outcome == HealthCheckOutcome.FAIL:
                reason = f"Failure symptom reproduced consistently across {len(attempts)} attempt(s)."
            else:
                reason = attempts[0].error_reason or "Health check inconclusive."
        else:
            # Attempts disagreed (flaky or intermittent)
            final_outcome = HealthCheckOutcome.INCONCLUSIVE
            outcome_names = [o.value for o in outcomes]
            reason = (
                f"Intermittent / inconsistent result observed across {len(attempts)} attempts: "
                f"{outcome_names}. Policy '{self.required_consistency}' requires strict consistency."
            )

        # Representative output from last attempt or error
        last_attempt = attempts[-1]
        summary_output = last_attempt.bounded_evidence or last_attempt.error_reason or reason

        return HealthCheckResult(
            outcome=final_outcome,
            output=summary_output,
            duration_ms=round(total_duration_ms, 2),
            reason=reason,
            attempts=attempts,
            details={
                "check_type": self.check_type,
                "check_version": self.check_version,
                "repeat_count": self.repeat_count,
                "consistency_policy": self.required_consistency,
                "parameters_hash": self.parameters_hash,
            },
        )
