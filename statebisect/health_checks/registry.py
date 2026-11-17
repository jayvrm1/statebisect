"""Registry and factory for pluggable health checks."""

from __future__ import annotations

from typing import Any

from .base import BaseHealthCheck
from .http_check import LoopbackHttpCheck
from .process_check import RegisteredProcessCheck
from .simulated_state import SimulatedStateCheck

SUPPORTED_CHECK_TYPES = frozenset(
    ["key_value_match", "compound_condition", "simulated_state", "registered_process", "loopback_http"]
)
SUPPORTED_CHECK_VERSIONS = frozenset(["1.0.0"])


class HealthCheckConfigurationError(ValueError):
    """Raised when health check parameters or types are invalid."""


def create_health_check(
    check_type: str,
    check_version: str = "1.0.0",
    name: str = "",
    description: str = "",
    timeout_ms: int = 5000,
    repeat_count: int = 1,
    required_consistency: str = "ALL_MATCH",
    parameters: dict[str, Any] | None = None,
) -> BaseHealthCheck:
    """Instantiate a validated health check instance."""
    if check_type not in SUPPORTED_CHECK_TYPES:
        raise HealthCheckConfigurationError(
            f"Unsupported health check type '{check_type}'. Supported types: {sorted(SUPPORTED_CHECK_TYPES)}"
        )

    if check_version not in SUPPORTED_CHECK_VERSIONS:
        raise HealthCheckConfigurationError(
            f"Unsupported health check version '{check_version}'. Supported versions: {sorted(SUPPORTED_CHECK_VERSIONS)}"
        )

    params = parameters or {}

    if check_type in ("key_value_match", "compound_condition", "simulated_state"):
        # For legacy compatibility, mode can be explicit or inferred
        state_params = dict(params)
        if "mode" not in state_params and check_type in ("key_value_match", "compound_condition"):
            state_params["mode"] = check_type
        return SimulatedStateCheck(
            check_type=check_type,
            check_version=check_version,
            name=name,
            description=description,
            timeout_ms=timeout_ms,
            repeat_count=repeat_count,
            required_consistency=required_consistency,
            parameters=state_params,
        )

    elif check_type == "registered_process":
        return RegisteredProcessCheck(
            check_type=check_type,
            check_version=check_version,
            name=name,
            description=description,
            timeout_ms=timeout_ms,
            repeat_count=repeat_count,
            required_consistency=required_consistency,
            parameters=params,
        )

    elif check_type == "loopback_http":
        return LoopbackHttpCheck(
            check_type=check_type,
            check_version=check_version,
            name=name,
            description=description,
            timeout_ms=timeout_ms,
            repeat_count=repeat_count,
            required_consistency=required_consistency,
            parameters=params,
        )

    raise HealthCheckConfigurationError(f"Unhandled health check type '{check_type}'")
