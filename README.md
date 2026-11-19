# StateBisect

> **Notice: Evidence-Grounded AI Explanation Layer (Phase 10)**  
> StateBisect is currently in a Phase 10 state. It features an optional, provider-neutral AI explanation layer grounded strictly in recorded investigation evidence. **The deterministic engine remains the sole authority for verified findings, candidate rankings, and safety decisions.** AI explanations are explicitly labeled as evidence summaries and unverified hypotheses; the AI layer cannot alter engine scores or verdicts, execute commands, modify configuration, or perform repairs. Data privacy is strictly enforced: remote transmission requires explicit user confirmation and context review, while an offline deterministic mock provider and fallback summary ensure full functionality without external network calls or credentials. Zero modifications are made to the host Windows registry, files, services, or system configuration.

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
│   ├── monitor/             # Targeted change monitoring (Phase 5)
│   │   ├── base.py          # BaseMonitorSource abstract class & loss tracking
│   │   ├── engine.py        # MonitoringEngine & foreground session lifecycle
│   │   ├── models.py        # MonitoringSession, MonitoringEvent, MonitoringCoverageGap
│   │   └── sources/         # 4 Targeted monitoring sources
│   │       ├── event_log.py # Polling of Application and System channels
│   │       ├── registry_watcher.py # Allowlisted user registry observer
│   │       ├── sysmon.py    # Pre-installed Sysmon detection & query
│   │       └── etw_evaluator.py # Safe ETW evaluation & documented deferral
│   ├── correlation/         # Candidate change generation & correlation (Phase 6)
│   │   ├── models.py        # CandidateChange, CorrelationWindow, ScoreBreakdown, ReviewRecord
│   │   ├── ranker.py        # Deterministic, explainable candidate scoring and ranking
│   │   └── engine.py        # Bounded correlation window & multi-source corroboration
│   ├── adapters/            # Isolated experiment adapters & trial runner (Phase 7)
│   │   ├── base.py          # BaseEnvironmentAdapter abstract lifecycle contract
│   │   ├── models.py        # EnvironmentRecord, BaselineRecord, IsolatedTrialRecord, enums
│   │   ├── simulation_adapter.py # In-memory deterministic simulation adapter
│   │   ├── fake_adapter.py  # Test-double adapter with fault injection capabilities
│   │   ├── hyperv_adapter.py # Non-host Hyper-V adapter with standard checkpoint rollback
│   │   ├── sandbox_evaluator.py # Windows Sandbox technical evaluation & deferral rationale
│   │   ├── registry.py      # Action & Adapter registries (with command-key rejection)
│   │   └── runner.py        # IsolatedTrialRunner orchestrating baseline restore & verify
│   ├── verification/        # Verification, necessity & differential validation (Phase 8)
│   │   ├── __init__.py      # Verification exports
│   │   ├── models.py        # Verdicts, roles, protocol configs, necessity details, run records
│   │   ├── engine.py        # 6-stage VerificationEngine, dry-run preview, necessity analysis
│   │   └── reporting.py     # Verification audit matrix and text/JSON formatters
│   ├── dashboard/           # Local investigation API & dashboard (Phase 9)
│   │   ├── __init__.py      # Dashboard exports (DashboardServer, run_dashboard)
│   │   ├── server.py        # Loopback HTTP server (ThreadingHTTPServer, Host/Origin/CSRF validation)
│   │   ├── handlers.py      # Typed REST API router & endpoint handlers
│   │   ├── serializers.py   # Safe JSON serializers with size bounds & redaction
│   │   ├── timeline.py      # Unified chronological timeline with post-onset consequence markers
│   │   └── static/          # Single-Page Dashboard assets (HTML5/CSS3/ES2022)
│   │       ├── index.html   # Semantic, accessible dashboard interface
│   │       ├── style.css    # High-contrast responsive styling
│   │       └── app.js       # Modern vanilla ES2022 dashboard controller
│   ├── ai/                  # Grounded AI explanation layer (Phase 10)
│   │   ├── __init__.py      # AI exports
│   │   ├── models.py        # Typed data contracts (Observations, Hypotheses, EvidenceReference, Preview)
│   │   ├── context.py       # EvidenceContextBuilder with secret scrubbing, size bounds & context hash
│   │   ├── validator.py     # OutputValidator checking citation grounding, causality invariants & XSS
│   │   ├── service.py       # ExplanationService orchestrating providers, fallback summaries & audit log
│   │   ├── mcp_tools.py     # Read-only Model Context Protocol (MCP) JSON-RPC 2.0 tool server
│   │   └── providers/       # Pluggable LLM and mock explanation providers
│   │       ├── base.py      # ExplanationProvider abstract interface
│   │       ├── mock_provider.py # Deterministic mock provider with adversarial test flags
│   │       └── openai_provider.py # Zero-dep OpenAI Structured Outputs provider (urllib.request)
│   ├── benchmark/           # Reproducible benchmarking & metrics (Phase 11)
│   │   ├── __init__.py      # Benchmark exports
│   │   ├── models.py        # BenchmarkScenario, ScenarioExecutionResult, BenchmarkRunRecord, MetricFraction
│   │   ├── suite.py         # BenchmarkSuite loader, validator, and SHA-256 fingerprinting
│   │   ├── runner.py        # BenchmarkRunner bounded executor (trials/timeout/memory bounds)
│   │   ├── metrics.py       # Exact accuracy, precision/recall, 1-minimality, bystander rejection
│   │   ├── reporting.py     # High-contrast text tables and JSON audit formatters
│   │   └── scenarios/       # 15 canonical ground-truth JSON benchmark scenarios
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
│   ├── test_hardening.py    # Phase 11 AST exception audit, loopback/host security, CSRF, and secret scrubbing
│   ├── test_benchmark.py    # Phase 11 benchmark suite, 15 scenarios, metrics, cancellation, and persistence
│   ├── test_ai.py           # Phase 10 AI explanation grounding, citations, invariants, MCP, and security
│   ├── test_dashboard.py    # Phase 9 dashboard API, security boundaries, and CSRF tests
│   ├── test_verification.py # Phase 8 verification protocol, necessity, reversal, and CLI tests
│   ├── test_adapters.py     # Phase 7 adapter lifecycle, runner, and Hyper-V safety tests
│   ├── test_correlation.py  # Phase 6 candidate generation, ranking, and audit tests
│   ├── test_monitor.py      # Phase 5 monitoring engine, sources, and lifecycle tests
│   ├── test_collector.py    # Phase 4 collector, sources, diffing, and gap tests
│   ├── test_health_checks.py# Phase 3 health check, evidence, and safety tests
│   ├── test_scenarios.py    # Scenario schema validation and safety tests
│   ├── test_cli.py          # CLI commands, formats, exit codes, and path tests
│   ├── test_migration.py    # Schema migrations (v1->v2->v3->v4->v5->v6) and audit logging tests
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

