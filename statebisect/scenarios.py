"""Declarative experiment scenario format, schema validation, and loading."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CURRENT_SCHEMA_VERSION = "1.0.0"
ALLOWED_OPERATIONS = frozenset(["set_key", "delete_key", "append_list", "replace_value"])
ALLOWED_HEALTH_CHECK_TYPES = frozenset(["key_value_match", "compound_condition"])
FORBIDDEN_EXECUTABLE_KEYS = frozenset(
    ["command", "cmd", "script", "exec", "eval", "powershell", "bash", "code", "shell", "run"]
)


class ScenarioValidationError(Exception):
    """Raised when a scenario JSON fails strict schema validation."""


@dataclass(frozen=True)
class ScenarioChange:
    id: str
    category: str
    description: str
    dependencies: tuple[str, ...]
    operation: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ScenarioHealthCheck:
    type: str
    parameters: dict[str, Any]
    timeout_ms: int = 5000


@dataclass(frozen=True)
class DeclarativeScenario:
    schema_version: str
    id: str
    name: str
    description: str
    simulation: bool
    baseline_id: str
    baseline_state: dict[str, Any]
    changes: tuple[ScenarioChange, ...]
    health_check: ScenarioHealthCheck
    expected_outcome: str | None = None
    raw_content: str = ""
    content_hash: str = ""


def _scan_for_forbidden_keys(obj: Any, path: str = "root") -> None:
    """Recursively ensure no arbitrary executable or script commands are present."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ScenarioValidationError(f"Invalid non-string key at {path}: {k!r}")
            if k.lower() in FORBIDDEN_EXECUTABLE_KEYS:
                raise ScenarioValidationError(
                    f"Forbidden executable or script property '{k}' found at {path}. "
                    "StateBisect scenarios must be purely declarative data."
                )
            _scan_for_forbidden_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            _scan_for_forbidden_keys(item, f"{path}[{idx}]")


def _validate_safe_path(path_str: str) -> None:
    """Ensure path contains no directory traversal attempts."""
    if ".." in path_str.replace("\\", "/").split("/"):
        raise ScenarioValidationError(f"Path traversal ('..') is strictly prohibited: {path_str}")


