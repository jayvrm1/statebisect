# StateBisect

> **Notice: Read-Only Windows Collector & Controlled Simulation Environment**  
> StateBisect is currently in a Phase 4 state. It features a strictly read-only Windows Collector MVP and isolated simulation/health-check execution. **No modifications are made to the host Windows registry, files, services, or system configuration.** All host telemetry collection executes with standard-user privileges.

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
│   ├── collector/           # Read-only Windows collector & snapshot diffing (Phase 4)
│   │   ├── base.py          # BaseCollectionSource abstract class & error boundaries
│   │   ├── engine.py        # WindowsCollector engine & preview generator
│   │   ├── models.py        # SnapshotRecord, CollectionRun, CoverageGap, SnapshotDiff
│   │   ├── differ.py        # Gap-aware snapshot comparator (compare_snapshots)
│   │   └── sources/         # 7 Read-only system telemetry sources
│   │       ├── os_info.py   # OS release, build, architecture, and machine ID
│   │       ├── installed_software.py # Read-only registry software inventory
│   │       ├── windows_updates.py    # CIM Win32_QuickFixEngineering updates
│   │       ├── services.py  # Read-only Win32 service state via registry
│   │       ├── drivers.py   # Read-only kernel/filesystem driver inventory
│   │       ├── network.py   # Read-only active network adapter configuration
│   │       └── event_logs.py# Time-bounded Application/System event query via wevtutil
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
│   ├── test_collector.py    # Phase 4 collector, sources, diffing, and gap tests
│   ├── test_health_checks.py# Phase 3 health check, evidence, and safety tests
│   ├── test_scenarios.py    # Scenario schema validation and safety tests
│   ├── test_cli.py          # CLI commands, formats, exit codes, and path tests
│   ├── test_migration.py    # Schema migrations (v1->v2->v3->v4) and audit logging tests
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

## 5. Read-Only Windows Collector MVP (Phase 4)

StateBisect includes an opt-in, read-only Windows system collector that gathers structured inventory and diagnostic snapshots without requiring administrator rights or altering system settings.

### 7 Safe Read-Only Collection Sources:
1. **`os_info`**: Gathers OS product name, build, release, architecture, and a pseudonymized machine identifier (`machine-{sha256(hostname)[:16]}`). No hardware serials or unhashed identifiers.
2. **`installed_software`**: In-process registry enumeration of 64-bit and 32-bit (`WOW6432Node`) uninstall keys via `winreg.KEY_READ`.
3. **`windows_updates`**: Gathers installed hotfixes and KBs via `Get-CimInstance Win32_QuickFixEngineering`.
4. **`services`**: In-process registry inspection of `HKLM\SYSTEM\CurrentControlSet\Services` identifying Win32 services and start types without invoking `sc.exe`.
5. **`drivers`**: In-process registry inspection identifying kernel and filesystem drivers.
6. **`network`**: Queries active network adapters and IP configurations via CIM.
7. **`event_logs`**: Time-bounded query of `Application` and `System` channels via `wevtutil` structured XML. **Security log is excluded by default** as it requires elevated privileges.

### Gap-Aware Snapshot Differ (`compare_snapshots`):
- Compares baseline and subsequent snapshot runs to classify changes as `ADDED`, `REMOVED`, or `MODIFIED`.
- **Safety Invariant**: If either snapshot had a `CoverageGap` in a given category, missing items in that category are strictly marked **`UNKNOWN`** rather than falsely inferred as `REMOVED` or `MODIFIED`.
- Configuration differences are explicitly flagged as correlations, requiring controlled experiments to establish causation.

## 6. Targeted Change Monitoring (Phase 5)

StateBisect provides opt-in, temporary, foreground-only monitoring of system configuration changes. Monitoring sessions run strictly for a requested duration (bounded up to 300s) or until stopped by user cancellation (Ctrl+C).

### Core Invariants:
- **Foreground Only**: Runs as a single foreground CLI command. Never registers startup tasks, scheduled tasks, Windows services, or persistent background daemons.
- **Explicit Opt-in**: Monitoring is OFF by default. It starts only on explicit user invocation and terminates cleanly when requested.
- **Graceful Cancellation**: Traps SIGINT / Ctrl+C to halt polling immediately, flush buffered events, disarm all sources, and persist the session as `CANCELLED`.
- **Zero Host Modifications**: All sources operate with standard user permissions. Never alters Windows audit policies or system settings.

### Supported Monitoring Sources:
1. **`event_log`**:
   - Time-bounded polling of `Application` and `System` event logs using structured XML queries via `wevtutil`.
   - Security log is excluded by default (requires elevated privileges).
   - Extracts EventID, Provider, TimeCreated, ProcessId/Execution metadata.
   - Redacts sensitive text and enforces 512-character bounds per field.