## 7. Candidate Change Generation & Correlation Engine (Phase 6)

StateBisect bridges observed Windows telemetry with experiment execution by extracting grounded candidate changes and ranking them using transparent, evidence-based signals.

### Core Principles:
- **Grounded in Observations**: Candidates are derived strictly from observed snapshot differences (`compare_snapshots`) and monitoring events. The system **never** synthesizes hypothetical or unobserved changes.
- **Correlation is Not Causation**: Candidate scores reflect temporal proximity, evidence strength, target relevance, and corroboration. Ranking determines the initial priority order for Delta Debugging experimentation—it is **never** presented as proof or probability of fault.
- **Deterministic & Explainable**: Ranking uses fully explainable score breakdowns with zero machine learning or opaque heuristics. Tie-breaking is deterministic (Score -> Proximity -> Weight -> Candidate ID).
- **Explicit User Review Audit**: Users can inspect, include (`--include`), or exclude (`--exclude`) candidates with mandatory audit justifications (`--reason`). Excluded candidates are disabled from downstream delta-debugging experiments (`is_active = False`) and tracked in the database.

### Transparent Scoring Breakdown (0 to 100 points):
1. **Evidence Type Strength (0 - 30 pts)**:
   - Confirmed snapshot difference: +30 pts
   - Direct system registry / service change: +25 pts
   - Event log observation: +18 pts
   - Application telemetry: +12 pts
