# StateBisect Project Notes

## Phase 0: Repository Review & Product Skeleton
- **Inspection Findings**:
  - Python 3.12.10 and Git 2.56 available on Windows.
  - Standard library (`unittest`, `sqlite3`, `dataclasses`, `enum`, `json`, `argparse`, `hashlib`, `urllib`) is fully sufficient.
- **Completed**:
  - Authored `docs/spec.md` with mission, MVP boundary, demonstration scenarios, pass/fail rules, and roadmap.
  - Authored `docs/architecture.md` outlining modular components and isolation boundaries.

## Phase 1: Simulated Experiment Runner & Delta Debugging
- **Completed**:
  - Pure Python deterministic core (`models.py`, `simulation.py`, `storage.py`, `runner.py`, `delta_debugging.py`, `reporting.py`).
  - Generalized `ddmin` with 1-minimality verification, multi-change interaction discovery, and explicit `INCONCLUSIVE` handling.
  - Safety cleanup in unconditional `finally` block.
  - Programmatic demo (`demo_phase1.py`).

## Phase 2: Declarative Scenarios, CLI, and Auditability
- **Completed**:
  - `statebisect/scenarios.py`: Declarative scenario schema v1.0.0, strict validation, safe path checks, SHA-256 hashing.
  - `statebisect/cli.py`: CLI subcommands (`list-scenarios`, `validate-scenario`, `run-scenario`, `inspect-investigation`, `export-report`).
  - `statebisect/storage.py`: Schema migrations up to Version 2 and structured audit logging (`audit_logs`).
  - Graceful cancellation via `CancellationToken` (handling SIGINT / Ctrl+C).

## Phase 3: Health Checks and Evidence Model
- **Completed**:
  - `statebisect/health_checks/`:
    - `BaseHealthCheck`: Pluggable, versioned health check contract with parameter hashing, timeout handling, repeat execution, and consistency aggregation.
    - `SimulatedStateCheck` (v1.0.0): In-memory state and compound condition checker with backward compatibility.
    - `RegisteredProcessCheck` (v1.0.0): Opt-in test process execution restricted to explicitly registered executables (`ProcessRegistry`). Passes structured argument lists only (`list[str]`); `shell=True` and arbitrary command strings are strictly disallowed.
    - `LoopbackHttpCheck` (v1.0.0): Read-only GET requests strictly restricted to loopback addresses (`127.0.0.1`, `localhost`, `[::1]`). Custom `SafeLoopbackRedirectHandler` prevents non-loopback redirect evasion.
    - `sanitize_and_bound_evidence`: Scrubs sensitive tokens/passwords (`redact_sensitive_text`) and enforces 4096-character bounds (`bound_evidence`) on evidence text.
  - Repetition and Flakiness:
    - Configurable `repeat_count` (1 to 10) and `required_consistency` (`"ALL_MATCH"`).
    - If attempts disagree across repeats, the trial outcome is marked `INCONCLUSIVE` (reasoning: intermittent / flaky symptom). Intermittent failures are never coerced to confident causes.
  - Persistence & Migration:
    - SQLite Schema Version 3: Adds `health_check_evidence` table recording each individual attempt with timestamps, exit codes, expected/observed values, and truncation/redaction indicators.
    - Non-destructive migration from Version 1/2 to Version 3 preserving all past investigations and trial records.
  - Reporting & CLI:
    - `inspect-investigation` displays attempt counts and check types.
    - `export-report` includes detailed evidence attempt arrays in JSON.
  - `tests/`: Added `tests/test_health_checks.py`. Total test count: 52 tests, all passing.

## Technology & Algorithm Decisions & Source Verification

1. **Pluggable Health Check Architecture**:
   - **Primary Source**: System telemetry standards and Microsoft safe testing patterns.
   - **Safety Boundary**: Scenario JSON definitions are untrusted data. Therefore, process health checks cannot accept arbitrary file paths or shell strings. They must reference a pre-registered executable ID verified outside the scenario. Subprocess spawning enforces `shell=False`.
2. **Loopback HTTP Isolation**:
   - **Primary Source**: RFC 6761 (Special-Use Domain Names) & RFC 1122 (Loopback host address requirements).
   - **Safety Boundary**: HTTP checks are restricted strictly to loopback interfaces to prevent SSRF or unintended external network interactions during investigations. Redirects are monitored and blocked if they attempt to escape loopback.
3. **Flaky / Intermittent Result Handling**:
   - **Decision**: Strict consistency (`ALL_MATCH`).
   - **Tradeoff**: In automated root-cause bisecting, an intermittent failure produces false candidate attribution if non-determinism is ignored. Any variance across repeated attempts is classified as `INCONCLUSIVE`.

## Verification Results
- All 52 unit and integration tests passing (`python -m unittest discover -s tests -v`).
- Phase 1 programmatic demonstration script runs and passes (`python demo_phase1.py`).
- CLI commands validated on multi-attempt scenario (`scenarios/repeated_health_check.json`).

## Phase 4: Windows Read-Only Collector MVP
- **Completed**:
  - `statebisect/collector/models.py`: Data models for read-only snapshots (`SnapshotRecord`, `CoverageGap`, `CollectionResult`, `CollectionRun`, `SnapshotDiff`, `SourceStatus`, `DiffChangeType`).
  - `statebisect/collector/base.py`: `BaseCollectionSource` providing preview contract, execution timing, and robust error boundaries mapping permission denials to `CoverageGap`.
  - `statebisect/collector/sources/`:
    - `os_info.py`: OS product, build, architecture, and privacy-preserving machine ID (`machine-{sha256(hostname)[:16]}`).
    - `installed_software.py`: In-process registry inspection of 64-bit and 32-bit (`WOW6432Node`) uninstall hives using `winreg.KEY_READ`.
    - `windows_updates.py`: Query of installed KBs/hotfixes using `Get-CimInstance Win32_QuickFixEngineering`.
    - `services.py`: In-process registry inspection of `HKLM\SYSTEM\CurrentControlSet\Services` for Win32 services and start configurations.
    - `drivers.py`: In-process registry inspection of kernel and filesystem drivers.
    - `network.py`: Query of active network adapters and IP configurations via CIM.
    - `event_logs.py`: Time-bounded query of `Application` and `System` channels using `wevtutil` structured XML. Security log excluded by default.
  - `statebisect/collector/engine.py`: `WindowsCollector` orchestrating sources, enforcing `sys.platform == 'win32'` checks with non-Windows test bypass, preview generation, and overall status aggregation.
  - `statebisect/collector/differ.py`: `compare_snapshots()` implementing gap-aware diffing. Categorizes differences as `ADDED`, `REMOVED`, `MODIFIED`, and strictly flags missing items as `UNKNOWN` if either run had a `CoverageGap` in that category.
  - `statebisect/storage.py`: SQLite Schema Version 4 adding `collection_runs`, `snapshot_records`, `coverage_gaps`, and `snapshot_diffs` tables with relational indexes and cascade deletion. Non-destructive migration from v1, v2, and v3.
  - `statebisect/cli.py`: Integrated subcommands:
    - `collect-preview`: Dry-run summary of sources, permission requirements, and safety guarantees.
    - `collect-windows`: Read-only snapshot collection persisting records to SQLite.
    - `list-collections`: Lists recorded snapshot collection runs.
    - `inspect-collection`: Inspects collection run metadata, category summaries, and coverage gaps.
    - `compare-snapshots`: Computes and displays gap-aware configuration differences between two runs.
  - `tests/`: Added `tests/test_collector.py` and updated `tests/test_cli.py` and `tests/test_migration.py`. Total test count: 69 tests across 10 modules, all passing.

