"""Isolated simulated workspace for safe, non-destructive experimentation."""

from __future__ import annotations

import copy
from typing import Any, Callable

from .models import Change, HealthCheckOutcome, HealthCheckResult


class DependencyResolutionError(Exception):
    """Raised when change dependencies are missing or cyclic."""


class BaselineIntegrityError(Exception):
    """Raised when the workspace fails to restore or verify baseline."""


class SimulatedWorkspace:
    """In-memory simulated environment for testing configuration changes safely."""

    def __init__(self, baseline_state: dict[str, Any] | None = None) -> None:
        self._baseline_state: dict[str, Any] = copy.deepcopy(baseline_state or {})
        self._current_state: dict[str, Any] = copy.deepcopy(self._baseline_state)
        self._applied_change_ids: list[str] = []
        self._reset_count: int = 0

    @property
    def current_state(self) -> dict[str, Any]:
        """Return a copy of the current state."""
        return copy.deepcopy(self._current_state)

    @property
    def applied_change_ids(self) -> list[str]:
        return list(self._applied_change_ids)

    def reset_to_baseline(self) -> None:
        """Restore workspace to exact baseline state."""
        self._current_state = copy.deepcopy(self._baseline_state)
        self._applied_change_ids.clear()
        self._reset_count += 1
        if not self.verify_baseline():
            raise BaselineIntegrityError("Workspace state does not match baseline after reset.")

    def verify_baseline(self) -> bool:
        """Verify that current state exactly matches the baseline state."""
        return self._current_state == self._baseline_state and len(self._applied_change_ids) == 0

    def apply_changes(self, changes: list[Change]) -> list[str]:
        """Apply changes in dependency-safe order.

        Args:
            changes: List of Change objects to apply.

        Returns:
            List of change IDs in the order they were applied.
        """
        change_map = {c.id: c for c in changes}
        selected_ids = set(change_map.keys())

        # Check for missing dependencies
        for c in changes:
            for dep in c.dependencies:
                if dep not in selected_ids:
                    raise DependencyResolutionError(
                        f"Change '{c.id}' depends on '{dep}', which is not in the selected change set."
                    )

        # Topological sort (Kahn's algorithm) to establish safe application order
        in_degree = {c.id: len(c.dependencies) for c in changes}
        graph: dict[str, list[str]] = {c.id: [] for c in changes}
        for c in changes:
            for dep in c.dependencies:
                graph[dep].append(c.id)

        queue = [cid for cid, deg in in_degree.items() if deg == 0]
        queue.sort()
        ordered_ids: list[str] = []

        while queue:
            node = queue.pop(0)
            ordered_ids.append(node)
            for neighbor in sorted(graph[node]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(ordered_ids) != len(changes):
            raise DependencyResolutionError("Cyclic dependency detected among selected changes.")

        # Apply state modifications in determined order
        for cid in ordered_ids:
            change = change_map[cid]
            op = change.metadata.get("operation")
            params = change.metadata.get("parameters", {})

            if op == "delete_key":
                key = params.get("key")
                if key:
                    self._current_state.pop(key, None)
            elif op == "append_list":
                key = params.get("key")
                item = params.get("item")
                if key:
                    curr_list = list(self._current_state.get(key, []))
                    curr_list.append(copy.deepcopy(item))
                    self._current_state[key] = curr_list
            else:
                # Default: key-value dictionary overlay
                for key, val in change.payload.items():
                    self._current_state[key] = copy.deepcopy(val)

            self._applied_change_ids.append(cid)

        return ordered_ids


# Health Check Protocol type
HealthCheckFn = Callable[[SimulatedWorkspace], HealthCheckResult]


def create_key_value_health_check(
    failing_keys_condition: Callable[[dict[str, Any]], tuple[HealthCheckOutcome, str]]
) -> HealthCheckFn:
    """Factory for standard rule-based health checks."""

    def check(ws: SimulatedWorkspace) -> HealthCheckResult:
        outcome, message = failing_keys_condition(ws.current_state)
        return HealthCheckResult(outcome=outcome, output=message)

    return check
