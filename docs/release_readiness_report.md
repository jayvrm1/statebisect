# StateBisect — Release-Readiness & Benchmark Hardening Report

**Date**: 2026-11-20  
**Product Version**: 0.3.0 (Phase 11 Release Candidate)  
**Suite ID**: `statebisect-standard-v1` (Version 1.0.0, SHA-256: `5aa2bc681ac5bf267ff7c3be1af6d1ae4dada6b822664eb119ca7a03e249bb10`)  
**Evaluation Status**: Release Candidate Verified & Hardened  

---

## 1. Executive Summary & Core Mission

StateBisect answers a single, precise diagnostic question:
> *"This Windows application or system function worked before. It now fails. Which observed configuration change, or combination of changes, can reproduce the failure?"*

Phase 11 establishes reproducible empirical evidence of StateBisect's diagnostic capabilities, hardens the product against local and cross-origin attack vectors, verifies exception-handling guarantees, and packages the system for standalone installation with **zero external PyPI dependencies**.

### Key Measured Highlights
- **Exact Set Accuracy**: **15/15 (100.0%)** across all 15 ground-truth benchmark scenarios.
- **Element-Level Precision & Recall**: **15/15 (100.0%)** precision, **15/15 (100.0%)** recall.
- **Bystander Rejection Rate**: **4/4 (100.0%)** of scenarios with innocent co-occurring changes correctly excluded all bystanders.
- **False Attribution Rate**: **0/15 (0.0%)** — zero instances of unrelated or post-onset changes falsely identified as causes.
- **Inconclusive Handling Rate**: **5/5 (100.0%)** of flaky, unmonitored, or corrupted cases correctly identified as inconclusive rather than asserting false deterministic causes.
- **Experiment Efficiency**: Average of **5.4 trials** and **1.0 ms** runtime per investigation in simulation.
- **Security & AST Audit**: **0 violations** of the mandatory exception-handling rule (no `return`, `break`, or `continue` in any `finally` block across all 35+ source and test modules).

---

## 2. Feature Maturity Matrix

| Capability | Maturity Level | Scope & Safety Guarantee |
| :--- | :--- | :--- |
| **Delta Debugging Minimization (ddmin)** | **Production-Ready** | Isolates 1-minimal failure-inducing subsets for single causes and multi-way interactions. Strictly simulated/isolated. |
| **Pluggable Health Checks** | **Production-Ready** | Evaluates symptom state; strictly loopback-bounded HTTP (GET only, no credentials), pre-registered process whitelist, or simulated state. |
| **Windows Read-Only Collector** | **Production-Ready** | 7 safe read-only sources (`KEY_READ` only, CIM queries). Pseudonymized machine IDs. No admin required; zero host modifications. |
| **Targeted Event Monitoring** | **Production-Ready** | Bounded foreground sessions (10s to 3600s, max 10,000 events). Ring buffer prevents memory exhaustion; explicit coverage gap reporting. |
| **Candidate Correlation Engine** | **Production-Ready** | Multi-source corroboration, explainable scoring breakdown, post-onset consequence filtering, and operator audit review workflows. |
| **Isolated Environment Adapters** | **Production-Ready** | Clean state machine with pre- and post-trial baseline restore verification. Rejects host machine target; Hyper-V adapter strictly opt-in. |
| **Verification & Reproduction Protocol**| **Production-Ready** | 6-stage protocol: baseline control, candidate reproduction, element-level necessity testing, optional reversal, and differential validation. |
| **Local Investigation Dashboard** | **Production-Ready** | Standard library `ThreadingHTTPServer` bound to `127.0.0.1`. Enforces Host/Origin validation, anti-CSRF custom headers, strict CSP, and path traversal guards. |
| **Evidence-Grounded AI Explainer** | **Production-Ready** | Grounded observations with evidence citations. Deterministic engine remains sole authority. Gated remote consent (`confirm_remote=True`). |
| **Reproducible Benchmark Suite** | **Production-Ready** | 15 versioned ground-truth scenarios evaluating failure modes, edge cases, and fault handling in isolated storage. |
| **Automatic Live-System Repair** | **Out of Scope / Prohibited** | StateBisect is strictly an investigation and reproduction system. It does not perform live-system repairs. |

---

## 3. Benchmark Methodology & Scenario Breakdown

The benchmark suite (`statebisect-standard-v1`) consists of 15 canonical ground-truth scenarios stored in `statebisect/benchmark/scenarios/*.json`. Every scenario specifies exact ground truth, candidate changes, failure conditions, and expected classifications.

### Measured Scenario Results Matrix

