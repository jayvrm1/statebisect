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

## Known Limitations
- Continuous monitoring and real-time event streaming are not yet active (scheduled for Phase 5).
- Collection is point-in-time snapshotting; changes that occur between snapshot runs without leaving event log or registry traces are not captured.
- Correlating collected host snapshots into disposable VM experiment environments is scheduled for Phase 7.

## Next Phase: Phase 5 — Targeted Continuous Collection & Lightweight Monitoring
- **Entry Criteria**: Phase 4 acceptance criteria met (read-only Windows collector, 7 sources, gap-aware differ, Schema Version 4 migrations, 69 tests passing, verified standard-user safety).
- **Upcoming Deliverables**:
  - Lightweight event monitoring daemon / file watcher.
  - Time-windowed change correlation near reported failure onset.
  - Candidate change extraction for feeding into Delta Debugging scenarios.
