"""Opt-in, safe test process execution health check with strict allowlisting."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ..models import HealthCheckAttempt, HealthCheckOutcome, utc_now_iso
from ..simulation import SimulatedWorkspace
from .base import BaseHealthCheck, sanitize_and_bound_evidence


class ProcessRegistry:
    """Preconfigured, explicitly registered safe test executables.

    Scenario JSON cannot specify arbitrary executable paths. It can only reference
    a pre-registered executable ID that has been approved in Python configuration.
    """

    _registered: dict[str, Path] = {}
    _enabled: bool = False

    @classmethod
    def enable(cls) -> None:
        """Explicitly enable test process execution."""
        cls._enabled = True

    @classmethod
    def disable(cls) -> None:
        """Disable test process execution (default safe state)."""
        cls._enabled = False

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def register_executable(cls, exec_id: str, path: str | Path) -> None:
        """Register a trusted executable path under an approved ID."""
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Registered executable not found: {resolved}")
        cls._registered[exec_id] = resolved

    @classmethod
    def get_executable(cls, exec_id: str) -> Path | None:
        return cls._registered.get(exec_id)

    @classmethod
    def clear(cls) -> None:
        cls._registered.clear()
        cls._enabled = False


class RegisteredProcessCheck(BaseHealthCheck):
    """Executes a trusted pre-registered test process without shell invocation."""

    def __init__(
        self,
        check_type: str = "registered_process",
        check_version: str = "1.0.0",
        name: str = "Registered Process Check",
        description: str = "Executes an approved test executable and verifies exit code",
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

        if not ProcessRegistry.is_enabled():
            duration_ms = (time.perf_counter() - t0) * 1000.0
            msg = "Process execution is opt-in and currently disabled by safety policy."
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason=msg,
                bounded_evidence=msg,
            )

        exec_id = self.parameters.get("executable_id")
        if not exec_id:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason="Missing 'executable_id' parameter.",
                bounded_evidence="No executable_id provided.",
            )

        exec_path = ProcessRegistry.get_executable(exec_id)
        if not exec_path:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            msg = f"Executable ID '{exec_id}' is not in the trusted ProcessRegistry."
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason=msg,
                bounded_evidence=msg,
            )

        # Validate arguments: MUST be a list of strings
        raw_args = self.parameters.get("args", [])
        if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason="Process 'args' parameter must be a list of strings.",
                bounded_evidence="Invalid arguments format.",
            )

        expected_exit = self.parameters.get("expected_exit_code", 0)
        expected_output_pattern = self.parameters.get("expected_output_pattern")
        timeout_sec = self.timeout_ms / 1000.0

        # Construct safe command array: NEVER shell=True
        cmd = [str(exec_path)] + raw_args
        exit_code: int | None = None
        stdout_txt = ""
        stderr_txt = ""

        try:
            # Execute with clean, controlled environment and shell=False
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                shell=False,
                check=False,
                cwd=str(exec_path.parent),
                env=os.environ.copy(),
            )
            exit_code = proc.returncode
            stdout_txt = proc.stdout
            stderr_txt = proc.stderr
        except subprocess.TimeoutExpired as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason=f"Process timed out after {self.timeout_ms}ms.",
                bounded_evidence="Process timed out and was killed.",
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason=f"Subprocess execution error: {exc}",
                bounded_evidence=f"Failed to spawn process: {exc}",
            )

        duration_ms = (time.perf_counter() - t0) * 1000.0
        combined_output = f"stdout:\n{stdout_txt}\nstderr:\n{stderr_txt}".strip()
        bounded_ev, was_trunc, was_red = sanitize_and_bound_evidence(combined_output)

        # Failure condition evaluation:
        # If exit_code matches expected_failing_exit_code -> FAIL
        # Otherwise -> PASS
        failing_exit = self.parameters.get("failing_exit_code")
        if failing_exit is not None:
            if exit_code == failing_exit:
                outcome = HealthCheckOutcome.FAIL
            else:
                outcome = HealthCheckOutcome.PASS
        else:
            # Default: expected_exit_code indicates health (PASS)
            if exit_code == expected_exit:
                if expected_output_pattern and expected_output_pattern not in combined_output:
                    outcome = HealthCheckOutcome.FAIL
                else:
                    outcome = HealthCheckOutcome.PASS
            else:
                outcome = HealthCheckOutcome.FAIL

        return HealthCheckAttempt(
            attempt_number=attempt_number,
            outcome=outcome,
            start_time=start_time,
            finish_time=utc_now_iso(),
            duration_ms=round(duration_ms, 2),
            exit_code=exit_code,
            expected_value=f"exit_code={expected_exit}",
            observed_value=f"exit_code={exit_code}",
            bounded_evidence=bounded_ev,
            is_truncated=was_trunc,
            is_redacted=was_red,
        )
