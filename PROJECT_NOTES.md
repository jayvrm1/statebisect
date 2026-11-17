# StateBisect Project Notes

## Phase 0: Repository Review & Product Skeleton
- **Inspection Findings**:
  - Empty repository on Windows host.
  - Python 3.12.10 and Git 2.56 available.
  - Standard library `unittest`, `sqlite3`, `dataclasses`, and `enum` are fully functional and require no third-party package installations.
- **Completed**:
  - Authored `docs/spec.md` with product mission, MVP boundary, demonstration scenarios, pass/fail rules, and roadmap.
  - Authored `docs/architecture.md` outlining modular components and isolation boundaries.
  - Initialized clean project structure.

## Phase 1: Simulated Experiment Runner & Delta Debugging
- **Completed**:
  - `statebisect/models.py`: Immutable `Change` models, `HealthCheckOutcome` (`PASS`, `FAIL`, `INCONCLUSIVE`), `ExperimentTrial`, `Investigation`, and `MinimizationResult`.
  - `statebisect/simulation.py`: `SimulatedWorkspace` providing in-memory virtual state, baseline snapshotting, strict verification of baseline restoration, and Kahn's topological sort for dependency enforcement.
  - `statebisect/storage.py`: SQLite persistence for investigations and trial audit logs with transactions and foreign keys.
  - `statebisect/runner.py`: `ExperimentRunner` with unconditional `finally` cleanup to guarantee zero state leakage across trials.
  - `statebisect/delta_debugging.py`: Generalized `ddmin` supporting subsets, complements, multi-change interactions, caching, and explicit handling of `INCONCLUSIVE` health checks.
  - `statebisect/reporting.py`: Diagnostic text report generator with audit matrix and simulation labeling.
  - `demo_phase1.py`: Executable demonstration of single-cause isolation, two-change interaction isolation, and inconclusive trial handling.
  - `tests/`: 15 unit tests covering all required test cases.

## Test Results & Verification
- Unit test suite: 15 passing tests (`python -m unittest discover -s tests -v`).
- Demo run: Successfully executed single cause (`C4` isolated in 7 trials), pairwise interaction (`{C3, C6}` isolated in 18 trials), and inconclusive handling.

## Known Limitations
- Current execution is purely simulated in-memory; no real Windows Event Log, registry, or service telemetry is collected.
- No CLI parser yet (scenarios are defined programmatically in Python).
- Health checks are in-memory callable functions rather than declarative process or network probes.

## Next Phase: Phase 2 — Experiment Definitions, CLI, and Auditability
- **Entry Criteria**: Phase 1 acceptance criteria met (simulated runner working, generalized `ddmin` passing, tests verified).
- **Upcoming Deliverables**:
  - Declarative experiment scenario format (JSON/YAML).
  - Schema validation, timeouts, and operation allowlists.
  - CLI commands: `list-scenarios`, `run-scenario`, `inspect-investigation`, `export-report`.
  - SQLite schema versioning and migrations.

