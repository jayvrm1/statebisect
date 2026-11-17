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

## Verification Results
- All 99 unit and integration tests passing (`python -m unittest discover -s tests -v`).
- Phase 1 programmatic demonstration script runs and passes (`python demo_phase1.py`).
- Full CLI investigation and candidate correlation workflow verified end-to-end (`create-investigation`, `generate-candidates`, `list-candidates`, `inspect-candidate`, `review-candidate`).
- Schema migrations from v1 through v6 verified with zero data loss.

## Next Phase: Phase 7 — Isolated Trial Runner & VM/Sandbox Abstraction
- **Entry Criteria**: Phase 6 acceptance criteria met (candidate generation from diffs/events, deterministic explainable ranking, bounded correlation window, user review audit trail, Schema Version 6 migrations, 99 tests passing).
- **Upcoming Deliverables**:
  - Virtual machine / Windows Sandbox isolation boundary for safe reproduction of candidate changes.
  - Safe state injection and snapshot revert lifecycle for Hyper-V / Sandbox.
  - Integration of prioritized candidate sets with Delta Debugging runner in isolated environments.
