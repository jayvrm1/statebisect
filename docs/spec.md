# StateBisect Product Specification

## 1. Product Mission & Scope

StateBisect investigates:
> *"This Windows application or system function worked before. It now fails. Which observed configuration change, or combination of changes, can reproduce the failure?"*

StateBisect is a rigorous investigation and experiment system. It is **not** an antivirus, system cleaner, registry repair tool, automated Windows administrator, or generic troubleshooting chatbot.

### Scope & Boundaries
- **MVP Scope**: Deterministic experiment orchestration, isolated workspace simulation, dependency-aware Delta Debugging (`ddmin`), explicit tracking of `INCONCLUSIVE` outcomes, SQLite experiment persistence, and evidence-grounded reporting.
- **Safety Boundary**: 
  - Never touches or modifies the host's actual Windows system settings, services, drivers, or real registry.
  - Requires zero administrator privileges.
  - Experiments run within an isolated virtual state or user-configured disposable VM (in later phases).
  - Destructive host operations are strictly forbidden.
- **Out of Scope for MVP**:
  - Direct live host system modification or remediation.
  - Arbitrary remote code execution or uncontrolled shell commands.
  - Automatic driver uninstallation or boot configuration modification.
  - Universal Windows coverage or heuristic guesswork without reproducible proof.

## 2. Core Principles
1. **Evidence before explanation**: Every reported finding must be grounded in recorded experimental trials.
2. **Deterministic core**: Delta Debugging, trial execution, and minimality evaluation must operate predictably without depending on an LLM.
3. **Clean experiments**: Every trial starts from a known baseline and returns to baseline with explicit restoration verification.
4. **Explicit uncertainty**: Inconclusive telemetry, intermittent checks, or trial timeouts remain `INCONCLUSIVE` and are never coerced to pass or fail.
5. **Multi-change interactions**: Faults triggered only by pairwise or $k$-way combinations are treated as first-class minimal sets.
6. **1-Minimality**: A minimal failure-inducing set is formally 1-minimal (removing any single element causes the failure to vanish).
7. **Simulation transparency**: All simulated runs are prominently labeled as simulations.

## 3. Demonstration Scenarios & Pass/Fail Rules

### Demonstration Scenario 1: Single-Cause Isolation
- **Baseline**: Healthy simulated system configuration. Health check returns `PASS`.
- **Candidates**: 8 candidate changes ($C_1 \dots C_8$) applied in synthetic timeline.
- **Trigger**: $C_4$ introduces an incompatible configuration key/value.
- **Pass/Fail Rule**:
  - Health check verifies application functionality.
  - Baseline: `PASS`.
  - Full set: `FAIL`.
  - Minimal set identified: Exactly $\{C_4\}$.

### Demonstration Scenario 2: Two-Change Interaction
- **Baseline**: Healthy simulated system configuration (`PASS`).
- **Candidates**: 8 candidate changes ($C_1 \dots C_8$).
- **Trigger**: $C_3$ modifies a communication protocol flag, and $C_6$ disables legacy fallback. Neither alone breaks the health check, but both together cause `FAIL`.
- **Pass/Fail Rule**:
  - Baseline: `PASS`.
  - Single subsets $\{C_3\}$ and $\{C_6\}$: `PASS`.
  - Full set: `FAIL`.
  - Minimal set identified: Exactly $\{C_3, C_6\}$.

### Demonstration Scenario 3: Explicit Inconclusive Handling
- When a trial encounters missing telemetry, flaky check outputs, or unexpected execution faults, the outcome is recorded as `INCONCLUSIVE`.
- Delta Debugging must safely constrain or stop pruning, preventing false negatives or false attribution.

## 4. Phase Roadmap
- **Phase 0**: Repository review, specification, product skeleton, safety boundary.
- **Phase 1**: Deterministic simulated experiment runner, generalized `ddmin`, SQLite storage, and reporting.
- **Phase 2**: Declarative experiment schemas, CLI, migrations, and auditability.
- **Phase 3**: Pluggable health checks, repeatability verification, allowlists.
- **Phase 4**: Windows read-only collector MVP (Event logs, registry snapshots, inventory).
- **Phase 5**: Targeted continuous change collection & Sysmon integration.
- **Phase 6**: Investigation timeline & transparent candidate ranking.
- **Phase 7**: Disposable Windows VM adapter with snapshot rollback.
- **Phase 8**: Verification passes & evidence-backed findings.
- **Phase 9**: Local API & dashboard.
- **Phase 10**: AI explanation layer grounded in stored evidence.
- **Phase 11**: Benchmarking, hardening, and release readiness.

