"""Declarative experiment scenario format, schema validation, and loading."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .health_checks.base import BaseHealthCheck
from .health_checks.registry import (
    SUPPORTED_CHECK_TYPES,
    SUPPORTED_CHECK_VERSIONS,
    create_health_check,
)

CURRENT_SCHEMA_VERSION = "1.0.0"
ALLOWED_OPERATIONS = frozenset(["set_key", "delete_key", "append_list", "replace_value"])
FORBIDDEN_EXECUTABLE_KEYS = frozenset(
    ["command", "cmd", "script", "exec", "eval", "powershell", "bash", "code", "shell", "run"]
)
LOOPBACK_HOSTS = frozenset(["localhost", "127.0.0.1", "::1", "[::1]"])


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
    version: str = "1.0.0"
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 5000
    repeat_count: int = 1
    required_consistency: str = "ALL_MATCH"


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
    if not hc_type or hc_type not in SUPPORTED_CHECK_TYPES:
        raise ScenarioValidationError(
            f"Invalid health check type '{hc_type}'. Allowed types: {sorted(SUPPORTED_CHECK_TYPES)}"
        )

    hc_version = hc_raw.get("version", "1.0.0")
    if hc_version not in SUPPORTED_CHECK_VERSIONS:
        raise ScenarioValidationError(
            f"Unsupported health check version '{hc_version}'. Supported versions: {sorted(SUPPORTED_CHECK_VERSIONS)}"
        )

    hc_params = hc_raw.get("parameters")
    if not isinstance(hc_params, dict):
        raise ScenarioValidationError("Health check must specify a dictionary of 'parameters'.")

    # Validate specific health check parameters
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
    elif hc_type == "registered_process":
        if "executable_id" not in hc_params or not isinstance(hc_params["executable_id"], str):
            raise ScenarioValidationError("health_check 'registered_process' requires string 'executable_id'.")
        if "args" in hc_params:
            if not isinstance(hc_params["args"], list) or not all(isinstance(a, str) for a in hc_params["args"]):
                raise ScenarioValidationError("health_check 'registered_process' 'args' must be a list of strings.")
    elif hc_type == "loopback_http":
        url = hc_params.get("url")
        if not url or not isinstance(url, str):
            raise ScenarioValidationError("health_check 'loopback_http' requires string 'url'.")
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.scheme.lower() not in ("http", "https"):
            raise ScenarioValidationError("health_check 'loopback_http' requires http or https scheme.")
        host = (parsed_url.hostname or "").lower()
        if host not in LOOPBACK_HOSTS:
            raise ScenarioValidationError(
                f"health_check 'loopback_http' url must target a loopback host (localhost, 127.0.0.1, [::1]). Got '{host}'."
            )

    timeout_ms = hc_raw.get("timeout_ms", 5000)
    if not isinstance(timeout_ms, int) or timeout_ms < 100 or timeout_ms > 60000:
        raise ScenarioValidationError("'timeout_ms' must be an integer between 100 and 60000.")

    repeat_count = hc_raw.get("repeat_count", 1)
    if not isinstance(repeat_count, int) or repeat_count < 1 or repeat_count > 10:
        raise ScenarioValidationError("'repeat_count' must be an integer between 1 and 10.")

    required_consistency = hc_raw.get("required_consistency", "ALL_MATCH")
    if required_consistency not in ("ALL_MATCH",):
        raise ScenarioValidationError("Currently supported required_consistency is 'ALL_MATCH'.")

    health_check = ScenarioHealthCheck(
        type=hc_type,
        version=hc_version,
        name=hc_raw.get("name", hc_type),
        description=hc_raw.get("description", ""),
        parameters=hc_params,
        timeout_ms=timeout_ms,
        repeat_count=repeat_count,
        required_consistency=required_consistency,
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


def build_declarative_health_check(hc_def: ScenarioHealthCheck) -> BaseHealthCheck:
    """Build a pluggable, versioned BaseHealthCheck from validated ScenarioHealthCheck."""
    return create_health_check(
        check_type=hc_def.type,
        check_version=hc_def.version,
        name=hc_def.name,
        description=hc_def.description,
        timeout_ms=hc_def.timeout_ms,
        repeat_count=hc_def.repeat_count,
        required_consistency=hc_def.required_consistency,
        parameters=hc_def.parameters,
    )


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
        details={
            "name": scenario.name,
            "hash": scenario.content_hash,
            "check_type": scenario.health_check.type,
            "check_version": scenario.health_check.version,
            "repeat_count": scenario.health_check.repeat_count,
        },
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
    hc_instance = build_declarative_health_check(scenario.health_check)
    debugger = DeltaDebugger(runner, changes, hc_instance)

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
