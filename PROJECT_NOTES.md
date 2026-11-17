# StateBisect Project Notes

## Phase 0: Repository Review & Product Skeleton
- **Inspection Findings**:
  - Python 3.12.10 and Git 2.56 available on Windows.
  - Zero third-party dependencies required. Standard library (`unittest`, `sqlite3`, `dataclasses`, `enum`, `json`, `argparse`, `hashlib`) is fully sufficient.
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
  - `statebisect/scenarios.py`:
    - Declarative scenario schema v1.0.0.
    - Strict validation against forbidden executable properties (`command`, `powershell`, `script`, `eval`, `cmd`, etc.).
    - Allowlisted operations (`set_key`, `replace_value`, `delete_key`, `append_list`).
    - Allowlisted health-checks (`key_value_match`, `compound_condition`).
    - Dependency validation and Kahn's topological sort cycle rejection.
    - Safe path checking preventing directory traversal (`..`).
    - SHA-256 cryptographic hashing of scenario definitions.
    - Declarative-to-execution bridge (`execute_scenario`).
  - `scenarios/`:
    - `scenarios/single_cause.json`: 8 candidate changes, single root cause (`C4`).
    - `scenarios/interaction.json`: 8 candidate changes, 2-change interaction (`C3` + `C6`).
    - `scenarios/invalid_scenario.json`: Example demonstrating strict rejection of script/command properties.
  - `statebisect/cli.py` & `statebisect_cli.py`:
    - Full CLI with subcommands: `list-scenarios`, `validate-scenario`, `run-scenario`, `inspect-investigation`, `export-report`.
    - Proper exit codes (0 for success, 1 for error, 130 for cancellation, 2 for CLI arg error).
    - Signal handling for graceful user cancellation (SIGINT / Ctrl+C) ensuring baseline cleanup.
    - Exporting reports to text and JSON formats (`--format text|json`).
  - `statebisect/storage.py`:
    - Versioned schema migrations (`schema_migrations` table tracking versions 1 and 2).
    - Non-destructive migration of Phase 1 databases preserving existing investigations and trials.
    - Idempotent / repeat-safe migrations.
    - Structured audit trail table (`audit_logs`) tracking lifecycle events (`RUN_STARTED`, `TRIAL_COMPLETED`, `RUN_FINISHED`, `TRIAL_CANCELLED`, `CLEANUP_FAILURE`).
    - Snapshot persistence of scenario definitions and SHA-256 hashes for complete reproducibility.
  - `statebisect/reporting.py`:
    - Added structured JSON report export (`generate_json_report`).
    - Explicit explanation of 1-minimality vs. globally minimal set.
  - `tests/`: Added 25 new tests across `test_scenarios.py`, `test_cli.py`, `test_migration.py`, and `test_cancellation.py`. Total test count: 40 tests.

## Technology & Algorithm Decisions & Source Verification

1. **Algorithm: Delta Debugging (`ddmin`)**:
   - **Primary Source**: Andreas Zeller and Ralf Hildebrandt, *"Simplifying and Isolating Failure-Inducing Input"*, IEEE Transactions on Software Engineering, Vol. 28, No. 2, Feb 2002.
   - **Comparative Analysis**:
     - *Zeller (2002)* establishes the formal definition of 1-minimality: a failure-inducing input $c$ is 1-minimal if for all $x \in c$, $test(c \setminus \{x\}) \neq \text{FAIL}$. Zeller proves that finding a globally minimal subset across all $2^N$ combinations is NP-complete, whereas `ddmin` produces a 1-minimal set in polynomial time ($O(|c|^2)$ worst-case, $O(|c| \log |c|)$ best-case).
     - *GRIMM (`grimm-co/delta-debugging`)* implements file byte-reduction for fuzzing crashes in Python 3.
     - *StateBisect Adaptation*: StateBisect applies Delta Debugging to structured configuration changes rather than raw character streams. We extended classical `ddmin` to enforce topological dependency order, gracefully handle explicit `INCONCLUSIVE` health checks without guessing, cache intermediate trials in SQLite, and support thread-safe cancellation between trials.
2. **CLI Framework**:
   - **Choice**: Python standard library `argparse`.
   - **Tradeoff**: Avoided introducing external dependencies (such as Click or Typer) because `argparse` is built-in, stable, well-specified, and completely satisfies the required subcommands, options, and error handling.
3. **Database Migrations**:
   - **Choice**: Custom lightweight migration runner with `schema_migrations` tracking table in `storage.py`.
   - **Tradeoff**: Avoided heavyweight external tools (like Alembic) which require SQLAlchemy dependencies. SQLite's transactional `ALTER TABLE` and table inspection provide zero-dependency, crash-safe, and idempotent migrations.
4. **Data Format**:
   - **Choice**: JSON (`json` module).
   - **Tradeoff**: Pure data parsing only; eliminates the arbitrary code execution vulnerabilities associated with YAML (`yaml.load`) or Python code execution.

## Verification Results
- All 40 unit and integration tests passing (`python -m unittest discover -s tests -v`).
- Phase 1 programmatic demonstration script runs and passes (`python demo_phase1.py`).
- Declarative CLI commands (`list-scenarios`, `validate-scenario`, `run-scenario`, `inspect-investigation`, `export-report`) manually verified and automated in integration tests.

## Known Limitations
- StateBisect is currently a pure simulation environment; host Windows registry, files, and services are not touched.
- Health checks are evaluated against simulated state conditions; live Windows process execution and HTTP probe checks will be introduced in Phase 3.

## Next Phase: Phase 3 — Health Checks and Evidence Model
- **Entry Criteria**: Phase 2 acceptance criteria met (declarative scenarios, schema validation, CLI, auditability, migrations, cancellation, 40 tests passing).
- **Upcoming Deliverables**:
  - Pluggable health check interface (process exit codes, output pattern matching, local endpoint response).
  - Strict allowlists for health-check commands (no arbitrary shell execution).
  - Health-check timeouts and repeat policies.
  - Distinguishing stable pass/fail from intermittent results.
  - Evidence retention and linking.