## Technology & Algorithm Decisions & Source Verification

1. **Pluggable Health Check Architecture (Phase 3)**:
   - **Primary Source**: System telemetry standards and Microsoft safe testing patterns.
   - **Safety Boundary**: Scenario JSON definitions are untrusted data. Therefore, process health checks cannot accept arbitrary file paths or shell strings. They must reference a pre-registered executable ID verified outside the scenario. Subprocess spawning enforces `shell=False`.
2. **Loopback HTTP Isolation (Phase 3)**:
   - **Primary Source**: RFC 6761 (Special-Use Domain Names) & RFC 1122 (Loopback host address requirements).
   - **Safety Boundary**: HTTP checks are restricted strictly to loopback interfaces to prevent SSRF or unintended external network interactions during investigations. Redirects are monitored and blocked if they attempt to escape loopback.
3. **Flaky / Intermittent Result Handling (Phase 3)**:
   - **Decision**: Strict consistency (`ALL_MATCH`).
   - **Tradeoff**: In automated root-cause bisecting, an intermittent failure produces false candidate attribution if non-determinism is ignored. Any variance across repeated attempts is classified as `INCONCLUSIVE`.
4. **In-Process Registry Enumeration (Phase 4)**:
   - **Primary Source**: Microsoft Docs: `RegOpenKeyExW` / `winreg` documentation.
   - **Decision**: Standard library `winreg` with `winreg.KEY_READ | winreg.KEY_WOW64_64KEY` and `KEY_WOW64_32KEY`.
   - **Tradeoff**: Reading registry keys in-process completes in <10 milliseconds and requires no external packages or elevated privileges. Avoids spawning slow external command-line utilities like `reg.exe` or PowerShell scripts for registry inspection.
5. **Structured Windows Event Log Ingestion (Phase 4)**:
   - **Primary Source**: Microsoft Docs: Windows Event Log Architecture and `wevtutil` CLI Reference.
   - **Decision**: `wevtutil qe <Channel> /c:<limit> /rd:true /f:xml`.
   - **Tradeoff**: Returns well-formed XML parseable directly with Python standard library `xml.etree.ElementTree`. Security log is excluded by default as reading it requires elevated administrative privileges (`SeSecurityPrivilege`). Application and System logs are readable by standard users.
6. **Gap-Aware Snapshot Differ Semantics (Phase 4)**:
   - **Decision**: If a category has a recorded `CoverageGap` in either snapshot, any difference in that category is marked `UNKNOWN` with status `INCONCLUSIVE`.
   - **Tradeoff**: Prevents falsely inferring that a configuration item was removed or added when the observation source was incomplete or permission-denied.
7. **Privacy-Preserving Machine Identification (Phase 4)**:
   - **Decision**: Salted pseudonymization: `machine-{sha256(hostname)[:16]}`.
   - **Tradeoff**: Avoids querying or storing hardware serial numbers (BIOS UUID, motherboard serial) or raw hostnames, preventing fingerprinting while maintaining unique session identity.

## Verification Results
- All 69 unit and integration tests passing (`python -m unittest discover -s tests -v`).
- Phase 1 programmatic demonstration script runs and passes (`python demo_phase1.py`).
- Real-world collection run tested live on host Windows 11 machine (`python statebisect_cli.py collect-windows --max-records 20`).
- Dry run preview validated (`python statebisect_cli.py collect-preview`).
- Snapshot comparison verified with both identical runs and synthesized changes.

## Phase 5: Targeted Change Monitoring
- **Completed**:
  - `statebisect/monitor/models.py`: Data models for bounded change monitoring sessions (`MonitoringSession`, `MonitoringEvent`, `MonitoringCoverageGap`, `MonitoringSessionStatus`, `ProcessProvenance`, `EventConfidence`).
  - `statebisect/monitor/base.py`: `BaseMonitorSource` defining startup, polling, shutdown, preview, and loss tracking contracts.
  - `statebisect/monitor/sources/`:
    - `event_log.py`: Time-bounded polling of `Application` and `System` channels using high-water mark XML queries via `wevtutil`.
    - `sysmon.py`: Non-invasive detection of pre-installed Sysmon. Never installs, modifies, or creates Sysmon files.
    - `registry_watcher.py`: In-process observation of strictly allowlisted user registry keys (`winreg.KEY_READ`). Rejects forbidden paths (e.g. `SAM`, `SECURITY`, `LSA`). Tracks `process_provenance = ProcessProvenance.UNAVAILABLE`.
    - `etw_evaluator.py`: Safe evaluation and deferral of real-time ETW kernel sessions to uphold zero-elevation and no-persistent-trace invariants.
  - `statebisect/monitor/engine.py`: `MonitoringEngine` with conservative duration bounds (1s to 300s, default 30s), event buffer cap (2000 events), SIGINT / Ctrl+C clean shutdown, and optional pre/post snapshot integration.
  - `statebisect/storage.py`: SQLite Schema Version 5 adding `monitoring_sessions`, `monitoring_events`, and `monitoring_coverage_gaps` tables with indexes and cascade deletion. Non-destructive migration from v1, v2, v3, and v4.
  - `statebisect/cli.py`: Integrated subcommands:
    - `monitor-preview`: Summary of monitoring sources, permissions, and duration bounds.
    - `monitor-start`: Starts foreground bounded monitoring session with live event streaming and clean Ctrl+C handling.
    - `list-monitor-sessions`: Lists recorded monitoring sessions with timestamps, elapsed times, event counts, and statuses.
    - `inspect-monitor-session`: Detailed view of session metadata, observed event timeline, process attribution provenance, coverage gaps, and loss indicators.
  - `tests/`: Added `tests/test_monitor.py` and updated `tests/test_cli.py` and `tests/test_migration.py`. Total test count: 85 tests across 11 modules, all passing.