2. **`registry_watcher`**:
   - Strictly restricted to allowlisted user keys (e.g. `HKCU\Software\StateBisect`, `HKCU\Environment`).
   - Any unlisted or security-sensitive paths (e.g. `SAM`, `SECURITY`, `LSA`) are rejected and recorded as coverage gaps.
   - Read-only polling (`winreg.KEY_READ`). Zero write, create, or delete operations.
   - Process attribution: Windows Registry does not natively record modifying PIDs without kernel ETW auditing; therefore `process_provenance` is explicitly set to `UNAVAILABLE` rather than guessing.
3. **`sysmon`** (Pre-installed Only):
   - Detects if Microsoft Sysinternals Sysmon is already installed and its operational channel is active.
   - **Never installs, modifies, or configures Sysmon**.
   - If missing, marks source `UNAVAILABLE` and records a clear coverage gap.
   - When present, reads process-creation and network telemetry with `ProcessProvenance.EVENT_PAYLOAD`.
4. **`etw`** (Evaluation & Safe Deferral):
   - Evaluates whether kernel ETW sessions are safely viable without elevation.
   - Because real-time ETW requires elevated Administrator or `TRACELOG_CREATE_REALTIME` permissions and persistent session handles, kernel ETW is safely deferred and documented as a `CoverageGap`.

### Process Attribution Provenance:
- `EVENT_PAYLOAD`: Directly provided in the event XML by the OS provider (e.g. Sysmon Image/ProcessID or Event Execution ProcessID).
- `PROCESS_LOOKUP`: Resolved via running process table at event time.
- `UNAVAILABLE`: Process identity was not captured by the source. StateBisect **never** guesses process attribution from timing alone.

### Snapshot Correlation:
- When `--with-snapshots` is specified, StateBisect automatically runs a Phase 4 read-only snapshot before monitoring starts (`pre_snapshot_run_id`) and immediately after monitoring ends (`post_snapshot_run_id`), linking the session to configuration diffs.

## 7. Persistence, Evidence Model & Migrations

- Default database: `data/statebisect.db`.
- **Schema Migrations**:
  - Automatically manages migrations up to Version 5 in `schema_migrations`.
  - Non-destructive and idempotent: preserves all existing investigations, trials, snapshots, and diffs.
  - Tables:
    - `investigations`, `experiment_trials`, `audit_logs`, `health_check_evidence` (Phases 1–3).
    - `collection_runs`, `snapshot_records`, `coverage_gaps`, `snapshot_diffs` (Phase 4).
    - `monitoring_sessions`, `monitoring_events`, `monitoring_coverage_gaps` (Phase 5).

## 8. Command-Line Interface (CLI)

```powershell
# --- Investigation & Simulation Commands ---
python statebisect_cli.py list-scenarios
python statebisect_cli.py validate-scenario scenarios/repeated_health_check.json
python statebisect_cli.py run-scenario scenarios/repeated_health_check.json
python statebisect_cli.py inspect-investigation <investigation_id>
python statebisect_cli.py export-report <investigation_id> --format json -o report.json

# --- Read-Only Windows Collector Commands (Phase 4) ---
python statebisect_cli.py collect-preview --lookback-sec 3600 --max-records 100
python statebisect_cli.py collect-windows --lookback-sec 3600 --max-records 100
python statebisect_cli.py list-collections
python statebisect_cli.py inspect-collection <collection_run_id>
python statebisect_cli.py compare-snapshots <base_run_id> <target_run_id>

# --- Targeted Change Monitoring Commands (Phase 5) ---
# Preview monitoring sources, permissions, and duration bounds
python statebisect_cli.py monitor-preview

# Start foreground bounded monitoring session (e.g. 30 seconds)
python statebisect_cli.py monitor-start --duration 30

# Start monitoring with pre- and post-session snapshot comparison
python statebisect_cli.py monitor-start --duration 30 --with-snapshots

# List recorded monitoring sessions
python statebisect_cli.py list-monitor-sessions

# Inspect monitoring session timeline and process attribution
python statebisect_cli.py inspect-monitor-session <session_id>
python statebisect_cli.py inspect-monitor-session <session_id> --format json
```

## 9. Running Tests and Demos

### Run Full Test Suite (85 Tests Across 11 Modules)
```powershell
python -m unittest discover -s tests -v
```

### Run Phase 1 Demonstration Script
```powershell
python demo_phase1.py
```

## 10. Phase Status

- [x] **Phase 0 — Repository review & product skeleton**: Completed.
- [x] **Phase 1 — Simulated experiment runner & Delta Debugging**: Completed.
- [x] **Phase 2 — Experiment definitions, CLI, and auditability**: Completed.
- [x] **Phase 3 — Failure health checks and evidence model**: Completed.
- [x] **Phase 4 — Windows read-only collector MVP**: Completed.
- [x] **Phase 5 — Targeted continuous collection & lightweight monitoring**: Completed.
- [ ] **Phase 6 — Candidate change generation & correlation engine**: Next phase.
