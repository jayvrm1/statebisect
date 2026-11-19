"""Configurable test double adapter for unit tests and fault-injection verification."""

from __future__ import annotations

import time
from typing import Any

from statebisect.models import HealthCheckOutcome, HealthCheckResult

from .base import BaseEnvironmentAdapter
from .models import (
    AdapterOperationResult,
    AdapterType,
    BaselineRecord,
    EnvironmentRecord,
    EnvironmentStatus,
)


class FakeAdapter(BaseEnvironmentAdapter):
    """Test double implementing BaseEnvironmentAdapter with configurable failure injection."""

    def __init__(
        self,
        environment_id: str = "fake-env-001",
        baseline_id: str = "fake-base-001",
        is_disposable: bool = True,
        is_host: bool = False,
    ) -> None:
        self.env_id = environment_id
        self.base_id = baseline_id
        self.is_disposable = is_disposable
        self.is_host = is_host
        self.call_history: list[tuple[str, Any]] = []

        # Fault injection toggles
        self.fail_restore: bool = False
        self.fail_verify: bool = False
        self.fail_apply: bool = False
        self.fail_health_check: bool = False
        self.fail_cleanup: bool = False
        self.corrupt_on_cleanup_failure: bool = True
        self.applied_changes: list[str] = []

        self._environments: dict[str, EnvironmentRecord] = {}
        self._baselines: dict[str, dict[str, BaselineRecord]] = {}

        self.register_environment(
            self.env_id,
            self.base_id,
            is_disposable=self.is_disposable,
            is_host=self.is_host,
        )

    def register_environment(
        self,
        env: EnvironmentRecord | str,
        baseline_id: str = "fake-base-001",
        is_disposable: bool = True,
        is_host: bool = False,
    ) -> None:
        """Register an environment or string ID into the fake adapter."""
        if isinstance(env, str):
            env_rec = EnvironmentRecord(
                environment_id=env,
                adapter_type=AdapterType.FAKE,
                display_name=f"Fake Env {env}",
                is_disposable=is_disposable,
                is_host=is_host,
                status=EnvironmentStatus.VERIFIED,
                baseline_ids=[baseline_id],
                configuration={"mock": True},
            )
            base_rec = BaselineRecord(
                baseline_id=baseline_id,
                environment_id=env,
                display_name=f"Baseline {baseline_id}",
                is_verified=True,
                checksum_or_state="mock-checksum",
            )
        else:
            env_rec = env
            base_rec = BaselineRecord(
                baseline_id=baseline_id,
                environment_id=env.environment_id,
                display_name=f"Baseline {baseline_id}",
                is_verified=True,
                checksum_or_state="mock-checksum",
            )
        self._environments[env_rec.environment_id] = env_rec
        self._baselines.setdefault(env_rec.environment_id, {})[base_rec.baseline_id] = base_rec

    def register_baseline(
        self, environment_id: str, baseline_id: str, display_name: str = ""
    ) -> None:
        """Register an additional baseline for an environment."""
        base_rec = BaselineRecord(
            baseline_id=baseline_id,
            environment_id=environment_id,
            display_name=display_name or f"Baseline {baseline_id}",
            is_verified=True,
            checksum_or_state="mock-checksum",
        )
        self._baselines.setdefault(environment_id, {})[baseline_id] = base_rec
        env = self._environments.get(environment_id)
        if env and baseline_id not in env.baseline_ids:
            env.baseline_ids.append(baseline_id)

    def is_available(self) -> bool:
        return True

    def get_adapter_type(self) -> AdapterType:
        return AdapterType.FAKE

    def list_registered_environments(self) -> list[EnvironmentRecord]:
        return list(self._environments.values())

    def inspect_environment(self, environment_id: str) -> EnvironmentRecord | None:
        return self._environments.get(environment_id)

    def inspect_baseline(self, environment_id: str, baseline_id: str) -> BaselineRecord | None:
        return self._baselines.get(environment_id, {}).get(baseline_id)

    def restore_baseline(self, environment_id: str, baseline_id: str) -> AdapterOperationResult:
        self.call_history.append(("restore_baseline", (environment_id, baseline_id)))
        if self.fail_restore:
            return AdapterOperationResult(
                success=False,
                duration_ms=5.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason="Injected baseline restore failure in FakeAdapter.",
            )
        self.applied_changes.clear()
        if hasattr(self, "restore_hook") and self.restore_hook is not None:
            self.restore_hook(environment_id, baseline_id)
        env = self._environments.get(environment_id)
        if env:
            env.status = EnvironmentStatus.VERIFIED
        return AdapterOperationResult(
            success=True,
            duration_ms=1.5,
            environment_id=environment_id,
            baseline_id=baseline_id,
            output="Restored fake baseline successfully.",
        )

    def verify_baseline(self, environment_id: str, baseline_id: str) -> AdapterOperationResult:
        self.call_history.append(("verify_baseline", (environment_id, baseline_id)))
        if self.fail_verify:
            return AdapterOperationResult(
                success=False,
                duration_ms=5.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason="Injected baseline verify failure in FakeAdapter.",
            )
        env = self._environments.get(environment_id)
        if env:
            env.status = EnvironmentStatus.VERIFIED
        return AdapterOperationResult(
            success=True,
            duration_ms=2.0,
            environment_id=environment_id,
            baseline_id=baseline_id,
            output="Verified fake baseline successfully.",
        )

    def apply_registered_change(
        self, environment_id: str, change_id: str, parameters: dict[str, Any]
    ) -> AdapterOperationResult:
        self.call_history.append(("apply_change", (environment_id, change_id, parameters)))
        if self.fail_apply:
            return AdapterOperationResult(
                success=False,
                duration_ms=5.0,
                environment_id=environment_id,
                error_reason=f"Injected change application failure for '{change_id}'.",
            )
        self.applied_changes.append(change_id)
        return AdapterOperationResult(
            success=True,
            duration_ms=3.0,
            environment_id=environment_id,
            output=f"Applied fake change '{change_id}'.",
        )

    def run_health_check(
        self, environment_id: str, check_id: str, parameters: dict[str, Any]
    ) -> HealthCheckResult:
        self.call_history.append(("run_health_check", (environment_id, check_id, parameters)))
        if hasattr(self, "custom_check_callback") and self.custom_check_callback is not None:
            res = self.custom_check_callback(self.applied_changes)
            if isinstance(res, tuple):
                return HealthCheckResult(outcome=res[0], output=res[1])
            elif isinstance(res, HealthCheckResult):
                return res
            elif isinstance(res, HealthCheckOutcome):
                return HealthCheckResult(outcome=res, output="")

        if self.fail_health_check:
            return HealthCheckResult(
                outcome=HealthCheckOutcome.FAIL,
                output="Injected health check failure.",
            )
        # If specific failing changes are applied
        failing_subset = parameters.get("failing_changes", ["fail_trigger"])
        if any(c in self.applied_changes for c in failing_subset):
            return HealthCheckResult(
                outcome=HealthCheckOutcome.FAIL,
                output=f"Failure reproduced by applied changes: {self.applied_changes}",
            )
        return HealthCheckResult(
            outcome=HealthCheckOutcome.PASS,
            output="Fake health check passed.",
        )

    def collect_trial_evidence(self, environment_id: str, trial_id: str) -> dict[str, Any]:
        return {
            "trial_id": trial_id,
            "environment_id": environment_id,
            "adapter_type": AdapterType.FAKE.value,
            "applied_changes": list(self.applied_changes),
            "call_history_len": len(self.call_history),
        }

    def cleanup_trial(self, environment_id: str, trial_id: str = "") -> AdapterOperationResult:
        self.call_history.append(("cleanup_trial", (environment_id, trial_id)))
        if self.fail_cleanup:
            env = self._environments.get(environment_id)
            if env and self.corrupt_on_cleanup_failure:
                env.status = EnvironmentStatus.CORRUPTED
            return AdapterOperationResult(
                success=False,
                duration_ms=5.0,
                environment_id=environment_id,
                error_reason="Injected cleanup failure in FakeAdapter.",
            )
        self.applied_changes.clear()
        return AdapterOperationResult(
            success=True,
            duration_ms=1.0,
            environment_id=environment_id,
            output="Cleaned up fake trial successfully.",
        )