## Technology & Algorithm Decisions & Source Verification

1. **Pluggable Health Check Architecture (Phase 3)**:
   - **Primary Source**: System telemetry standards and Microsoft safe testing patterns.
   - **Safety Boundary**: Scenario JSON definitions are untrusted data. Process health checks reference pre-registered executable IDs verified outside the scenario. Subprocess spawning enforces `shell=False`.
2. **Loopback HTTP Isolation (Phase 3)**:
   - **Primary Source**: RFC 6761 & RFC 1122.
   - **Safety Boundary**: HTTP checks restricted strictly to loopback interfaces (`127.0.0.1`, `localhost`, `[::1]`). Redirects escaping loopback are blocked.
3. **Flaky / Intermittent Result Handling (Phase 3)**:
   - **Decision**: Strict consistency (`ALL_MATCH`). Any variance across repeated attempts is classified as `INCONCLUSIVE`.
4. **In-Process Registry Enumeration (Phase 4)**:
   - **Primary Source**: Microsoft Docs: `RegOpenKeyExW` / `winreg` documentation.
   - **Decision**: Standard library `winreg` with `winreg.KEY_READ | winreg.KEY_WOW64_64KEY` and `KEY_WOW64_32KEY`. Reading keys in-process completes in <10ms with zero elevation.
5. **Structured Windows Event Log Ingestion (Phase 4 & 5)**:
   - **Primary Source**: Microsoft Learn: Event Logging Functions and modern API guidance.
   - **Decision**: `wevtutil qe <Channel> /q:"*[System[TimeCreated[@SystemTime>='{high_water_mark}']]]" /f:xml /c:50`.
   - **Tradeoff**: Returns structured XML with standard-user privileges. Security log is excluded by default as it requires elevated privileges (`SeSecurityPrivilege`).
6. **Gap-Aware Snapshot Differ Semantics (Phase 4)**:
   - **Decision**: If a category has a recorded `CoverageGap` in either snapshot, any difference in that category is marked `UNKNOWN` with status `INCONCLUSIVE`.
7. **Privacy-Preserving Machine Identification (Phase 4)**:
   - **Decision**: Salted pseudonymization: `machine-{sha256(hostname)[:16]}`. Avoids collecting or persisting hardware serials.
8. **Sysmon Telemetry Integration Strategy (Phase 5)**:
   - **Primary Source**: Microsoft Sysinternals: Sysmon Documentation.
   - **Decision**: Read-only query of pre-existing `Microsoft-Windows-Sysmon/Operational` channel only.
   - **Safety Invariant**: StateBisect **never** installs Sysmon, updates drivers, creates configuration XML, or enables services. If Sysmon is absent, the engine records an explicit `CoverageGap` and proceeds with remaining sources.
9. **Safe Deferral of Real-Time ETW Tracing (Phase 5)**:
   - **Primary Source**: Microsoft Learn: Event Tracing for Windows (ETW) Architecture and Security.
   - **Decision**: Safely deferred and recorded as a documented limitation.
   - **Tradeoff**: Real-time ETW sessions require `TRACELOG_CREATE_REALTIME` permissions (Administrators or Performance Log Users group), and kernel sessions require administrator rights and persistent logger configuration. To adhere strictly to StateBisect's core principle of zero elevation and temporary foreground execution, ETW is evaluated but safely deferred.
10. **Strict Registry Observation Allowlist (Phase 5)**:
    - **Decision**: Strictly enforced allowlist of user-space configuration paths (e.g. `HKCU\Software\StateBisect`, `HKCU\Environment`). Unlisted or security-sensitive paths (`SAM`, `SECURITY`, `LSA`) are rejected and recorded as coverage gaps. Zero write operations.
11. **Process Attribution Provenance Invariant (Phase 5)**:
    - **Decision**: Explicit `ProcessProvenance` (`EVENT_PAYLOAD`, `PROCESS_LOOKUP`, `UNAVAILABLE`).
    - **Safety Invariant**: Process identity is **never** guessed or inferred from timing proximity alone. If source telemetry does not include process identity, it is marked `UNAVAILABLE`.

## Verification Results
- All 85 unit and integration tests passing (`python -m unittest discover -s tests -v`).
- Phase 1 programmatic demonstration script runs and passes (`python demo_phase1.py`).
- Real-world collection run tested live on host Windows 11 machine (`python statebisect_cli.py collect-windows --max-records 20`).
- Live 3-second monitoring session executed on Windows 11 host (`python statebisect_cli.py monitor-start --duration 3`), verified with `list-monitor-sessions` and `inspect-monitor-session` (text and JSON).
- Dry run monitoring preview validated (`python statebisect_cli.py monitor-preview`).
- Snapshot comparison verified with both identical runs and synthesized changes.

## Known Limitations
- Monitoring is point-in-time observation during the active session; changes that occurred prior to session start cannot be reconstructed from monitoring alone (though prior snapshots and event logs can be inspected).
- Process attribution for registry changes is unavailable in standard user space without kernel-level driver auditing.

