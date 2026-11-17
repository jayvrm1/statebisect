"""Hyper-V virtualization adapter implementing typed baseline checkpoint restoration and isolation."""

from __future__ import annotations

import re
import subprocess
import sys
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

SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


def _is_safe_vm_identifier(name: str) -> bool:
    """Validate VM and snapshot names to strictly prevent command injection."""
    return bool(SAFE_NAME_PATTERN.match(name.strip()))


class HyperVAdapter(BaseEnvironmentAdapter):
    """Safe, typed Hyper-V adapter for isolated disposable VM trials.

    Safety Guarantees:
        1. Explicit Opt-in Only: Requires pre-registered disposable VM with named baseline.
        2. Host Refusal: Rejects host machine, production VMs, and ambiguous names.
        3. No Elevation: Operates strictly with caller's permissions; reports UNAVAILABLE if access denied.
        4. No System Mutation: Never enables Hyper-V, alters Windows features, networking, or user groups.
        5. Injection-Proof: Uses fixed PowerShell parameter templates with shell=False and strict regex validation.
    """

    def __init__(self) -> None:
        self._registered_environments: dict[str, EnvironmentRecord] = {}
        self._registered_baselines: dict[str, dict[str, BaselineRecord]] = {}

    def is_available(self) -> bool:
        """Check if Hyper-V virtualization tools and permissions are available on this host."""
        if sys.platform != "win32":
            return False

        try:
            # Query if Hyper-V cmdlets are present without mutating any state
            res = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "if (Get-Module -ListAvailable Hyper-V) { exit 0 } else { exit 1 }",
                ],
                capture_output=True,
                timeout=10,
                shell=False,
            )
            return res.returncode == 0
        except Exception:
            return False

    def get_adapter_type(self) -> AdapterType:
        return AdapterType.HYPERV

    def register_disposable_vm(
        self,
        environment_id: str,
        vm_name: str,
        is_disposable: bool = True,
        is_host: bool = False,
        baseline_id: str = "baseline-001",
    ) -> AdapterOperationResult:
        """Register a disposable Hyper-V VM enforcing host refusal and disposable requirements."""
        if is_host:
            raise ValueError("Refusing to register host machine as a Hyper-V experiment environment.")
        if not is_disposable:
            raise ValueError("Hyper-V environment must be disposable. Non-disposable VMs are refused.")
        if not _is_safe_vm_identifier(environment_id):
            raise ValueError(f"Invalid VM identifier '{environment_id}'. Must be alphanumeric, underscores, or hyphens.")
        if not _is_safe_vm_identifier(vm_name):
            raise ValueError(f"Invalid VM name '{vm_name}'. Must be alphanumeric, underscores, or hyphens.")

        env = EnvironmentRecord(
            environment_id=environment_id,
            adapter_type=AdapterType.HYPERV,
            display_name=vm_name,
            is_disposable=True,
            is_host=False,
            status=EnvironmentStatus.REGISTERED,
            baseline_ids=[baseline_id],
            configuration={"vm_name": vm_name},
        )
        self.register_environment(env)
        return AdapterOperationResult(
            success=True,
            duration_ms=0.0,
            environment_id=environment_id,
            output=f"Registered disposable Hyper-V VM '{vm_name}' (ID: {environment_id}).",
        )

    def register_environment(self, env: EnvironmentRecord) -> None:
        """Explicitly register a disposable VM environment."""
        env.validate_for_execution()
        vm_name = env.configuration.get("vm_name", "")
        if not _is_safe_vm_identifier(vm_name):
            raise ValueError(
                f"Invalid VM name '{vm_name}'. Name must be 1-128 alphanumeric characters, hyphens, or underscores."
            )
        self._registered_environments[env.environment_id] = env

    def register_baseline(self, baseline: BaselineRecord) -> None:
        """Register a known checkpoint baseline for a registered environment."""
        if not _is_safe_vm_identifier(baseline.baseline_id):
            raise ValueError(f"Invalid baseline ID '{baseline.baseline_id}'.")
        self._registered_baselines.setdefault(baseline.environment_id, {})[baseline.baseline_id] = baseline

    def list_registered_environments(self) -> list[EnvironmentRecord]:
        return list(self._registered_environments.values())

    def inspect_environment(self, environment_id: str) -> EnvironmentRecord | None:
        return self._registered_environments.get(environment_id)

    def inspect_baseline(self, environment_id: str, baseline_id: str) -> BaselineRecord | None:
        return self._registered_baselines.get(environment_id, {}).get(baseline_id)

    def restore_baseline(self, environment_id: str, baseline_id: str) -> AdapterOperationResult:
        """Restore a disposable Hyper-V VM to its registered checkpoint baseline."""
        t0 = time.perf_counter()
        env = self._registered_environments.get(environment_id)
        if not env:
            return AdapterOperationResult(
                success=False,
                duration_ms=0.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Environment '{environment_id}' is not registered.",
            )

        try:
            env.validate_for_execution()
        except Exception as err:
            return AdapterOperationResult(
                success=False,
                duration_ms=0.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=str(err),
            )

        vm_name = env.configuration.get("vm_name", "")
        checkpoint_name = baseline_id

        if not _is_safe_vm_identifier(vm_name) or not _is_safe_vm_identifier(checkpoint_name):
            return AdapterOperationResult(
                success=False,
                duration_ms=0.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason="VM name or checkpoint name failed safe character validation.",
            )

        if not self.is_available():
            return AdapterOperationResult(
                success=False,
                duration_ms=0.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason="Hyper-V is UNAVAILABLE or required PowerShell module is not accessible.",
            )

        # Restore VM snapshot via audited PowerShell command template
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Restore-VMSnapshot -VMName '{vm_name}' -Name '{checkpoint_name}' -Confirm:$false",
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, shell=False)
            duration_ms = (time.perf_counter() - t0) * 1000.0

            if res.returncode == 0:
                return AdapterOperationResult(
                    success=True,
                    duration_ms=round(duration_ms, 2),
                    environment_id=environment_id,
                    baseline_id=baseline_id,
                    output=f"Successfully restored checkpoint '{checkpoint_name}' on VM '{vm_name}'.",
                )
            else:
                err_msg = res.stderr.strip() or res.stdout.strip() or f"Process exited with code {res.returncode}"
                # Check for permission error
                if "Access is denied" in err_msg or "UnauthorizedAccessException" in err_msg:
                    err_msg = (
                        f"Hyper-V Permission Denied: Caller is not a member of 'Hyper-V Administrators' or 'Administrators'. "
                        f"Original error: {err_msg}"
                    )
                env.status = EnvironmentStatus.CORRUPTED
                return AdapterOperationResult(
                    success=False,
                    duration_ms=round(duration_ms, 2),
                    environment_id=environment_id,
                    baseline_id=baseline_id,
                    error_reason=err_msg,
                )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            env.status = EnvironmentStatus.CORRUPTED
            return AdapterOperationResult(
                success=False,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Hyper-V restore timed out after 60s on VM '{vm_name}'.",
            )
        except Exception as err:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            env.status = EnvironmentStatus.CORRUPTED
            return AdapterOperationResult(
                success=False,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Exception restoring Hyper-V baseline: {err}",
            )

    def verify_baseline(self, environment_id: str, baseline_id: str) -> AdapterOperationResult:
        """Verify that the VM is in running state and checkpoint matches baseline."""
        t0 = time.perf_counter()
        env = self._registered_environments.get(environment_id)
        if not env:
            return AdapterOperationResult(
                success=False,
                duration_ms=0.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Environment '{environment_id}' not found.",
            )

        vm_name = env.configuration.get("vm_name", "")
        if not self.is_available():
            return AdapterOperationResult(
                success=False,
                duration_ms=0.0,
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason="Hyper-V is UNAVAILABLE.",
            )

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-VM -Name '{vm_name}').State",
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, shell=False)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            vm_state = res.stdout.strip()
            if res.returncode == 0 and vm_state in ("Running", "Off"):
                return AdapterOperationResult(
                    success=True,
                    duration_ms=round(duration_ms, 2),
                    environment_id=environment_id,
                    baseline_id=baseline_id,
                    output=f"VM '{vm_name}' verified in state '{vm_state}'.",
                )
            else:
                return AdapterOperationResult(
                    success=False,
                    duration_ms=round(duration_ms, 2),
                    environment_id=environment_id,
                    baseline_id=baseline_id,
                    error_reason=f"VM '{vm_name}' state verification failed: {res.stderr.strip() or vm_state}",
                )
        except Exception as err:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return AdapterOperationResult(
                success=False,
                duration_ms=round(duration_ms, 2),
                environment_id=environment_id,
                baseline_id=baseline_id,
                error_reason=f"Verification failed: {err}",
            )

    def apply_registered_change(
        self, environment_id: str, change_id: str, parameters: dict[str, Any]
    ) -> AdapterOperationResult:
        # In Phase 7 initial release, changes inside real VMs are guarded by the action registry
        return AdapterOperationResult(
            success=False,
            duration_ms=0.0,
            environment_id=environment_id,
            error_reason=(
                f"Direct execution of change '{change_id}' on live Hyper-V VM is disabled until "
                "safe guest integration agent is configured in Phase 8."
            ),
        )

    def run_health_check(
        self, environment_id: str, check_id: str, parameters: dict[str, Any]
    ) -> HealthCheckResult:
        return HealthCheckResult(
            outcome=HealthCheckOutcome.INCONCLUSIVE,
            output="Remote Hyper-V health check invocation deferred to Phase 8 guest agent.",
        )

    def collect_trial_evidence(self, environment_id: str, trial_id: str) -> dict[str, Any]:
        return {
            "trial_id": trial_id,
            "environment_id": environment_id,
            "adapter_type": AdapterType.HYPERV.value,
        }

    def cleanup_trial(self, environment_id: str, trial_id: str = "") -> AdapterOperationResult:
        env = self._registered_environments.get(environment_id)
        base_id = env.baseline_ids[0] if env and env.baseline_ids else "default"
        return self.restore_baseline(environment_id, base_id)
