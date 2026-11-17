"""Base contract and typed interface for isolated experiment adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from statebisect.models import HealthCheckResult

from .models import (
    AdapterOperationResult,
    AdapterType,
    BaselineRecord,
    EnvironmentRecord,
)


class BaseEnvironmentAdapter(ABC):
    """Abstract interface defining required operations for experiment environment adapters.
    
    Guarantees:
        1. Explicit, typed operations with zero arbitrary command strings or shell execution.
        2. Structured results containing duration, environment identity, and failure reasons.
        3. Baseline restore and verification before every trial and cleanup after every trial.
    """

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether the virtualization environment/hypervisor is currently available."""
        ...

    @abstractmethod
    def get_adapter_type(self) -> AdapterType:
        """Return the unique adapter type identifier."""
        ...

    @abstractmethod
    def list_registered_environments(self) -> list[EnvironmentRecord]:
        """List environments explicitly registered with this adapter."""
        ...

    @abstractmethod
    def inspect_environment(self, environment_id: str) -> EnvironmentRecord | None:
        """Retrieve details and readiness status for a specific environment."""
        ...

    @abstractmethod
    def inspect_baseline(self, environment_id: str, baseline_id: str) -> BaselineRecord | None:
        """Inspect a named baseline / checkpoint record for an environment."""
        ...

    @abstractmethod
    def restore_baseline(self, environment_id: str, baseline_id: str) -> AdapterOperationResult:
        """Restore the environment to the exact named baseline state.
        
        Must be executed prior to applying any candidate changes in a trial.
        """
        ...

    @abstractmethod
    def verify_baseline(self, environment_id: str, baseline_id: str) -> AdapterOperationResult:
        """Verify the integrity of the restored baseline before applying changes.
        
        If verification fails, the trial must abort immediately to prevent false attribution.
        """
        ...

    @abstractmethod
    def apply_registered_change(
        self, environment_id: str, change_id: str, parameters: dict[str, Any]
    ) -> AdapterOperationResult:
        """Apply an allowlisted, registered configuration action inside the environment.
        
        Arbitrary scripts, unparsed command lines, and unregistered action IDs must be rejected.
        """
        ...

    @abstractmethod
    def run_health_check(
        self, environment_id: str, check_id: str, parameters: dict[str, Any]
    ) -> HealthCheckResult:
        """Execute the configured health check within the environment."""
        ...

    @abstractmethod
    def collect_trial_evidence(self, environment_id: str, trial_id: str) -> dict[str, Any]:
        """Extract bounded diagnostic evidence from the environment for the trial."""
        ...

    @abstractmethod
    def cleanup_trial(self, environment_id: str, trial_id: str) -> AdapterOperationResult:
        """Restore baseline and clean up any transient resources after a trial completes."""
        ...