## Phase 6: Candidate Change Generation & Correlation Engine
- **Completed**:
  - `statebisect/correlation/models.py`: Data models for candidates (`CandidateChange`, `CandidateEvidenceType`, `CandidateReviewStatus`, `CandidateReversibility`, `CorrelationWindow`, `ScoreBreakdown`, `CandidateReviewRecord`).
  - `statebisect/correlation/ranker.py`: `CandidateRanker` computing transparent, explainable priority scores (0-100 pts) using deterministic weighted signals:
    - Evidence Type Strength (0-30 pts): Confirmed snapshot diff (+30), Registry/Service change (+25), Event log (+18), App telemetry (+12).
    - Temporal Proximity to Onset (0-30 pts): $\le 60$s before (+30), $\le 300$s (+25), $\le 900$s (+18), $\le 3600$s (+10), $>3600$s or fallback (+5). Changes occurring *after* onset receive 5 pts and are flagged as potential consequence/symptom.
    - Target Relevance (0-20 pts): Direct substring match (+20), system infrastructure dependency (+10), unrelated (+0).
    - Process Provenance (0-10 pts): `EVENT_PAYLOAD` with PID/name (+10), `PROCESS_LOOKUP` (+6), `UNAVAILABLE` (+2).
    - Multi-Source Corroboration (0-10 pts): Independent multi-source observation (+10 bonus).
    - Coverage Gap Penalty (0 to -10 pts): Subtracted penalty (-10) if category had recorded gaps.
    - Deterministic tie-breaking: Score DESC -> Temporal Proximity ASC -> Evidence Weight DESC -> Candidate ID ASC.
  - `statebisect/correlation/engine.py`: `CorrelationEngine` building configurable correlation windows (pre-onset, post-onset, timezone parsing), ingesting snapshot diffs (`SnapshotDiff`) and monitoring events (`MonitoringEvent`), corroborating overlapping evidence, and ranking deterministically. Supports untimed fallback when onset is missing.
  - `statebisect/storage.py`: SQLite Schema Version 6 adding `investigation_candidates` and `candidate_reviews` tables with cascade deletion and indexing. Added `onset_time_utc`, `onset_time_user`, `onset_timezone`, `last_known_good_utc`, `correlation_window_json`, `ranking_version`, `user_notes` to `investigations`. Idempotent migration from v1-v5.
  - `statebisect/cli.py`: Integrated subcommands:
    - `create-investigation`: Creates investigation record with symptom, target, onset time, timezone, and notes.
    - `generate-candidates`: Ingests diffs and monitoring events, builds bounded window, ranks candidates, and persists them.
    - `list-candidates`: Lists candidates with ranks, scores, categories, and review statuses (supports `--active-only` and `--format json`).
    - `inspect-candidate`: Full inspection of score breakdown, before/after values, process provenance, coverage gaps, and audit review history.
    - `review-candidate`: Includes (`--include`) or excludes (`--exclude`) candidates with mandatory audit `--reason`. Excluded candidates are marked `is_active = False` to prevent silent inclusion in delta debugging.
  - `tests/`: Added `tests/test_correlation.py`, updated `tests/test_cli.py` and `tests/test_migration.py`. Total test count: 99 tests across 12 modules, all passing.

## Technology & Algorithm Decisions & Source Verification

1. **Deterministic Priority Ranking (Phase 6)**:
   - **Primary Source**: Delta Debugging principles (Zeller, 2002) and Microsoft Root Cause Analysis telemetry patterns.
   - **Safety Boundary**: Machine learning models and black-box scoring algorithms are strictly excluded. The ranking is a transparent heuristic for determining trial order in Delta Debugging and is **never** presented to the user as proof or probability of causation.
2. **Temporal Proximity and Post-Onset Consequence Handling (Phase 6)**:
   - **Decision**: Changes occurring *after* the reported onset time receive a low proximity score (+5) and an explicit explanatory note in the score breakdown: `"Observed after symptom onset (possible consequence rather than cause)"`.
   - **Tradeoff**: In real-world Windows failures, crash handlers, telemetry uploaders, and event logs trigger *after* the failure. Treating post-onset events as top causes leads to diagnosing the error handler rather than the root failure.
3. **Multi-Source Corroboration Bonus (Phase 6)**:
   - **Decision**: If a change was observed in both a snapshot diff and a live monitoring event, it receives a +10 corroboration bonus.
   - **Tradeoff**: Corroboration demonstrates that the change was both actively executed (event log / watcher) and persisted in system state (snapshot diff), significantly reducing false positives from transient in-memory events.
4. **Explicit User Review and Invariant on Excluded Candidates (Phase 6)**:
   - **Decision**: Users can review each candidate change and mark it `EXCLUDED` with an audit reason.
   - **Safety Invariant**: When a candidate is marked `EXCLUDED`, `is_active` is set to `False`. Downstream Delta Debugging runners and experiment generators must respect `active_only=True` and **never** silently reintroduce an excluded candidate into trial sets. All decisions are immutably logged in `candidate_reviews`.

## Phase 7: Isolated Experiment Adapters & Trial Runner
- **Completed**:
  - `statebisect/adapters/models.py`:
    - Enums: `AdapterType` (`SIMULATION`, `FAKE`, `HYPERV`, `WINDOWS_SANDBOX`), `EnvironmentStatus` (`REGISTERED`, `VERIFIED`, `CORRUPTED`, `UNAVAILABLE`), `TrialLifecycleState` (11 states: `PENDING`, `VALIDATING`, `RESTORING`, `BASELINE_VERIFIED`, `APPLYING`, `TESTING`, `CLEANING_UP`, `COMPLETED`, `FAILED`, `INCONCLUSIVE`, `CANCELLED`, `ENVIRONMENT_CORRUPTED`).
    - Dataclasses: `EnvironmentRecord`, `BaselineRecord`, `AdapterOperationResult`, `RegisteredChangeAction`, `IsolatedTrialRecord`.
    - Safety invariant enforcement: `is_simulation` properties, non-host refusal (`is_host=False`), and `is_disposable` requirement.
  - `statebisect/adapters/base.py`:
    - `BaseEnvironmentAdapter` defining typed abstract contract for initialization, baseline restoration, baseline verification, change application, health check execution, and trial teardown.
  - `statebisect/adapters/simulation_adapter.py`:
    - Pure in-memory deterministic simulation adapter wrapping `SimulatedWorkspace`. Used by default.
    - Default environment `sim-env-default` and baseline `baseline-001`.
  - `statebisect/adapters/fake_adapter.py`:
    - Configurable test-double adapter supporting fault injection (`fail_restore`, `fail_verify`, `fail_apply`, `fail_health_check`, `fail_cleanup`, `corrupt_on_cleanup_failure`, `call_history`) to rigorously test lifecycle error handling and corruption protection without external side effects.
  - `statebisect/adapters/hyperv_adapter.py`:
    - Safe Hyper-V VM adapter:
      - Validates non-host identity (`is_host=False`) and disposable registration (`is_disposable=True`).
      - Validates VM and checkpoint names against strict regex (`^[a-zA-Z0-9_\-\. ]{1,128}$`) to prevent injection.
      - Dispatches standard checkpoint restore via PowerShell (`Restore-VMSnapshot`) using `shell=False`.
      - Checks Hyper-V availability and user permissions; falls back gracefully to `UNAVAILABLE` without attempting privilege escalation.
  - `statebisect/adapters/sandbox_evaluator.py`:
    - `WindowsSandboxEvaluation` technical evaluation citing official Microsoft documentation. Documents the decision to defer Windows Sandbox due to lack of programmatic checkpoint/restore mechanisms and cold boot latency (15-30s per container launch).
  - `statebisect/adapters/registry.py`:
    - `RegisteredActionRegistry`: Allowlisted registry rejecting raw command/shell/powershell keys and risk tiers above medium.
    - `AdapterRegistry`: Manages adapter lifecycle, defaulting to `SimulationAdapter`.
  - `statebisect/adapters/runner.py`:
    - `IsolatedTrialRunner`: Orchestrates baseline restore and health-check verification *before* applying candidate changes, executes health check, enforces mandatory cleanup in an unconditional `finally` block, preserves dual exceptions without control flow statements in `finally`, locks out corrupted environments (`ENVIRONMENT_CORRUPTED`), and implements a compatibility method `run_trial(...)` for `DeltaDebugger`.
  - `statebisect/storage.py`:
    - Schema Version 7: Adds `registered_environments`, `registered_baselines`, and `isolated_trials` tables with cascade deletion and indexing.
    - Storage helper methods: `register_environment`, `get_environment`, `list_environments`, `update_environment_status`, `register_baseline`, `get_baseline`, `list_baselines`, `save_isolated_trial`, `get_isolated_trials`, `get_isolated_trial`, `update_investigation_status`.
    - Non-destructive migration from v1-v6 to v7.
  - `statebisect/cli.py`:
    - Integrated subcommands: `list-adapters`, `sandbox-evaluation`, `list-environments`, `inspect-environment`, `register-environment`, `register-baseline`, `verify-baseline`, `run-trial`, `run-minimization`.
  - `tests/`:
    - Added `tests/test_adapters.py` (18 tests covering adapters, fake fault injection, action registry rejection of shell commands, runner lifecycle state transitions, corruption protection, cancellation, Hyper-V safety checks, and sandbox evaluation).
    - Updated `tests/test_cli.py` and `tests/test_migration.py`.
    - Total test suite: 119 tests across 13 modules, all passing.

