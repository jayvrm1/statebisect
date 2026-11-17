# StateBisect

> **Notice: Simulation & Controlled Health-Check Prototype**  
> StateBisect is currently in a Phase 3 prototype state. All experiments run within an isolated virtual state and execute simulated or strictly bounded local health checks. **No modifications are made to the host Windows registry, files, services, or system configuration.**

## 1. Product Mission

StateBisect investigates:
> *"This Windows application or system function worked before. It now fails. Which observed configuration change, or combination of changes, can reproduce the failure?"*

StateBisect is a rigorous investigation and experiment system. It is **not** an antivirus, system cleaner, registry repair tool, automated Windows administrator, or generic chatbot.

## 2. Core Principles
- **Evidence before explanation**: All findings are grounded in deterministic, recorded experiment trials.
- **Deterministic core**: Delta Debugging (`ddmin`), candidate ranking, and trial evaluation operate deterministically without dependence on AI models.
- **Clean experiments**: Every trial resets to a verified baseline state.
- **Explicit uncertainty**: Inconclusive telemetry, timeouts, ambiguous outcomes, and intermittent/flaky attempts are recorded as `INCONCLUSIVE`, never coerced to pass or fail.
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
│   ├── repeated_health_check.json # Example multi-attempt repeated check
│   └── invalid_scenario.json# Example invalid scenario demonstrating strict rejection
├── statebisect/
│   ├── __init__.py          # Public package exports
│   ├── cli.py               # CLI interface (argparse)
│   ├── models.py            # Dataclasses & Enums (Change, Trial, Evidence, CancellationToken)
│   ├── health_checks/       # Pluggable, versioned health-check system
│   │   ├── base.py          # BaseHealthCheck, evidence bounding, and secret redaction
│   │   ├── simulated_state.py # State condition matcher (key-value and compound)
│   │   ├── process_check.py # Opt-in, pre-registered safe executable checker
│   │   ├── http_check.py    # Strictly loopback-bounded GET HTTP checker
│   │   └── registry.py      # Health-check factory and validation
│   ├── scenarios.py         # Declarative scenario schema parser & strict validator
│   ├── simulation.py        # Isolated workspace & dependency resolution
│   ├── delta_debugging.py   # Generalized ddmin algorithm with cancellation & integrity checks
│   ├── runner.py            # Experiment runner with baseline cleanup & evidence recording
│   ├── storage.py           # SQLite persistence layer with schema migrations & evidence store
│   └── reporting.py         # Diagnostic text and JSON report generators
├── tests/
│   ├── test_health_checks.py# Phase 3 health check, evidence, and safety tests
│   ├── test_scenarios.py    # Scenario schema validation and safety tests
│   ├── test_cli.py          # CLI commands, formats, exit codes, and path tests
│   ├── test_migration.py    # Schema migrations (v1->v2->v3) and audit logging tests
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

## 4. Pluggable Health-Check Interface (Phase 3)

Health checks determine whether a specific symptom is observed (`FAIL`), absent (`PASS`), or uncertain (`INCONCLUSIVE`).

### Supported Health Check Types:
1. **`simulated_state`** (Version `1.0.0`):
   - Evaluates virtual configuration state conditions (`key_value_match` or `compound_condition`).
   - Purely in-memory and non-destructive.
2. **`registered_process`** (Version `1.0.0`):
   - **Safety Boundary**: Opt-in and disabled by default. Scenario files **cannot** execute arbitrary commands or paths.
   - Only pre-registered executables in `ProcessRegistry` can be targeted by an ID string.
   - Executes with structured argument lists only (`list[str]`); `shell=True`, shell strings, and PowerShell are strictly prohibited.
   - Enforces timeout, isolates subprocess stdout/stderr, and bounds captured evidence.
3. **`loopback_http`** (Version `1.0.0`):
   - **Safety Boundary**: Strictly restricted to loopback addresses (`localhost`, `127.0.0.1`, `::1`). Any non-loopback address is rejected before network contact.
   - Read-only `GET` requests only; no credentials or cookies sent.
   - Non-loopback HTTP redirects are rejected by `SafeLoopbackRedirectHandler`.
   - Response bodies are bounded to a maximum of 16 KB.

### Repeat Policies & Flaky Outcome Aggregation:
- Scenarios can specify `repeat_count` (1 to 10) and `required_consistency` (`"ALL_MATCH"`).
- If attempts disagree (e.g. Attempt 1 passes, Attempt 2 fails), the overall trial is marked `INCONCLUSIVE` due to intermittency. Flaky failures are never falsely claimed as deterministic causes.
- Timeouts and unhandled exceptions are recorded as `INCONCLUSIVE`.

### Secret Redaction & Evidence Bounding:
- All output strings are sanitized via `redact_sensitive_text` to scrub passwords, tokens, API keys, and bearer tokens.
- Evidence strings are bounded to 4096 characters to prevent log explosion.

## 5. Persistence, Evidence Model & Migrations

- Default database: `data/statebisect.db`.
- **Schema Migrations**:
  - Automatically manages migrations up to Version 3 in `schema_migrations`.
  - Non-destructive and idempotent: preserves all existing investigations and trials from earlier phases.
  - Adds `health_check_evidence` table recording each individual attempt with timestamps, exit codes, expected vs observed values, and truncation/redaction indicators.

## 6. Command-Line Interface (CLI)

```powershell
# List scenarios
python statebisect_cli.py list-scenarios

# Validate scenario file
python statebisect_cli.py validate-scenario scenarios/repeated_health_check.json

# Execute scenario
python statebisect_cli.py run-scenario scenarios/repeated_health_check.json

# Inspect investigation with recorded attempts
python statebisect_cli.py inspect-investigation <investigation_id>

# Export structured report
python statebisect_cli.py export-report <investigation_id> --format json -o report.json
```

## 7. Running Tests and Demos

### Run Full Test Suite (52 Tests)
```powershell
python -m unittest discover -s tests -v
```

### Run Phase 1 Demonstration Script
```powershell
python demo_phase1.py
```

## 8. Phase Status

- [x] **Phase 0 — Repository review & product skeleton**: Completed.
- [x] **Phase 1 — Simulated experiment runner & Delta Debugging**: Completed.
- [x] **Phase 2 — Experiment definitions, CLI, and auditability**: Completed.
- [x] **Phase 3 — Failure health checks and evidence model**: Completed.
- [ ] **Phase 4 — Windows read-only collector MVP**: Next phase.
