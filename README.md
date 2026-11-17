# StateBisect

> **Notice: Simulation Prototype**  
> StateBisect is currently in a Phase 2 prototype state. All experiments run within an isolated virtual state and execute simulated health checks. **No modifications are made to the host Windows registry, files, services, or system configuration.**

## 1. Product Mission

StateBisect investigates:
> *"This Windows application or system function worked before. It now fails. Which observed configuration change, or combination of changes, can reproduce the failure?"*

StateBisect is a rigorous investigation and experiment system. It is **not** an antivirus, system cleaner, registry repair tool, automated Windows administrator, or generic chatbot.

## 2. Core Principles
- **Evidence before explanation**: All findings are grounded in deterministic, recorded experiment trials.
- **Deterministic core**: Delta Debugging (`ddmin`), candidate ranking, and trial evaluation operate deterministically without dependence on AI models.
- **Clean experiments**: Every trial resets to a verified baseline state.
- **Explicit uncertainty**: Inconclusive telemetry, timeouts, and ambiguous outcomes are recorded as `INCONCLUSIVE`, never coerced to pass or fail.
- **Interactions as first-class findings**: Multi-change combinations (e.g. pairs of changes) are recognized and reported.
- **1-Minimality**: Guarantees that removing any single element from the failure-inducing set resolves the failure. Note: 1-minimal does not imply a globally smallest set across all $2^N$ combinations.
- **Safety by default**: Runs with zero host administrator privileges and zero host modifications. Never executes arbitrary shell commands or untrusted code from scenario files.

## 3. Project Structure

```
statebisect/
├── docs/
│   ├── spec.md              # Product specification & MVP boundaries
│   └── architecture.md      # Modular architecture overview
├── scenarios/               # Declarative experiment scenario definitions
│   ├── single_cause.json    # Example single-cause scenario (TLS cipher suite deprecation)
│   ├── interaction.json     # Example interaction scenario (HTTP/2 + fallback disabled)
│   └── invalid_scenario.json# Example invalid scenario demonstrating strict rejection
├── statebisect/
│   ├── __init__.py          # Public package exports
│   ├── cli.py               # CLI interface (argparse)
│   ├── models.py            # Dataclasses & Enums (Change, Trial, Investigation, CancellationToken)
│   ├── scenarios.py         # Declarative scenario schema parser & strict validator
│   ├── simulation.py        # Isolated workspace & dependency resolution
│   ├── delta_debugging.py   # Generalized ddmin algorithm with cancellation & integrity checks
│   ├── runner.py            # Experiment runner with baseline cleanup & cancellation
│   ├── storage.py           # SQLite persistence layer with schema migrations & audit logs
│   └── reporting.py         # Diagnostic text and JSON report generators
├── tests/
│   ├── test_scenarios.py    # Scenario schema validation and safety tests
│   ├── test_cli.py          # CLI commands, formats, exit codes, and path tests
│   ├── test_migration.py    # Schema migrations (v1->v2) and audit logging tests
│   ├── test_cancellation.py # Interruption, cleanup, and corruption handling tests
│   ├── test_simulation.py   # Workspace, baseline, and dependency tests
│   ├── test_storage.py      # SQLite storage and query tests
│   ├── test_runner.py       # Trial runner and cleanup tests
│   └── test_delta_debugging.py # ddmin single, interaction, and inconclusive tests
├── statebisect_cli.py       # CLI executable entry point
├── demo_phase1.py           # Runnable programmatic demonstration script
├── PROJECT_NOTES.md         # Implementation logs, technology decisions, and phase status
└── README.md
```

## 4. Declarative Scenarios

Scenarios are defined in pure, declarative JSON adhering to schema version `1.0.0`.

### Supported Operations:
- `set_key`: Sets or updates a configuration key in the simulated state (`{"key": str, "value": any}`).
- `replace_value`: Replaces an existing configuration value (`{"key": str, "value": any}`).
- `delete_key`: Removes a configuration key (`{"key": str}`).
- `append_list`: Appends an item to a list configuration key (`{"key": str, "item": any}`).

### Supported Health Check Types:
- `key_value_match`: Evaluates whether a configuration key matches an expected value (`{"key": str, "expected_value": any}`).
- `compound_condition`: Evaluates a list of conditions joined with logical AND (`{"conditions": [{"key": str, "op": "eq"|"neq"|"in"|"nin", "value": any}]}`).

### Security Boundary:
- Any occurrence of executable properties (e.g. `command`, `cmd`, `script`, `eval`, `powershell`, `shell`) causes immediate rejection.
- Path traversal (`..`) in scenario loading or export paths is rejected.
- Loading is strictly data parsing (`json.loads`); no `eval`, `exec`, or dynamic imports are permitted.

## 5. Command-Line Interface (CLI)

The CLI is accessible via `python statebisect_cli.py` or `python -m statebisect.cli`.

### Commands

#### 1. List Scenarios
```powershell
python statebisect_cli.py list-scenarios [scenarios_dir]
```

#### 2. Validate Scenario
```powershell
python statebisect_cli.py validate-scenario scenarios/single_cause.json
```

#### 3. Run Scenario
```powershell
python statebisect_cli.py run-scenario scenarios/single_cause.json [--db data/statebisect.db]
```

#### 4. Inspect Stored Investigation
```powershell
python statebisect_cli.py inspect-investigation <investigation_id> [--format text|json]
```

#### 5. Export Report
```powershell
python statebisect_cli.py export-report <investigation_id> --format text [-o report.txt]
python statebisect_cli.py export-report <investigation_id> --format json [-o report.json]
```

## 6. Persistence & Schema Migrations

- Default database location: `data/statebisect.db`.
- **Schema Migrations**:
  - Automatically tracked in the `schema_migrations` table.
  - Safe, transactional, and repeat-safe (idempotent).
  - Preserves Phase 1 databases without loss of existing investigations or trial records.
- **Auditability & Reproducibility**:
  - Every run records a SHA-256 cryptographic hash of the scenario JSON and stores the full scenario snapshot.
  - Structured audit trail table (`audit_logs`) logs lifecycle events (`RUN_STARTED`, `TRIAL_COMPLETED`, `RUN_FINISHED`, `TRIAL_CANCELLED`, `CLEANUP_FAILURE`).

## 7. Running Tests and Demos

### Run Full Test Suite (40 Tests)
```powershell
python -m unittest discover -s tests -v
```

### Run Demonstration
```powershell
python demo_phase1.py
```

## 8. Phase Status

- [x] **Phase 0 — Repository review & product skeleton**: Completed.
- [x] **Phase 1 — Simulated experiment runner & Delta Debugging**: Completed.
- [x] **Phase 2 — Experiment definitions, CLI, and auditability**: Completed.
- [ ] **Phase 3 — Failure health checks and evidence model**: Next phase.