2. **Temporal Proximity to Onset (0 - 30 pts)**:
   - Within 60 seconds before onset: +30 pts
   - Within 5 minutes before onset: +25 pts
   - Within 15 minutes before onset: +18 pts
   - Within 1 hour before onset: +10 pts
   - Beyond 1 hour or untimed fallback: +5 pts
   - *Post-onset changes*: Receive 5 pts and are flagged in the scoring rationale as potential consequences/symptoms of the failure rather than causes.
3. **Target Relevance (0 - 20 pts)**:
   - Direct string match to target application/system name: +20 pts
   - Known system infrastructure/dependency category (services, network, drivers): +10 pts
   - Generic/unrelated components: +0 pts
4. **Process Attribution Provenance (0 - 10 pts)**:
   - `EVENT_PAYLOAD` with recorded PID and process name: +10 pts
   - `PROCESS_LOOKUP` (table match at event time): +6 pts
   - `UNAVAILABLE`: +2 pts
5. **Multi-Source Corroboration (0 - 10 pts)**:
   - Change observed across multiple independent sources (e.g. registry watcher event + snapshot diff): +10 pts bonus.
6. **Coverage Gap Penalty (0 to -10 pts)**:
   - If telemetry sources in that category reported permission denials or loss, a -10 pt penalty is applied with explicit notation of uncertainty.

### Bounded Correlation Window:
- Pre-onset lookback (default: 3600s) and post-onset bounds (default: 300s).
- Robust timezone parsing (ISO, UTC, local offset).
- **Untimed Fallback**: If symptom onset time is unknown or omitted, the engine falls back to evidence strength and target relevance without failing.

## 8. Isolated Experiment Adapters & Trial Runner (Phase 7)

StateBisect provides a strict isolation boundary between the host operating system and experiment trials. **Under no circumstances does StateBisect execute candidate change trials directly against the host Windows operating system.**

### Supported Environment Adapters:
1. **`SimulationAdapter` (Default, In-Memory)**:
   - Built on `SimulatedWorkspace`.
   - Modifies in-memory dictionary state and evaluates health checks deterministically.
   - Used by default (`is_simulation = True`) for rapid, non-destructive experimentation and testing.
2. **`FakeAdapter` (Test Double)**:
   - Configurable test-double adapter supporting fault injection (`fail_restore`, `fail_verify`, `fail_apply`, `fail_health_check`, `fail_cleanup`, `corrupt_on_cleanup_failure`).
   - Verifies trial runner state machine transitions, recovery pathways, and dual-error handling under adversity without modifying external systems.
3. **`HyperVAdapter` (Disposable VM Isolation)**:
   - Connects to dedicated, disposable Hyper-V virtual machines.
   - **Safety Invariants**:
     - Explicit user registration: Environment must be explicitly marked `is_disposable=True`.
     - Host refusal: Reject any environment attempting to target the host system (`is_host=False`).
     - Identifier validation: VM names and snapshot names must match `^[a-zA-Z0-9_\-\. ]{1,128}$` (command injection defense).
     - Standard checkpoint restore: Utilizes standard Hyper-V snapshots via `Restore-VMSnapshot` (with standard user privilege checks and `shell=False` execution). Standard checkpoints capture disk and RAM state at an exact point in time.
     - Privilege fallback: If the standard user lacks Hyper-V administrator privileges or Hyper-V is disabled, returns `UNAVAILABLE` rather than crashing or attempting privilege escalation.
4. **Windows Sandbox Technical Evaluation**:
   - Windows Sandbox was evaluated against Microsoft documentation (Windows Sandbox architecture, `WindowsSandbox.exe`, `.wsb` configuration files).
   - **Deferral Rationale**: Windows Sandbox is an ephemeral container that discards all state on exit. It lacks programmatic named checkpoint/restore mechanisms (no snapshotting API). Cold booting a new sandbox container for every trial incurs 15–30s latency per trial, making multi-trial delta debugging impractical. It is formally deferred with full evaluation documentation.

