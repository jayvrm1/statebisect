"""Simulation adapter wrapping SimulatedWorkspace behind the BaseEnvironmentAdapter interface."""

from __future__ import annotations

import time
from typing import Any

from statebisect.models import Change, HealthCheckOutcome, HealthCheckResult
from statebisect.simulation import BaselineIntegrityError, SimulatedWorkspace

from .base import BaseEnvironmentAdapter
from .models import (
    AdapterOperationResult,
    AdapterType,
    BaselineRecord,
    EnvironmentRecord,
    EnvironmentStatus,
)


class SimulationAdapter(BaseEnvironmentAdapter):
    """Safe, in-memory simulation adapter for testing and rapid delta debugging.
    
    Serves as the default adapter for StateBisect. Performs zero host modifications.
    """

    DEFAULT_ENV_ID = "sim-env-default"
    DEFAULT_BASELINE_ID = "baseline-001"

    def __init__(self, workspace: SimulatedWorkspace | None = None) -> None:
        self.workspace = workspace or SimulatedWorkspace(
            baseline_state={"status": "healthy", "config.status": "healthy"}
        )
        self._environments: dict[str, EnvironmentRecord] = {}
        self._baselines: dict[str, dict[str, BaselineRecord]] = {}

        # Register default disposable simulation environment
        env = EnvironmentRecord(
            environment_id=self.DEFAULT_ENV_ID,
            adapter_type=AdapterType.SIMULATION,
            display_name="Default In-Memory Simulation Workspace",
            is_disposable=True,
            is_host=False,
            status=EnvironmentStatus.VERIFIED,
            baseline_ids=[self.DEFAULT_BASELINE_ID],
            configuration={"workspace_type": "in_memory_dict"},
        )
        self._environments[self.DEFAULT_ENV_ID] = env
        self._baselines[self.DEFAULT_ENV_ID] = {
            self.DEFAULT_BASELINE_ID: BaselineRecord(
                baseline_id=self.DEFAULT_BASELINE_ID,
                environment_id=self.DEFAULT_ENV_ID,
                display_name="Initial Clean In-Memory State",
                is_verified=True,
                checksum_or_state=str(hash(frozenset(self.workspace.current_state.items()))),
            )
        }

    def is_available(self) -> bool:
        """Simulation is always available in all supported Python environments."""
        return True

    def get_adapter_type(self) -> AdapterType:
        return AdapterType.SIMULATION

    def get_workspace(self, environment_id: str = "") -> SimulatedWorkspace:
        """Return the underlying simulation workspace."""
        return self.workspace

    def list_registered_environments(self) -> list[EnvironmentRecord]:
        return list(self._environments.values())

    def inspect_environment(self, environment_id: str) -> EnvironmentRecord | None:
        return self._environments.get(environment_id)

    def inspect_baseline(self, environment_id: str, baseline_id: str) -> BaselineRecord | None:
        return self._baselines.get(environment_id, {}).get(baseline_id)

    def restore_baseline(self, environment_id: str, baseline_id: str) -> AdapterOperationResult:
        t0 = time.perf_counter()
        env = self._environments.get(environment_id)
        if not env:
            return AdapterOperationResult(
                success=False,
                duration_ms=0.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Environment '{environment_id}' not found.",
            )

        try:
            self.workspace.reset_to_baseline()
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return AdapterOperationResult(
                success=True,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                baseline_id=baseline_id,
                output="Simulation workspace reset to baseline.",
            )
        except Exception as err:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            env.status = EnvironmentStatus.CORRUPTED
            return AdapterOperationResult(
                success=False,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Simulation reset failed: {err}",
            )

    def verify_baseline(self, environment_id: str, baseline_id: str) -> AdapterOperationResult:
        t0 = time.perf_counter()
        env = self._environments.get(environment_id)
        if not env:
            return AdapterOperationResult(
                success=False,
                duration_ms=0.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Environment '{environment_id}' not found.",
            )

        try:
            self.workspace.verify_baseline()
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return AdapterOperationResult(
                success=True,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                baseline_id=baseline_id,
                output="Baseline verified cleanly.",
            )
        except BaselineIntegrityError as err:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            env.status = EnvironmentStatus.CORRUPTED
            return AdapterOperationResult(
                success=False,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Baseline integrity verification failed: {err}",
            )

    def apply_registered_change(
        self, environment_id: str, change_id: str, parameters: dict[str, Any]
    ) -> AdapterOperationResult:
        t0 = time.perf_counter()
        try:
            # Construct simulated Change object with payload
            category = parameters.get("category", "config")
            key = parameters.get("key", change_id)
            val = parameters.get("value")
            payload = {key: val, f"{category}.{key}": val}
            chg = Change(
                id=change_id,
                category=category,
                description=f"Simulated change {change_id}",
                payload=payload,
            )
            self.workspace.apply_changes([chg])
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return AdapterOperationResult(
                success=True,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                output=f"Applied simulated change '{change_id}'.",
            )
        except Exception as err:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return AdapterOperationResult(
                success=False,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                error_reason=str(err),
            )

    def run_health_check(
        self, environment_id: str, check_id: str, parameters: dict[str, Any]
    ) -> HealthCheckResult:
        # Default key-value check on workspace
        target_key = parameters.get("key", "status")
        expected_val = parameters.get("expected", "healthy")
        actual_val = self.workspace.current_state.get(target_key)
        if actual_val is None:
            actual_val = self.workspace.current_state.get(f"config.{target_key}")

        if actual_val == expected_val:
            return HealthCheckResult(
                outcome=HealthCheckOutcome.PASS,
                output=f"Key '{target_key}' matches expected '{expected_val}'.",
            )
        else:
            return HealthCheckResult(
                outcome=HealthCheckOutcome.FAIL,
                output=f"Key '{target_key}' is '{actual_val}', expected '{expected_val}'.",
            )

    def collect_trial_evidence(self, environment_id: str, trial_id: str) -> dict[str, Any]:
        return {
            "trial_id": trial_id,
            "environment_id": environment_id,
            "adapter_type": AdapterType.SIMULATION.value,
            "current_state_summary": dict(self.workspace.current_state),
        }

    def cleanup_trial(self, environment_id: str, trial_id: str = "") -> AdapterOperationResult:
        return self.restore_baseline(environment_id, self.DEFAULT_BASELINE_ID)
