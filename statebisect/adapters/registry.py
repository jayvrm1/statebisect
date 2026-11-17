"""Registry for environment adapters and safe, allowlisted change actions."""

from __future__ import annotations

from typing import Any

from .base import BaseEnvironmentAdapter
from .fake_adapter import FakeAdapter
from .hyperv_adapter import HyperVAdapter
from .models import AdapterType, RegisteredChangeAction
from .simulation_adapter import SimulationAdapter


class RegisteredActionRegistry:
    """Allowlist and validator for actions that can be applied to an experiment environment.

    Safety Invariant:
        Candidate change descriptions, evidence snippets, or raw JSON must NEVER be
        executed as raw commands or shell scripts. Only explicitly registered actions
        with validated parameter schemas may be executed.
    """

    def __init__(self) -> None:
        self._actions: dict[str, RegisteredChangeAction] = {}
        self._register_default_actions()

    def _register_default_actions(self) -> None:
        # Action: Safe key-value update in an isolated test fixture
        self.register_action(
            RegisteredChangeAction(
                action_id="fixture_config_set",
                name="Set Test Fixture Configuration Value",
                description="Updates a key-value setting in an isolated test application configuration file.",
                risk_level="LOW",
                allowed_parameters=["fixture_path", "section", "key", "value"],
                reversibility="AUTOMATIC",
            )
        )
        self.register_action(
            RegisteredChangeAction(
                action_id="simulated_state_set",
                name="Set Simulated State Key",
                description="Sets a key-value pair in a simulated workspace memory state.",
                risk_level="LOW",
                allowed_parameters=["category", "key", "value"],
                reversibility="AUTOMATIC",
            )
        )

    def register_action(self, action: RegisteredChangeAction) -> None:
        self._actions[action.action_id] = action

    def get_action(self, action_id: str) -> RegisteredChangeAction | None:
        return self._actions.get(action_id)

    def list_actions(self) -> list[RegisteredChangeAction]:
        return list(self._actions.values())

    def validate_action_invocation(self, action_id: str, parameters: dict[str, Any]) -> None:
        """Validate that an action ID is allowlisted and its parameters are safe."""
        action = self.get_action(action_id)
        if not action:
            raise PermissionError(
                f"Safety Violation: Action ID '{action_id}' is not registered in the action allowlist. "
                "Arbitrary or unallowlisted actions are rejected."
            )
        if action.risk_level.upper() in ("HIGH", "CRITICAL", "FORBIDDEN"):
            raise PermissionError(
                f"Safety Violation: Action '{action_id}' has risk level '{action.risk_level}' "
                "and is rejected for automated experiment execution."
            )
        # Check for disallowed parameter keys (e.g. 'command', 'shell', 'script', 'powershell')
        disallowed_keys = {"command", "cmd", "shell", "powershell", "script", "raw_exec", "run"}
        found_disallowed = disallowed_keys.intersection(parameters.keys())
        if found_disallowed:
            raise ValueError(
                f"Safety Violation: Disallowed executable keys detected in parameters: {found_disallowed}. "
                "Passing arbitrary command strings is strictly forbidden."
            )


class AdapterRegistry:
    """Manages available environment adapters."""

    def __init__(self) -> None:
        self._adapters: dict[AdapterType, BaseEnvironmentAdapter] = {}
        self._action_registry = RegisteredActionRegistry()

        # Register default adapters
        self.register_adapter(SimulationAdapter())
        self.register_adapter(HyperVAdapter())
        self.register_adapter(FakeAdapter())

    def register_adapter(self, adapter: BaseEnvironmentAdapter) -> None:
        self._adapters[adapter.get_adapter_type()] = adapter

    def get_adapter(self, adapter_type: AdapterType | str) -> BaseEnvironmentAdapter | None:
        if isinstance(adapter_type, str):
            try:
                adapter_type = AdapterType(adapter_type.upper())
            except ValueError:
                return None
        return self._adapters.get(adapter_type)

    def get_default_adapter(self) -> BaseEnvironmentAdapter:
        """Simulation is always the default adapter."""
        return self._adapters[AdapterType.SIMULATION]

    def list_adapters(self) -> list[dict[str, Any]]:
        results = []
        for atype, adp in self._adapters.items():
            results.append({
                "adapter_type": atype.value,
                "is_available": adp.is_available(),
                "registered_environments": len(adp.list_registered_environments()),
                "is_default": atype == AdapterType.SIMULATION,
            })
        return results

    @property
    def action_registry(self) -> RegisteredActionRegistry:
        return self._action_registry