### Trial Lifecycle State Machine:
Isolated trials progress through 11 explicit lifecycle states:
`PENDING` $\rightarrow$ `VALIDATING` $\rightarrow$ `RESTORING` $\rightarrow$ `BASELINE_VERIFIED` $\rightarrow$ `APPLYING` $\rightarrow$ `TESTING` $\rightarrow$ `CLEANING_UP` $\rightarrow$ `COMPLETED` / `FAILED` / `INCONCLUSIVE` / `CANCELLED` / `ENVIRONMENT_CORRUPTED`.

- **Pre-Trial Verification**: Baseline state is restored and health check is verified *before* applying candidate changes. If the baseline fails health check before changes are applied, the trial aborts immediately.
- **Mandatory Cleanup**: Teardown and baseline restore execute in an unconditional `finally` block. A trial is never considered successful if cleanup fails.
- **Environment Corruption Protection**: If cleanup or baseline restore fails, the environment is marked `ENVIRONMENT_CORRUPTED` and locked out from future trials to prevent contamination.
- **Dual-Exception Preservation**: Cleanups in `finally` blocks never contain `return`, `break`, or `continue`. If an error occurs during both trial execution and cleanup, both exceptions are preserved using Python's `__context__` exception chaining.
- **Allowlisted Action Registry**: Only pre-registered, safe change actions can be applied. Actions specifying raw `command`, `shell`, or `powershell` keys or risk tiers above medium are strictly rejected.

## 9. Verification, Reproduction & Differential Validation Engine (Phase 8)

StateBisect provides a rigorous verification and differential validation engine that tests whether candidate changes isolated during investigation are genuinely failure-inducing, 1-minimal, reversible, and robust across baselines.

### 6-Stage Verification Protocol:
1. **Baseline Control Check**:
   - Executes the failure health check against the clean primary baseline $B_1$ with no changes applied.
   - If the baseline is unhealthy, the run immediately aborts with verdict `INVALID_BASELINE`. Never tests changes against an already-broken baseline.
2. **Candidate Set Reproduction**:
   - Applies the entire candidate set $S$ and evaluates health with configurable repeats ($k \in [1, 5]$).
   - If all repeats pass, symptom reproduction failed: verdict `NOT_REPRODUCED`.
   - If repeats disagree (intermittent/flaky symptom), marked `INCONCLUSIVE` (inconsistent reproduction).
3. **Member-by-Member Necessity Analysis**:
   - For each candidate $c \in S$, tests the subset $S \setminus \{c\}$ to evaluate necessity.
   - If $S \setminus \{c\}$ fails: $c$ is a **bystander** (symptom still reproduces without it).
   - If $S \setminus \{c\}$ passes: $c$ is **necessary** for the failure.
   - Single-element sets where $S = \{c\}$ are classified as `VERIFIED_SET` (1-minimal).
   - Multi-element sets where all members are required are classified as `VERIFIED_INTERACTION` (multi-change interaction).
   - Sets where only some members are necessary are classified as `PARTIALLY_SUPPORTED`.
4. **Safe Reversal Validation (Optional)**:
   - If a reversal action or hook is provided, verifies that restoring or undoing the change resolves the failure.
   - Enforces dual-exception safety and mandatory cleanup in `finally` blocks.
5. **Differential Baseline Validation (Optional)**:
   - Evaluates whether the candidate set $S$ reproduces the failure against an alternative verified baseline $B_2$ (e.g. a different OS configuration or build).
   - Distinguishes environment-portable causes from baseline-specific defects.
6. **Synthesis into Evidence-Backed Findings**:
   - Synthesizes all trial results, necessity classifications, and differential comparisons into an audit matrix with explicit rationale and limitations.