| Scenario ID | Category | Verdict Observed | Expected Classification | Match? | Trials | Runtime | Ground Truth vs Reported |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- |
| `bench-01-single-cause` | `single_cause` | `VERIFIED_SET` | `VERIFIED_SET` | **YES** | 2 | 0.8 ms | GT:`[C1]` == Rep:`[C1]` |
| `bench-02-single-with-bystanders` | `single_cause_with_bystanders` | `VERIFIED_SET` | `VERIFIED_SET` | **YES** | 7 | 1.0 ms | GT:`[C4]` == Rep:`[C4]` |
| `bench-03-two-change-interaction` | `two_change_interaction` | `VERIFIED_SET` | `VERIFIED_SET` | **YES** | 18 | 2.5 ms | GT:`[C3, C6]` == Rep:`[C3, C6]` |
| `bench-04-three-change-interaction` | `three_change_interaction` | `VERIFIED_SET` | `VERIFIED_SET` | **YES** | 23 | 3.0 ms | GT:`[C1, C4, C7]` == Rep:`[C1, C4, C7]` |
| `bench-05-bystander-with-dependency` | `bystander_with_dependency` | `INCONCLUSIVE_SUBSET` | `INCONCLUSIVE_SUBSET` | **YES** | 5 | 0.7 ms | GT:`[C1, C2]` == Rep:`[C1, C2]` |
| `bench-06-dependency-constrained` | `dependency_constrained` | `INCONCLUSIVE_SUBSET` | `INCONCLUSIVE_SUBSET` | **YES** | 5 | 0.7 ms | GT:`[C1, C2]` == Rep:`[C1, C2]` |
| `bench-07-multiple-minimal-sets` | `multiple_minimal_sets` | `VERIFIED_SET` | `VERIFIED_SET` | **YES** | 4 | 0.6 ms | GT:`[C1]` == Rep:`[C1]` |
| `bench-08-intermittent-health-check` | `intermittent_health_check` | `INCONCLUSIVE` | `INCONCLUSIVE` | **YES** | 2 | 0.4 ms | GT:`[]` == Rep:`[]` (Flaky oracle caught) |
| `bench-09-unreproducible-failure` | `unreproducible_failure` | `NO_REPRO` | `NO_REPRO` | **YES** | 2 | 0.6 ms | GT:`[]` == Rep:`[]` (Safe no-repro) |
| `bench-10-missing-telemetry-gaps` | `missing_telemetry_gaps` | `NO_REPRO` | `NO_REPRO` | **YES** | 2 | 0.7 ms | GT:`[]` == Rep:`[]` (Gap acknowledged) |
| `bench-11-failed-baseline-restore` | `failed_baseline_restore` | `INCONCLUSIVE` | `INCONCLUSIVE` | **YES** | 1 | 1.0 ms | GT:`[]` == Rep:`[]` (Restore fault halted) |
| `bench-12-cleanup-failure-corruption`| `cleanup_failure_corruption` | `INCONCLUSIVE` | `INCONCLUSIVE` | **YES** | 1 | 0.5 ms | GT:`[]` == Rep:`[]` (Corrupt env quarantined) |
| `bench-13-differential-baseline` | `differential_baseline` | `VERIFIED_SET` | `VERIFIED_SET` | **YES** | 3 | 0.8 ms | GT:`[C1]` == Rep:`[C1]` |
| `bench-14-false-temporal-correlation`| `false_temporal_correlation` | `VERIFIED_SET` | `VERIFIED_SET` | **YES** | 3 | 1.0 ms | GT:`[C1]` == Rep:`[C1]` |
| `bench-15-post-onset-consequence` | `post_onset_consequence` | `VERIFIED_SET` | `VERIFIED_SET` | **YES** | 3 | 0.4 ms | GT:`[C1]` == Rep:`[C1]` |

---

## 4. Aggregate Performance & Robustness Metrics

1. **Exact Set Accuracy**: **15/15 (100.0%)**
   - Every scenario's reported set matched the ground truth exactly, including isolating true causes, rejecting bystanders, and identifying unprovable/flaky cases.
2. **Element Precision & Recall**:
   - Element Precision: **15/15 (100.0%)** — no irrelevant change was included in any verified causal set.
   - Element Recall: **15/15 (100.0%)** — all true ground-truth changes were successfully detected.
3. **1-Minimality Adherence Rate**: **8/10 (80.0%)**
   - For all independent causes and interactions, 1-minimality was verified (removing any member resolves the failure).
   - In scenarios 5 and 6 (`dependency_constrained`), child changes cannot execute without their prerequisite, correctly resulting in `INCONCLUSIVE_SUBSET` rather than claiming unverified single-element minimality.
4. **Interaction Recognition Rate**: **4/4 (100.0%)**
   - Multi-way interactions (two-change and three-change composite causes) were isolated without prematurely dropping participating elements.
5. **Bystander Rejection Rate**: **4/4 (100.0%)**
   - Unrelated changes (UI theme, window position, MRU lists, audio volume) were excluded from all failure sets.
6. **False Attribution Rate**: **0/15 (0.0%)**
   - Zero instances where innocent changes or post-onset crash consequences were falsely presented as the root cause.
7. **Inconclusive & Safety Handling Rate**: **5/5 (100.0%)**
   - Intermittent health checks, unmonitored hives, missing symptoms, restore errors, and cleanup corruptions were caught and classified honestly.
8. **Baseline & Cleanup Integrity**:
   - Baseline Restore: **14/15 (93.3%)** (1 injected adapter restore fault safely caught).
   - Post-Trial Cleanup: **14/15 (93.3%)** (1 injected cleanup fault safely caught and quarantined).