## Phase 8: Verification, Reproduction, Differential Validation, and Evidence-Backed Findings

- **Date**: March 2026
- **Status**: Complete
- **Components Implemented**:
  - `statebisect/verification/models.py`:
    - `VerificationVerdict` enum: `VERIFIED_SET`, `VERIFIED_INTERACTION`, `PARTIALLY_SUPPORTED`, `NOT_REPRODUCED`, `INCONCLUSIVE`, `INVALID_BASELINE`, `ENVIRONMENT_CORRUPTED`, `CANCELLED`.
    - `TrialRole` enum: `BASELINE_CONTROL`, `CANDIDATE_SET`, `NECESSITY_TEST`, `REVERSAL_CHECK`, `DIFFERENTIAL_BASELINE`.
    - `VerificationProtocolConfig`: Configures repeats (1 to 5), per-trial timeout (default 30.0s), differential baseline, reversal action/hook, and cancellation token.
    - Dataclasses: `VerificationTrialRecord`, `VerificationNecessityDetail`, `VerificationRunRecord`.
  - `statebisect/verification/engine.py`:
    - `VerificationEngine`: Executes dry-run `preview(...)` and the 6-stage protocol `verify(...)`.
    - Stage 1: Baseline control check ($B_1$) - verifies baseline health before testing candidates.
    - Stage 2: Candidate set reproduction with multi-attempt repetition consistency check ($k \in [1, 5]$).
    - Stage 3: Member-by-member necessity checks ($S \setminus \{c\}$) classifying 1-minimality and interaction sets.
    - Stage 4: Optional safe reversal check on modified state with mandatory `finally` baseline restoration.
    - Stage 5: Optional differential baseline validation ($B_2$).
    - Stage 6: Synthesis of evidence-backed findings and audit matrix.
  - `statebisect/verification/reporting.py`:
    - `format_verification_text`: Full human-readable diagnostic report with audit matrix, necessity analysis, differential comparison, rationale, and limitations.
    - `format_verification_json`: Structured JSON serialization.
  - `statebisect/storage.py`:
    - Bumped schema to Version 8 (`schema_version = 8`).
    - Added Migration 8: creates `verification_runs` and `verification_trials` tables with cascade deletion.
    - Added methods: `save_verification_run`, `get_verification_run`, `list_verification_runs`.
  - `statebisect/cli.py`:
    - Added subcommands: `preview-verification`, `run-verification`, `inspect-verification`, `export-verification`.
  - `tests/test_verification.py`:
    - 14 comprehensive unit and integration tests covering: verdicts (`VERIFIED_SET`, `VERIFIED_INTERACTION`, `PARTIALLY_SUPPORTED`, `NOT_REPRODUCED`, `INVALID_BASELINE`, `INCONCLUSIVE`), dry-run preview, repetition consistency, safe reversal cleanup, differential baselines, text/JSON reporting, SQLite persistence/retrieval, and CLI commands.

## Technology & Algorithm Decisions & Source Verification

1. **Delta Debugging 1-Minimality and Necessity**:
   - **Primary Source**: Zeller, A. "Yesterday, My Program Worked. Today, It Does Not. Why?" (ESEC/FSE 1999); Zeller, A. *Why Programs Fail: A Guide to Systematic Debugging* (2nd ed., Morgan Kaufmann).
   - **Decision**: In Phase 8, candidate change verification extends raw minimization by testing each subset $S \setminus \{c\}$ against the isolated environment to determine whether each individual change $c$ is strictly necessary for symptom reproduction.
   - **Distinction**:
     - *1-Minimal Set*: A failure-inducing set where removing any single change causes the failure to disappear (all elements necessary).
     - *Bystander*: A change present in $S$ whose removal still reproduces the failure ($S \setminus \{c\} \rightarrow \text{FAIL}$).
     - *Interaction*: A multi-element 1-minimal set requiring all members concurrently.
2. **Standard Checkpoints vs. Production Checkpoints (Hyper-V)**:
   - **Primary Source**: Microsoft Learn: "Choose between standard or production checkpoints in Hyper-V" (2023/2024).
   - **Decision**: StateBisect utilizes standard checkpoints (`Restore-VMSnapshot`) for isolation trials.
   - **Rationale**: Production checkpoints rely on VSS inside guest Windows to perform application-consistent backup, which does not capture complete in-memory machine state or exact point-in-time configuration differentials. Standard checkpoints capture the exact disk and memory state, enabling instantaneous and deterministic rollback to clean baselines.