### Explicit Verification Verdicts:
- `VERIFIED_SET`: Candidate set reproduces failure and is strictly 1-minimal.
- `VERIFIED_INTERACTION`: Multi-change set requires all members concurrently to trigger failure.
- `PARTIALLY_SUPPORTED`: Failure reproduced, but some candidates were identified as bystanders.
- `NOT_REPRODUCED`: Symptom did not manifest when candidates were applied.
- `INCONCLUSIVE`: Repeated health check attempts disagreed or timed out.
- `INVALID_BASELINE`: Baseline control check failed prior to change application.
- `ENVIRONMENT_CORRUPTED`: Target environment encountered cleanup or restore failure and was quarantined.
- `CANCELLED`: User interrupted the verification run.

### Limitations & Causality Disclaimer:
- **Evidence-Backed, Not Causal Proof**: Verification results prove reproduction in the tested environment under specific health-check conditions. They do not claim universal causality or system-wide immunity.

## 10. Persistence, Evidence Model & Migrations

- Default database: `data/statebisect.db`.
- **Schema Migrations**:
  - Automatically manages migrations up to Version 8 in `schema_migrations`.
  - Non-destructive and idempotent: preserves all existing investigations, trials, snapshots, monitoring sessions, candidates, environments, and baselines.
  - Tables:
    - `investigations`, `experiment_trials`, `audit_logs`, `health_check_evidence` (Phases 1–3).
    - `collection_runs`, `snapshot_records`, `coverage_gaps`, `snapshot_diffs` (Phase 4).
    - `monitoring_sessions`, `monitoring_events`, `monitoring_coverage_gaps` (Phase 5).
    - `investigation_candidates`, `candidate_reviews` (Phase 6).
    - `registered_environments`, `registered_baselines`, `isolated_trials` (Phase 7).
    - `verification_runs`, `verification_trials` (Phase 8).

## 11. Command-Line Interface (CLI)

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
python statebisect_cli.py monitor-preview
python statebisect_cli.py monitor-start --duration 30
python statebisect_cli.py monitor-start --duration 30 --with-snapshots
python statebisect_cli.py list-monitor-sessions
python statebisect_cli.py inspect-monitor-session <session_id>
python statebisect_cli.py inspect-monitor-session <session_id> --format json

# --- Candidate Generation & Review Commands (Phase 6) ---
python statebisect_cli.py create-investigation --symptom "Database connection timeout" --target "PostgresApp" --onset "2026-03-30 14:00:00"
python statebisect_cli.py generate-candidates <inv_id> --before-run <run_a> --after-run <run_b> --monitor-session <sess_id>
python statebisect_cli.py list-candidates <inv_id>
python statebisect_cli.py list-candidates <inv_id> --active-only
python statebisect_cli.py list-candidates <inv_id> --format json
python statebisect_cli.py inspect-candidate <candidate_id>
python statebisect_cli.py review-candidate <candidate_id> --exclude --reason "Verified routine antivirus definition update"
python statebisect_cli.py review-candidate <candidate_id> --include --reason "Target config modified near onset"

# --- Isolated Adapters & Trial Runner Commands (Phase 7) ---
# 1. List available adapters and inspect Windows Sandbox technical evaluation
python statebisect_cli.py list-adapters
python statebisect_cli.py sandbox-evaluation

# 2. Register, list, inspect, and verify environments and baselines
python statebisect_cli.py register-environment --id test-vm-01 --name "Disposable Dev VM" --type hyperv --is-disposable
python statebisect_cli.py list-environments
python statebisect_cli.py inspect-environment sim-env-default
python statebisect_cli.py register-baseline --id baseline-clean --env-id sim-env-default --name "Clean Baseline" --snapshot-ref "clean_v1"
python statebisect_cli.py verify-baseline sim-env-default baseline-001

# 3. Execute an isolated trial
python statebisect_cli.py run-trial --env-id sim-env-default --baseline-id baseline-001 --changes c1,c2

# 4. Run Delta Debugging minimization over an isolated adapter
python statebisect_cli.py run-minimization --env-id sim-env-default --baseline-id baseline-001 --changes c1,c2,c3

# --- Verification & Reproduction Commands (Phase 8) ---
# 1. Dry-run preview of verification trial plan
python statebisect_cli.py preview-verification --env-id sim-env-default --baseline-id baseline-001 --changes c1,c2 --differential-baseline baseline-alt