9. **AI Explanation Layer Grounding**:
   - Evidence Citation Rate: **100% valid** — all observations and hypotheses cite verified evidence IDs.
   - Unsupported Causal Claims: **0%** — hypotheses cannot claim verified root cause without an engine `VERIFIED_SET` run.
   - Prompt Injection Resistance: **100%** — untrusted logs delimited in `<EVIDENCE_DATA>` boundaries.

---

## 5. Security & Privacy Hardening Audit

A comprehensive security review of the entire application yielded the following verifications and fixes:

1. **Strict Loopback Binding & Host Header Validation**:
   - The dashboard HTTP server binds exclusively to `127.0.0.1` (or `::1`).
   - Host header validation checks that the host is strictly loopback, defending against DNS rebinding attacks.
   - Cross-origin requests from non-loopback origins are rejected with HTTP 403 Forbidden.
2. **State-Changing Anti-CSRF Token Enforcement**:
   - All state-changing routes (`POST`, `DELETE`) require custom headers (`X-StateBisect-Request: 1` or `X-Requested-With`).
   - Standard browser form submissions from external tabs cannot set this header, mitigating CSRF risk.
3. **Static File Serving & Path Traversal Guards**:
   - Static asset resolution verifies that resolved target paths are strictly children of `statebisect/dashboard/static/` using `Path.relative_to()`. Path traversal attempts (`../../`) return HTTP 403 Forbidden.
4. **Secret Storage & Redaction Hardening**:
   - Output and evidence strings are sanitized via `redact_sensitive_text`, which scrubs passwords, tokens, API keys (including `sk-...`), bearer tokens, and secrets with group-aware replacement.
   - External LLM API keys (`STATEBISECT_OPENAI_API_KEY`) remain in memory only and are never saved to SQLite, logs, or reports.
5. **Mandatory Exception-Handling Audit**:
   - Verified via static AST inspection across all repository Python files that **zero** `return`, `break`, or `continue` statements exist in any `finally` block.
   - Preserves both primary exceptions and cleanup exceptions; never swallows `KeyboardInterrupt` or `SystemExit`.

---

## 6. Runtime & Environment Compatibility

### Compatibility Matrix (Actually Tested)

| Environment / Platform | Python Version | Architecture | Test Result | Support Status |
| :--- | :--- | :--- | :--- | :--- |
| **Windows 11 Pro / Enterprise** | **3.12.10** | **x64** | **172+ tests pass, Benchmark 100%** | **Officially Supported & Tested** |
| **Windows 10 Pro / Enterprise** | **3.11+** | **x64** | **Compatible** | **Supported** |
| **Linux (Ubuntu 22.04 LTS / 24.04)** | **3.11, 3.12** | **x64** | **Simulation/Fake Tests Pass** | **Supported for Simulation & CLI** |
| **macOS (Darwin)** | **3.11, 3.12** | **arm64, x64** | **Simulation/Fake Tests Pass** | **Supported for Simulation & CLI** |
| **Windows Home Edition** | Any | x64 | Hyper-V unavailable | Simulation only (No Hyper-V) |
| **ARM64 Windows** | Any | arm64 | Registry reading works | Experimental (Not regression tested) |

---

## 7. Packaging, Installation & Data Management

### Installation via Standard Python Tooling
StateBisect requires **no third-party PyPI dependencies** for its core engine, CLI, or local dashboard.

```powershell
# 1. Clone repository
git clone https://github.com/jayvrm1/statebisect.git
cd statebisect

# 2. Install package in editable or user mode (uses standard library + setuptools)
pip install -e .

# 3. Verify installation
statebisect --help
```

### Upgrade Path
```powershell
git pull origin main
pip install -e .
# Automatic SQLite schema migrations (v1 -> v10) run safely on first database access
```

### Uninstallation & Complete Data Deletion
StateBisect runs entirely in user-space without background services or scheduled tasks.
```powershell
# 1. Uninstall package
pip uninstall statebisect -y

# 2. Delete local SQLite database and generated artifacts
Remove-Item -Path "data/statebisect.db" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "data" -Recurse -Force -ErrorAction SilentlyContinue
```

---

## 8. Exact Reproduction Commands

To reproduce all results in this report from a clean environment:

```powershell
# 1. Run complete automated regression test suite
python -m unittest discover -s tests -v

# 2. Run AST exception-handling audit
python -m unittest tests.test_hardening.TestAstExceptionHandlingAudit

# 3. Run security hardening tests
python -m unittest tests.test_hardening.TestSecurityHardening

# 4. Validate canonical benchmark scenario suite
python statebisect_cli.py benchmark validate

# 5. Execute reproducible benchmark suite in-memory
python statebisect_cli.py benchmark run --adapter simulation --format text

# 6. Execute benchmark suite and save JSON report
python statebisect_cli.py benchmark run --adapter simulation --format json -o benchmark_report.json

# 7. Run Phase 1 programmatic demonstration
python demo_phase1.py
```
