# StateBisect

> **Notice: Simulation Prototype**  
> StateBisect is currently in Phase 1 prototype state. All experiments run within an isolated virtual state and execute simulated health checks. **No modifications are made to the host Windows registry, files, services, or system configuration.**

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
- **1-Minimality**: Guarantees that removing any element from the failure-inducing set resolves the failure.
- **Safety by default**: Runs with zero host administrator privileges and zero host modifications.

## 3. Project Structure

```
statebisect/
├── docs/
│   ├── spec.md              # Product specification & MVP boundaries
│   └── architecture.md      # Modular architecture overview
├── statebisect/
│   ├── __init__.py          # Public package interfaces
│   ├── models.py            # Dataclasses & Enums (Change, Trial, Investigation)
│   ├── simulation.py        # Isolated workspace & dependency resolution
│   ├── delta_debugging.py   # Generalized ddmin algorithm
│   ├── runner.py            # Experiment runner with baseline cleanup
│   ├── storage.py           # SQLite persistence layer
│   └── reporting.py         # Diagnostic text report generator
├── tests/
│   ├── test_simulation.py   # Workspace, baseline, and dependency tests
│   ├── test_storage.py      # SQLite storage and query tests
│   ├── test_runner.py       # Trial runner and cleanup tests
│   └── test_delta_debugging.py # ddmin single, interaction, and inconclusive tests
├── demo_phase1.py           # Runnable Phase 1 demonstration script
├── PROJECT_NOTES.md         # Implementation logs and phase status
└── README.md
```

## 4. Running the Tests and Demonstration

### Prerequisites
- Python 3.10+ (Standard library only; zero external dependencies required for Phase 1).

### Run Test Suite
```bash
python -m unittest discover -s tests -v
```

### Run Demonstration
```bash
python demo_phase1.py
```

The demonstration executes three scenarios:
1. **Single-Cause Isolation**: Evaluates 8 synthetic changes and isolates root cause `C4` in 7 trials.
2. **Two-Change Interaction**: Evaluates 8 synthetic changes and isolates the pair `{C3, C6}` required to reproduce the fault.
3. **Explicit Inconclusive Handling**: Safely halts when telemetry or health checks return `INCONCLUSIVE`.

## 5. Phase Status

- [x] **Phase 0 — Repository review & product skeleton**: Completed.
- [x] **Phase 1 — Simulated experiment runner & Delta Debugging**: Completed.
- [ ] **Phase 2 — Experiment definitions, CLI, and auditability**: Next phase.