# 2. Execute 6-stage verification protocol
python statebisect_cli.py run-verification --env-id sim-env-default --baseline-id baseline-001 --changes c1,c2 --repeats 2 --differential-baseline baseline-alt

# 3. Inspect verification audit record
python statebisect_cli.py inspect-verification <verification_run_id>
python statebisect_cli.py inspect-verification <verification_run_id> --format json

# 4. Export verification findings report
python statebisect_cli.py export-verification <verification_run_id> -o verification_report.json --format json

# --- Local Investigation API & Dashboard Commands (Phase 9) ---
# 1. Start local dashboard on default loopback (http://127.0.0.1:8080) and open browser
python statebisect_cli.py dashboard

# 2. Start on custom loopback port without opening browser
python statebisect_cli.py dashboard --port 8888 --no-browser

# 3. Use custom database path with dashboard
python statebisect_cli.py --db my_investigation.db dashboard

# --- AI Explanation Layer Commands (Phase 10) ---
# 1. Check AI explainer status & configured provider
python statebisect_cli.py ai-status

# 2. Preview redacted evidence context bundle before sending to remote model
python statebisect_cli.py ai-context-preview <investigation_id>
python statebisect_cli.py ai-context-preview <investigation_id> --format json

# 3. Generate grounded AI explanation (requires --confirm-remote for remote providers)
python statebisect_cli.py ai-explain <investigation_id> --provider mock
python statebisect_cli.py ai-explain <investigation_id> --provider openai --confirm-remote --question "Why did TLS handshake fail?"

# 4. Inspect or clear explanation audit history
python statebisect_cli.py ai-history <investigation_id>
python statebisect_cli.py ai-clear-history <investigation_id>

# --- Reproducible Benchmark Suite Commands (Phase 11) ---
# 1. List all 15 ground-truth benchmark scenarios
python statebisect_cli.py benchmark list
python statebisect_cli.py benchmark list --format json

# 2. Validate benchmark scenario definitions, schemas, seeds, and SHA-256 hash
python statebisect_cli.py benchmark validate

# 3. Run full benchmark suite in isolated simulation/fake environment
python statebisect_cli.py benchmark run
python statebisect_cli.py benchmark run --adapter simulation --format text
python statebisect_cli.py benchmark run --format json -o benchmark_report.json

# 4. Run benchmark against an explicit database and view stored report
python statebisect_cli.py benchmark run --db bench_runs.db
python statebisect_cli.py benchmark report <run_id> --db bench_runs.db
```

## 12. Running Tests and Demos

### Run Full Test Suite (192 Tests Across 18 Modules)
```powershell
python -m unittest discover -s tests -v
```

### Run AST Exception-Handling Audit (Zero return/break/continue in finally)
```powershell
python -m unittest tests.test_hardening.TestAstExceptionHandlingAudit
```

### Run Phase 1 Demonstration Script
```powershell
python demo_phase1.py
```

### Run Reproducible Benchmark Suite
```powershell
python statebisect_cli.py benchmark run
```

## 13. Phase Status

- [x] **Phase 0 — Repository review & product skeleton**: Completed.
- [x] **Phase 1 — Simulated experiment runner & Delta Debugging**: Completed.
- [x] **Phase 2 — Experiment definitions, CLI, and auditability**: Completed.
- [x] **Phase 3 — Failure health checks and evidence model**: Completed.
- [x] **Phase 4 — Windows read-only collector MVP**: Completed.
- [x] **Phase 5 — Targeted continuous collection & lightweight monitoring**: Completed.
- [x] **Phase 6 — Candidate change generation & correlation engine**: Completed.
- [x] **Phase 7 — Isolated trial runner & VM/Sandbox abstraction**: Completed.
- [x] **Phase 8 — Verification, reproduction, and differential validation**: Completed.
- [x] **Phase 9 — Investigation Dashboard & Visualization**: Completed.
- [x] **Phase 10 — AI Explanation Layer & Synthesis**: Completed.
- [x] **Phase 11 — Benchmarking, Release Hardening, & Distribution**: Completed (Release Candidate).