def validate_and_parse_scenario(data: dict[str, Any], raw_json: str = "") -> DeclarativeScenario:
    """Strictly validate and parse scenario dictionary.

    Args:
        data: Parsed dictionary from JSON.
        raw_json: Raw JSON string for computing cryptographic SHA-256 hash.

    Returns:
        DeclarativeScenario instance.

    Raises:
        ScenarioValidationError: If validation fails for any reason.
    """
    _scan_for_forbidden_keys(data)

    # 1. Schema version
    schema_ver = data.get("schema_version")
    if not schema_ver:
        raise ScenarioValidationError("Missing required field 'schema_version'.")
    if schema_ver != CURRENT_SCHEMA_VERSION:
        raise ScenarioValidationError(
            f"Unsupported schema_version '{schema_ver}'. Supported version is '{CURRENT_SCHEMA_VERSION}'."
        )

    # 2. Top-level metadata
    scenario_id = data.get("id")
    if not scenario_id or not isinstance(scenario_id, str):
        raise ScenarioValidationError("Scenario must have a non-empty string 'id'.")

    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ScenarioValidationError("Scenario must have a non-empty string 'name'.")

    description = data.get("description", "")
    if not isinstance(description, str):
        raise ScenarioValidationError("'description' must be a string.")

    simulation = data.get("simulation")
    if simulation is not True:
        raise ScenarioValidationError(
            "Property 'simulation' must be explicitly True. Live system modification is not permitted."
        )

    # 3. Baseline definition
    baseline = data.get("baseline")
    if not isinstance(baseline, dict):
        raise ScenarioValidationError("Scenario must specify a 'baseline' dictionary.")
    baseline_id = baseline.get("baseline_id")
    if not baseline_id or not isinstance(baseline_id, str):
        raise ScenarioValidationError("Baseline must specify a non-empty string 'baseline_id'.")
    baseline_state = baseline.get("initial_state")
    if not isinstance(baseline_state, dict):
        raise ScenarioValidationError("Baseline must specify an 'initial_state' dictionary.")

    # 4. Changes definition
    changes_raw = data.get("changes")
    if not isinstance(changes_raw, list) or len(changes_raw) == 0:
        raise ScenarioValidationError("Scenario must contain a non-empty list of 'changes'.")

    seen_ids: set[str] = set()
    parsed_changes: list[ScenarioChange] = []

    for idx, c in enumerate(changes_raw):
        if not isinstance(c, dict):
            raise ScenarioValidationError(f"Change at index {idx} must be a dictionary.")

        cid = c.get("id")
        if not cid or not isinstance(cid, str):
            raise ScenarioValidationError(f"Change at index {idx} must have a non-empty string 'id'.")
        if cid in seen_ids:
            raise ScenarioValidationError(f"Duplicate change id '{cid}' detected.")
        seen_ids.add(cid)

        category = c.get("category", "general")
        cdesc = c.get("description", "")
        deps = c.get("dependencies", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ScenarioValidationError(f"Change '{cid}' dependencies must be a list of strings.")

        op = c.get("operation")
        if not op or op not in ALLOWED_OPERATIONS:
            raise ScenarioValidationError(
                f"Change '{cid}' specifies invalid operation '{op}'. "
                f"Allowed operations: {sorted(ALLOWED_OPERATIONS)}"
            )

        params = c.get("parameters")
        if not isinstance(params, dict):
            raise ScenarioValidationError(f"Change '{cid}' must have a dictionary of 'parameters'.")

        # Validate parameters for operation
        if op in ("set_key", "replace_value"):
            if "key" not in params or not isinstance(params["key"], str):
                raise ScenarioValidationError(f"Operation '{op}' on '{cid}' requires a string 'key'.")
            if "value" not in params:
                raise ScenarioValidationError(f"Operation '{op}' on '{cid}' requires a 'value'.")
        elif op == "delete_key":
            if "key" not in params or not isinstance(params["key"], str):
                raise ScenarioValidationError(f"Operation '{op}' on '{cid}' requires a string 'key'.")
        elif op == "append_list":
            if "key" not in params or not isinstance(params["key"], str):
                raise ScenarioValidationError(f"Operation '{op}' on '{cid}' requires a string 'key'.")
            if "item" not in params:
                raise ScenarioValidationError(f"Operation '{op}' on '{cid}' requires an 'item'.")

        parsed_changes.append(
            ScenarioChange(
                id=cid,
                category=category,
                description=cdesc,
                dependencies=tuple(deps),
                operation=op,
                parameters=params,
            )
        )

    # Validate change dependencies
    for c in parsed_changes:
        for dep in c.dependencies:
            if dep not in seen_ids:
                raise ScenarioValidationError(
                    f"Change '{c.id}' depends on '{dep}', which does not exist in the scenario."
                )

    # Detect cycles (Kahn's algorithm)
    in_degree = {c.id: len(c.dependencies) for c in parsed_changes}
    graph: dict[str, list[str]] = {c.id: [] for c in parsed_changes}
    for c in parsed_changes:
        for dep in c.dependencies:
            graph[dep].append(c.id)

    queue = [cid for cid, deg in in_degree.items() if deg == 0]
    visited_count = 0
    while queue:
        node = queue.pop(0)
        visited_count += 1
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited_count != len(parsed_changes):
        raise ScenarioValidationError("Cyclic dependency detected among changes in scenario.")

    # 5. Health check definition
    hc_raw = data.get("health_check")
    if not isinstance(hc_raw, dict):
        raise ScenarioValidationError("Scenario must specify a 'health_check' dictionary.")

    hc_type = hc_raw.get("type")
    if not hc_type or hc_type not in ALLOWED_HEALTH_CHECK_TYPES:
        raise ScenarioValidationError(
            f"Invalid health check type '{hc_type}'. Allowed types: {sorted(ALLOWED_HEALTH_CHECK_TYPES)}"
        )

    hc_params = hc_raw.get("parameters")
    if not isinstance(hc_params, dict):
        raise ScenarioValidationError("Health check must specify a dictionary of 'parameters'.")

    # Validate health check parameters
    if hc_type == "key_value_match":
        if "key" not in hc_params or not isinstance(hc_params["key"], str):
            raise ScenarioValidationError("health_check 'key_value_match' requires string 'key'.")
        if "expected_value" not in hc_params:
            raise ScenarioValidationError("health_check 'key_value_match' requires 'expected_value'.")
    elif hc_type == "compound_condition":
        conditions = hc_params.get("conditions")
        if not isinstance(conditions, list) or len(conditions) == 0:
            raise ScenarioValidationError(
                "health_check 'compound_condition' requires non-empty list of 'conditions'."
            )
        for cidx, cond in enumerate(conditions):
            if not isinstance(cond, dict) or "key" not in cond or "op" not in cond:
                raise ScenarioValidationError(
                    f"Condition at index {cidx} must specify 'key' and 'op'."
                )
            if cond["op"] not in ("eq", "neq", "in", "nin"):
                raise ScenarioValidationError(f"Unsupported condition operator '{cond['op']}'.")

    timeout_ms = hc_raw.get("timeout_ms", 5000)
    if not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise ScenarioValidationError("'timeout_ms' must be a positive integer.")

    health_check = ScenarioHealthCheck(
        type=hc_type,
        parameters=hc_params,
        timeout_ms=timeout_ms,
    )

    expected_outcome = data.get("expected_outcome")
    content_hash = (
        hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        if raw_json
        else hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
    )

    return DeclarativeScenario(
        schema_version=schema_ver,
        id=scenario_id,
        name=name,
        description=description,
        simulation=simulation,
        baseline_id=baseline_id,
        baseline_state=baseline_state,
        changes=tuple(parsed_changes),
        health_check=health_check,
        expected_outcome=expected_outcome,
        raw_content=raw_json,
        content_hash=content_hash,
    )


def build_declarative_health_check(hc_def: ScenarioHealthCheck) -> Any:
    """Build a deterministic HealthCheckFn from a validated ScenarioHealthCheck."""
    from .models import HealthCheckOutcome, HealthCheckResult
    from .simulation import SimulatedWorkspace

    def health_check(ws: SimulatedWorkspace) -> HealthCheckResult:
        state = ws.current_state

        if hc_def.type == "key_value_match":
            key = hc_def.parameters["key"]
            expected = hc_def.parameters["expected_value"]
            actual = state.get(key)
            if actual == expected:
                return HealthCheckResult(
                    HealthCheckOutcome.FAIL,
                    f"Failure condition matched: key '{key}' has value {actual!r}",
                )
            return HealthCheckResult(
                HealthCheckOutcome.PASS,
                f"Health check passed: key '{key}' does not match failure value",
            )

        elif hc_def.type == "compound_condition":
            conditions = hc_def.parameters.get("conditions", [])
            matches = []
            for cond in conditions:
                key = cond["key"]
                op = cond["op"]
                target_val = cond.get("value")
                actual_val = state.get(key)

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
                    matches.append(f"{key} {op} {target_val!r}")
                else:
                    break

            if len(matches) == len(conditions) and len(conditions) > 0:
                return HealthCheckResult(
                    HealthCheckOutcome.FAIL,
                    f"Compound failure condition met: {', '.join(matches)}",
                )
            return HealthCheckResult(
                HealthCheckOutcome.PASS,
                "Health check passed: compound failure condition not satisfied",
            )

        return HealthCheckResult(HealthCheckOutcome.INCONCLUSIVE, f"Unknown check type '{hc_def.type}'")

    return health_check


def execute_scenario(
    scenario: DeclarativeScenario,
    storage: Any,
    cancellation_token: Any | None = None,
) -> tuple[Any, Any]:
    """Execute a validated declarative scenario through the deterministic runner and DeltaDebugger.

    Returns:
        tuple of (Investigation, MinimizationResult)
    """
    import uuid
    from .delta_debugging import DeltaDebugger
    from .models import (
        CancellationToken,
        Change,
        ENGINE_VERSION,
        Investigation,
        InvestigationStatus,
        utc_now_iso,
    )
    from .runner import ExperimentRunner
    from .simulation import SimulatedWorkspace

    inv_id = f"inv-{scenario.id}-{uuid.uuid4().hex[:6]}"
    inv = Investigation(
        id=inv_id,
        symptom=scenario.description,
        target_system=scenario.name,
        status=InvestigationStatus.RUNNING,
        baseline_id=scenario.baseline_id,
        scenario_id=scenario.id,
        scenario_hash=scenario.content_hash,
        scenario_snapshot=scenario.raw_content,
        engine_version=ENGINE_VERSION,
    )
    storage.save_investigation(inv)
    storage.log_audit(
        inv_id,
        "RUN_STARTED",
        f"Execution started for scenario '{scenario.id}'",
        details={"name": scenario.name, "hash": scenario.content_hash},
    )

    token = cancellation_token or CancellationToken()

    # Convert ScenarioChange instances to Change models
    changes: list[Change] = []
    for sc in scenario.changes:
        payload = {}
        if sc.operation in ("set_key", "replace_value"):
            payload[sc.parameters["key"]] = sc.parameters["value"]
        changes.append(
            Change(
                id=sc.id,
                category=sc.category,
                description=sc.description,
                dependencies=sc.dependencies,
                payload=payload,
                metadata={"operation": sc.operation, "parameters": sc.parameters},
            )
        )

    workspace = SimulatedWorkspace(baseline_state=scenario.baseline_state)
    runner = ExperimentRunner(workspace, storage, inv_id, cancellation_token=token)
    hc_fn = build_declarative_health_check(scenario.health_check)
    debugger = DeltaDebugger(runner, changes, hc_fn)

    result = debugger.minimize([c.id for c in changes])

    if result.verdict == "CANCELLED":
        inv.status = InvestigationStatus.CANCELLED
    elif result.verdict == "SIMULATION_CORRUPTED":
        inv.status = InvestigationStatus.FAILED
    elif result.is_1_minimal:
        inv.status = InvestigationStatus.VERIFIED
    elif result.verdict in ("INCONCLUSIVE", "INCONCLUSIVE_SUBSET"):
        inv.status = InvestigationStatus.INCONCLUSIVE
    else:
        inv.status = InvestigationStatus.FAILED

    inv.updated_at = utc_now_iso()
    storage.save_investigation(inv)
    storage.log_audit(
        inv_id,
        "RUN_FINISHED",
        f"Investigation finished with verdict '{result.verdict}'",
        details={
            "status": inv.status.value,
            "verdict": result.verdict,
            "suspected_set": result.suspected_set,
            "is_1_minimal": result.is_1_minimal,
            "total_trials": result.total_trials,
        },
    )

    return inv, result


def load_scenario_file(file_path: str | Path) -> DeclarativeScenario:
    """Safely load and validate a declarative scenario file from disk.

    Only standard JSON parsing is used. No code execution, imports, or eval.
    """
    path = Path(file_path).resolve()
    _validate_safe_path(str(file_path))

    if not path.is_file():
        raise FileNotFoundError(f"Scenario file not found: {path}")

    try:
        raw_text = path.read_text(encoding="utf-8")
        parsed_json = json.loads(raw_text)
    except json.JSONDecodeError as err:
        raise ScenarioValidationError(f"Invalid JSON syntax in {path.name}: {err}") from err

    if not isinstance(parsed_json, dict):
        raise ScenarioValidationError("Top-level scenario JSON must be an object.")

    return validate_and_parse_scenario(parsed_json, raw_json=raw_text)