3. **Windows Sandbox Technical Deferral**:
   - **Primary Source**: Microsoft Learn: "Windows Sandbox architecture and configuration" (2024).
   - **Decision**: Formally evaluate and document Windows Sandbox capabilities, but defer live adapter execution in favor of disposable Hyper-V VMs.
   - **Rationale**: Windows Sandbox is architecturally stateless and ephemeral. It does not provide an API for in-place snapshotting or rollback. Each trial would require a cold boot of a new sandbox container taking 15–30 seconds, leading to unviable test times for Delta Debugging ($O(N)$ trials).
4. **Mandatory Exception Handling in Cleanup (`finally` block)**:
   - **Decision**: Adhere strictly to the rule prohibiting `return`, `break`, or `continue` inside `finally` blocks.
   - **Safety Boundary**: Using `return` in a `finally` block suppresses any exception thrown in the `try` block. In `IsolatedTrialRunner` and `VerificationEngine`, any cleanup error is either logged or chained to the primary trial exception via `__context__`, ensuring no unexpected crash or contamination is swallowed.
5. **Environment Corruption Isolation Invariant**:
   - **Decision**: If an adapter fails to restore its baseline or cleanup a trial, its status is immediately transitioned to `ENVIRONMENT_CORRUPTED`.
   - **Safety Invariant**: All subsequent trials against that environment ID are refused immediately with an error. Contaminated environments are never reused for Delta Debugging experiments.
6. **Differential Validation Across Baselines**:
   - **Decision**: Provide optional differential baseline validation ($B_2$) to distinguish environment-portable configuration defects from defects dependent on specific base system images.

## Verification Results
- All 136 unit and integration tests passing (`python -m unittest discover -s tests -v`).
- Phase 1 programmatic demonstration script runs and passes (`python demo_phase1.py`).
- Phase 8 CLI workflow verified end-to-end (`preview-verification`, `run-verification`, `inspect-verification`, `export-verification`).
- Schema migrations from v1 through v8 verified with zero data loss.

## Phase 9: Local Investigation API and Dashboard

- **Date**: March 2026
- **Status**: Complete
- **Components Implemented**:
  - `statebisect/dashboard/server.py`:
    - Loopback-only HTTP server built on Python standard library `http.server.ThreadingHTTPServer` with daemon request threads.
    - Security Boundaries:
      - Strictly binds to `127.0.0.1` or `::1`; rejects any attempt to bind to `0.0.0.0` or external network interfaces with `ValueError`.
      - DNS Rebinding Defense: Validates `Host` header against loopback origins (`127.0.0.1`, `localhost`, `::1`).
      - Untrusted Origin Defense: Rejects cross-origin `Origin` headers from non-loopback domains with HTTP 403 Forbidden.
      - Anti-CSRF Custom Header: Requires `X-StateBisect-Request: 1` or `X-Requested-With` on all state-changing methods (`POST`, `PUT`, `DELETE`).
      - Defensive Headers: Injects `Content-Security-Policy: default-src 'self' ...`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and `Cache-Control: no-store`.
      - Body Size Limits: Limits request bodies to `MAX_BODY_BYTES = 1_048_576` (1 MB) to prevent denial-of-service / memory exhaustion.
      - Path Traversal Defense: Verifies resolved file paths remain strictly inside `static/` directory using `os.path.commonpath`.
  - `statebisect/dashboard/handlers.py`:
    - `DashboardApiRouter` with synchronized `_lock = threading.Lock()` around dispatch to ensure serialized, thread-safe SQLite access.
    - Endpoints:
      - `GET /api/overview`: System status, active counts, recent collection/monitoring runs, and transparency disclaimers.
      - `GET /api/environments`: Registered environments and baselines with simulation vs. physical guest badges.
      - `GET /api/investigations`: List investigations with query filtering.
      - `POST /api/investigations`: Create new investigation with validated parameters.
      - `GET /api/investigations/{id}`: Detailed investigation record.
      - `GET /api/investigations/{id}/timeline`: Unified chronological timeline with post-onset consequence tagging.
      - `GET /api/investigations/{id}/candidates`: Ranked candidate changes with explicit disclaimer and score breakdowns.
      - `GET /api/candidates/{id}`: Candidate details and immutable review history.
      - `POST /api/candidates/{id}/review`: Audited user review (`include` or `exclude` with mandatory audit reason). Updates active status to prevent silent inclusion in Delta Debugging.
      - `POST /api/investigations/{id}/generate-candidates`: Safe candidate generation from telemetry diffs and monitoring sessions.
      - `GET /api/investigations/{id}/trials`: Isolated trial history and lifecycle states.
      - `GET /api/investigations/{id}/verifications`: Verification runs, necessity details, and differential validation findings.
      - `GET /api/verification/{id}`: Detailed verification audit record.
      - `GET /api/investigations/{id}/report`: Formatted diagnostic report preview (text or JSON).
      - `GET /api/investigations/{id}/report/download`: Downloadable attachment (`Content-Disposition: attachment; filename=...`).
      - `GET /api/collections`: Telemetry collection history.
      - `GET /api/monitoring-sessions`: Live monitoring session history.
  - `statebisect/dashboard/serializers.py`:
    - Safe JSON serialization scrubbing sensitive credentials, tokens, and passwords using `safe_redact`.
    - Bounds string lengths and collection sizes to protect against memory bloat.
  - `statebisect/dashboard/timeline.py`:
    - `TimelineBuilder` synthesizing unified chronological timelines from symptom onset, snapshot diffs, monitoring events, candidates, reviews, trials, and verifications.
    - Post-onset tagging (`is_post_onset = True` when event timestamp > onset time) to clearly flag crash consequences vs. potential root causes.
  - `statebisect/dashboard/static/`:
    - Single-Page Dashboard built with semantic HTML5, accessible ARIA attributes, responsive CSS variables, and vanilla ES2022 JavaScript.
    - Zero external dependencies: no third-party CDNs, npm packages, or external trackers.
    - Real-time views: Overview, Investigations, Detail Workspace (Timeline, Ranked Candidates, Isolated Trials, Verification Runs, Reports), and Environments.
  - `statebisect/storage.py`:
    - Updated `Storage.__init__` to support `check_same_thread=False` with synchronized application locking.
  - `statebisect/cli.py`:
    - Added `cmd_dashboard` and `dashboard` CLI parser (`statebisect dashboard [--host 127.0.0.1] [--port 8080] [--no-browser]`).
  - `tests/test_dashboard.py`:
    - 18 comprehensive tests covering loopback binding, Host/Origin/CSRF validation, path traversal defense, payload size limits, endpoints, candidate review audit trails, report downloads, and exception handling without `finally` control flow.

