"""Simulated state health checks (key-value and compound condition matching)."""

from __future__ import annotations

import json
import time
from typing import Any

from ..models import HealthCheckAttempt, HealthCheckOutcome, utc_now_iso
from ..simulation import SimulatedWorkspace
from .base import BaseHealthCheck, sanitize_and_bound_evidence


class SimulatedStateCheck(BaseHealthCheck):
    """Evaluates simulated workspace state against declarative failure conditions."""

    def __init__(
        self,
        check_type: str = "simulated_state",
        check_version: str = "1.0.0",
        name: str = "Simulated State Check",
        description: str = "Verifies configuration state conditions",
        timeout_ms: int = 5000,
        repeat_count: int = 1,
        required_consistency: str = "ALL_MATCH",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            check_type=check_type,
            check_version=check_version,
            name=name,
            description=description,
            timeout_ms=timeout_ms,
            repeat_count=repeat_count,
            required_consistency=required_consistency,
            parameters=parameters,
        )

    def execute_attempt(self, ws: SimulatedWorkspace, attempt_number: int) -> HealthCheckAttempt:
        start_time = utc_now_iso()
        t0 = time.perf_counter()

        state = ws.current_state
        mode = self.parameters.get("mode") or self.check_type

        outcome = HealthCheckOutcome.PASS
        expected_val = ""
        observed_val = ""
        evidence_text = ""

        if mode in ("key_value_match", "simulated_state") and "key" in self.parameters:
            key = self.parameters["key"]
            expected = self.parameters.get("expected_value")
            actual = state.get(key)
            expected_val = json.dumps(expected)
            observed_val = json.dumps(actual)

            if actual == expected:
                outcome = HealthCheckOutcome.FAIL
                evidence_text = f"Failure condition matched: key '{key}' has value {actual!r}"
            else:
                outcome = HealthCheckOutcome.PASS
                evidence_text = f"Health check passed: key '{key}' has value {actual!r}, does not match failing value"

        elif mode in ("compound_condition", "simulated_state") and "conditions" in self.parameters:
            conditions = self.parameters.get("conditions", [])
            matches: list[str] = []
            expected_val = json.dumps(conditions)

            for cond in conditions:
                k = cond.get("key")
                op = cond.get("op")
                target_val = cond.get("value")
                actual_val = state.get(k)

                matched = False
                if op == "eq":
                    matched = actual_val == target_val
                elif op == "neq":
                    matched = actual_val != target_val
                elif op == "in":
                    matched = isinstance(target_val, (list, tuple, set)) and actual_val in target_val
                elif op == "nin":
                    matched = isinstance(target_val, (list, tuple, set)) and actual_val not in target_val

                if matched:
                    matches.append(f"{k} {op} {target_val!r}")
                else:
                    break

            observed_val = json.dumps({c.get("key"): state.get(c.get("key")) for c in conditions})

            if len(matches) == len(conditions) and len(conditions) > 0:
                outcome = HealthCheckOutcome.FAIL
                evidence_text = f"Compound failure condition met: {', '.join(matches)}"
            else:
                outcome = HealthCheckOutcome.PASS
                evidence_text = "Health check passed: compound failure condition not satisfied"
        else:
            outcome = HealthCheckOutcome.INCONCLUSIVE
            evidence_text = f"Unsupported simulated state parameters: {list(self.parameters.keys())}"

        duration_ms = (time.perf_counter() - t0) * 1000.0
        bounded_ev, was_trunc, was_red = sanitize_and_bound_evidence(evidence_text)

        return HealthCheckAttempt(
            attempt_number=attempt_number,
            outcome=outcome,
            start_time=start_time,
            finish_time=utc_now_iso(),
            duration_ms=round(duration_ms, 2),
            exit_code=0 if outcome == HealthCheckOutcome.PASS else 1,
            expected_value=expected_val,
            observed_value=observed_val,
            bounded_evidence=bounded_ev,
            is_truncated=was_trunc,
            is_redacted=was_red,
        )
