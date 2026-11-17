# StateBisect Architecture

## System Overview

```
+-----------------------------------------------------------+
|                     StateBisect Core                      |
|                                                           |
|  +--------------------+         +----------------------+  |
|  | Delta Debugging    |         | Experiment Runner    |  |
|  | (ddmin engine)     |<------->| (Reset -> Apply ->   |  |
|  +--------------------+         |  Check -> Restore)   |  |
|            |                    +----------------------+  |
|            v                                |             |
|  +--------------------+                     v             |
|  | SQLite Storage     |         +----------------------+  |
|  | (Investigations &  |         | Isolated Workspace   |  |
|  |  Trials Audit)     |         | (Simulation Adapter) |  |
|  +--------------------+         +----------------------+  |
|            |                                              |
|            v                                              |
|  +--------------------+                                   |
|  | Report Generator   |                                   |
|  | (Markdown/Text)    |                                   |
|  +--------------------+                                   |
+-----------------------------------------------------------+
```

## Architectural Components

1. **Core Engine (`statebisect.delta_debugging`, `statebisect.runner`)**:
   - Implements Zeller's generalized Delta Debugging algorithm (`ddmin`), extended to handle multi-change interactions and explicit `INCONCLUSIVE` health checks.
   - Enforces dependency closure and topological application ordering.
   - Manages strict `try ... finally` baseline restoration so state never leaks between trials.

2. **Isolated Workspace / Simulation Adapter (`statebisect.simulation`)**:
   - Manages a virtualized environment state (isolated key-value registry and filesystem mock).
   - Guarantees zero host mutation.
   - Validates that restoration to baseline succeeded before any test begins.

3. **Persistence Layer (`statebisect.storage`)**:
   - SQLite backed store for durable investigation tracking, trial logging, outcomes, durations, and output artifacts.

4. **Reporting Layer (`statebisect.reporting`)**:
   - Produces structured diagnostic reports documenting the suspected 1-minimal set, trial matrix, baseline integrity checks, and limitations.

5. **Future Components (Phases 4-10)**:
   - Windows Collector (read-only system and event log telemetry).
   - Disposable VM Adapter (Hyper-V snapshot restoration).
   - Local API & Web Dashboard.
   - Evidence-grounded AI explanation assistant.