## Technology & Architecture Decisions (Phase 9)

1. **Standard Library `ThreadingHTTPServer` over Third-Party Frameworks**:
   - **Decision**: Implemented dashboard HTTP server using Python 3.12 `http.server.ThreadingHTTPServer` without pulling in Flask, FastAPI, Starlette, or Uvicorn.
   - **Rationale**: StateBisect adheres strictly to the rule avoiding unnecessary dependencies. Python's built-in `ThreadingHTTPServer` provides complete HTTP/1.1 serving, daemon thread pooling, and header customization with zero external supply-chain risk and zero third-party license obligations.
2. **Strict Loopback Binding & Defense-in-Depth Local Security**:
   - **Decision**: Server binds exclusively to `127.0.0.1` or `::1`. Enforces RFC 2616 Host validation, CORS loopback verification, anti-CSRF custom headers (`X-StateBisect-Request: 1`), and strict CSP (`default-src 'self'`).
   - **Rationale**: Local web dashboards on developer workstations are vulnerable to DNS rebinding and cross-site scripting/form submission attacks from malicious browser tabs. By requiring custom headers and strict loopback origins, cross-origin web pages cannot query or trigger actions on the local StateBisect API.
3. **Zero Host Execution Endpoints**:
   - **Decision**: The dashboard API contains zero endpoints for arbitrary shell, powershell, python, SQL, or command execution against the host machine.
   - **Safety Boundary**: The dashboard is an investigation viewer and controller for isolated simulations/guests only. It never executes live code on the host Windows environment.
4. **Transparent Disclaimer on Candidate Ranking**:
   - **Decision**: The candidate API and dashboard UI prominently display: *"Ranking reflects experimental priority for Delta Debugging, NOT proof or probability of fault."*
   - **Rationale**: Prevents users from misinterpreting algorithmic candidate order as an AI diagnosis or guaranteed cause prior to empirical verification.
5. **Mandatory Exception-Handling Rule in Cleanup**:
   - **Decision**: Maintained strict adherence to the project rule prohibiting `return`, `break`, or `continue` inside any `finally` block; used `finally` exclusively for server socket closing and cleanup.

## Verification Results
- All 154 unit and integration tests passing (`python -m unittest discover -s tests -v`).
- Phase 1 programmatic demonstration script runs and passes (`python demo_phase1.py`).
- Phase 9 dashboard CLI workflow verified end-to-end (`statebisect dashboard --help`).
- Schema migrations from v1 through v8 verified with zero data loss.

## Next Phase: Phase 10 — AI Explanation Layer & Synthesis (Completed)
- **Status**: Completed.
- **Deliverables Delivered**:
  - Grounded AI explanation layer explaining verification findings without hallucinatory repair claims.
  - Typed evidence contracts taking verified evidence sets and diagnostic matrices.

## Phase 10 Implementation: Grounded AI Explanation Layer

### Overview
Phase 10 introduces an optional, evidence-grounded AI explanation layer that synthesizes saved investigation evidence, candidate change rankings, trial outcomes, and verification run records into human-readable causal narratives and safe next-step questions.

Crucially, **the deterministic engine remains the sole authority** for all empirical findings, candidate scoring, trial executions, and verification verdicts. The AI layer is strictly an explanatory interpreter: it cannot declare verified causality without an engine verification run, cannot alter candidate scores, cannot execute commands or repairs, and cannot hide coverage gaps or uncertainty.

### Architecture & Key Components
1. **`statebisect/ai/models.py`**:
   - `ProviderStatus`: Runtime health, readiness, and configured model for explanation providers.
   - `DataClassification`: Strict data classification metadata (`CONFIDENTIAL`, `RESTRICTED`, `PUBLIC`).
   - `EvidenceReference`: Formal citation contract tying claims to concrete evidence IDs (`ev-inv-*`, `ev-cand-*`, `ev-verif-*`, `ev-trial-*`, `ev-gap-*`).
   - `EvidenceObservation`: Grounded observation with mandatory citation links to empirical evidence.
   - `Hypothesis`: Plausible causal explanation with explicit plausibility tier (`HIGH`, `MEDIUM`, `LOW`, `SPECULATIVE`), required citations, counter-evidence, and testable differentiation steps.
   - `ContextPreview`: Scrubbed and bounded data bundle showing exact tokens, size in bytes, and item counts before any remote transmission.
   - `ExplanationResponse`: Structured AI explanation output with observations, hypotheses, coverage gaps, safe questions, and validator flags.
   - `AiExplanationRecord`: Persistent audit record storing explanation runs, prompts, responses, provider IDs, context hashes, and timestamps.
2. **`statebisect/ai/context.py` (`EvidenceContextBuilder`)**:
   - **Secret Scrubbing**: Automatic redaction of credentials, API keys, passwords, and bearer tokens using `redact_sensitive_text`.
   - **Size Bounding**: Enforces strict safety limits (max 30 candidates, 20 trials, 10 verifications, 25 coverage gaps).
   - **Stable Evidence IDs**: Generates predictable citation targets (`ev-inv-<id>`, `ev-cand-<id>`, `ev-verif-<id>`, `ev-trial-<id>`, `ev-gap-<idx>`).
   - **Context Hashing**: Computes deterministic SHA-256 fingerprint of the scrubbed context for cryptographic audit integrity.
   - **Prompt Injection Defense**: Wraps untrusted user symptoms, logs, and notes within explicit XML-style boundary delimiters (`<EVIDENCE_DATA>`) per OWASP guidelines, instructing models to treat contained text solely as passive diagnostic data.
3. **`statebisect/ai/validator.py` (`OutputValidator`)**:
   - **Grounding Verification**: Validates all citations against context evidence IDs; flags or rejects hallucinated citations.
   - **Causality Invariants**: Enforces that a hypothesis cannot claim verified root cause unless supported by an engine `VERIFIED_SET` verification run.
   - **XSS Defense**: Sanitizes observations, hypotheses, and safe questions against script injection and unescaped HTML tags.
4. **`statebisect/ai/service.py` (`ExplanationService`)**:
   - **Gated Remote Consent**: Rejects calls to remote providers (e.g. OpenAI) unless `confirm_remote=True` is explicitly passed after user review of the context preview.
   - **Deterministic Fallback Summary**: Generates 100% offline, deterministic heuristic summaries when AI is disabled, offline, or times out.
   - **Audit Trail**: Persists all generated explanations and reviews into SQLite table `ai_explanations`.
5. **`statebisect/ai/providers/`**:
   - `base.py`: Provider ABC defining `generate_explanation`, `validate_credentials`, and `get_status`.
   - `mock_provider.py`: Deterministic mock provider with adversarial test flags (`force_invalid_citations`, `force_malformed_json`, `force_timeout`, `force_refusal`).
   - `openai_provider.py`: Zero-external-dependency implementation using Python 3.12 standard library `urllib.request`. Uses OpenAI Structured Outputs (`response_format: {"type": "json_schema", ...}`) with `gpt-4o-mini` (or `gpt-4o`), exponential backoff on HTTP 429, 30s timeout, and in-memory ephemeral key handling (`STATEBISECT_OPENAI_API_KEY`).
6. **`statebisect/ai/mcp_tools.py` (`ReadOnlyMcpServer`)**:
   - Implements standard Model Context Protocol (MCP) JSON-RPC 2.0 tool server exposing 4 read-only tools:
     - `get_investigation_summary`: High-level symptom, status, and baseline metadata.
     - `list_evidence_ids`: Available stable evidence IDs and summaries.
     - `get_saved_report`: Rendered text investigation report.
     - `get_candidate_or_verification`: Details for a specific candidate or verification run.
   - Strict `readOnlyHint: true` annotation on all exposed tools; rejects any write or execution calls.
7. **`statebisect/storage.py` & Schema Migration 9**:
   - Updated `LATEST_SCHEMA_VERSION = 9`.
   - Created `ai_explanations` table with columns: `id`, `investigation_id`, `created_at`, `provider_type`, `provider_model`, `user_question`, `context_hash`, `prompt_preview`, `raw_response_json`, `status`, `validation_flags_json`.
   - Added index `idx_ai_explanations_inv` on `investigation_id`.
   - Added persistence methods: `save_ai_explanation`, `get_ai_explanation`, `list_ai_explanations`, `delete_ai_explanations`.
8. **Dashboard & API Integration**:
   - Added REST endpoints: `GET /api/ai/status`, `GET /api/investigations/{id}/ai/context-preview`, `POST /api/investigations/{id}/ai/explain`, `GET/DELETE /api/investigations/{id}/ai/history`, `POST /api/investigations/{id}/ai/fallback-summary`, and `POST /api/mcp`.
   - Added `do_DELETE` handler in `DashboardServer` with Host/Origin/CSRF validation.
   - Added interactive "🤖 Ask AI Explainer" subtab in the dashboard UI with context preview drawer, remote consent confirmation checkbox, question input with quick suggestions, grounded observation cards with citation pills, hypotheses cards, and history management.
9. **CLI Commands**:
   - Added `ai-status`, `ai-context-preview`, `ai-explain`, `ai-history`, and `ai-clear-history` commands with human-readable table and JSON output formats.
10. **Testing Suite (`tests/test_ai.py`)**:
    - 14 comprehensive tests covering grounding validation, hypothesis invariants, prompt-injection defense, secret scrubbing, remote consent gating, MCP tool contracts, fallback summary generation, and static AST inspection verifying zero `return`/`break`/`continue` inside `finally` blocks.

## Technology & Architecture Decisions (Phase 10)

1. **Zero External Dependencies for LLM Integration**:
   - **Decision**: Implemented `OpenAIExplanationProvider` directly using Python 3.12 standard library `urllib.request` and `json` rather than requiring the `openai` PyPI package.
   - **Rationale**: Preserves StateBisect's core tenet of zero external dependencies. Eliminates transitive package bloat, supply-chain risks, and binary build requirements while leveraging OpenAI's native JSON Schema Structured Outputs.
2. **Sole Authority Principle**:
   - **Decision**: The AI layer cannot declare cause, alter scores, or run experiments. Causal claims require verification run backing (`VERIFIED_SET`).
   - **Rationale**: LLMs are statistical generators susceptible to hallucination. Diagnostic integrity demands that causal assertions rest strictly on empirical delta debugging and verification experiments.
3. **Explicit Consent & Redacted Previews**:
   - **Decision**: Remote providers require explicit opt-in confirmation (`confirm_remote=True`). Users can inspect exact token counts, byte sizes, and redacted fields via context previews before data leaves their machine.
   - **Rationale**: Prevents accidental leakage of proprietary system logs, registry keys, or environment data to third-party cloud APIs.
4. **OWASP Prompt Injection Delimitation**:
   - **Decision**: Embedded untrusted logs and system metadata inside `<EVIDENCE_DATA>` XML-style boundaries with strict system prompt directives.
   - **Rationale**: Prevents malicious log payloads from overriding system rules or hijacking model instructions.
5. **Strict Read-Only MCP Tool Contract**:
   - **Decision**: Exposed Model Context Protocol (MCP) server implements only 4 read-only inspection tools with `readOnlyHint: true`.
   - **Rationale**: Ensures AI agents or IDE assistants interacting via MCP can safely inspect StateBisect investigations without the capability to modify state, trigger unmonitored experiments, or execute commands.

## Verification Results
- All 172 unit and integration tests passing (`python -m unittest discover -s tests -v`).
- Phase 1 programmatic demonstration script runs and passes (`python demo_phase1.py`).
- Phase 10 AI CLI workflows verified end-to-end (`statebisect ai-status`).
- Schema migrations from v1 through v9 verified with zero data loss.

## Next Phase: Phase 11 — Benchmarking, Release Hardening, & Distribution
- **Entry Criteria**: Phase 10 acceptance criteria met (evidence-grounded AI explanation layer, OpenAI Structured Outputs via stdlib, deterministic mock provider, fallback summaries, schema migration 9, read-only MCP server, 172 tests passing).
- **Upcoming Deliverables**:
  - Reproducible benchmark suites measuring isolation overhead, search efficiency, and accuracy.
  - Performance profiling and memory optimizations for long-running investigations.
  - Final packaging, distribution artifacts, and release documentation.
  - Strict preservation of the safety boundary: NO automatic live-system repairs.
