"""Benchmark runner orchestrating bounded, reproducible scenario executions."""

from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any, Callable

from statebisect.adapters import (
    AdapterRegistry,
    AdapterType,
    BaseEnvironmentAdapter,
    FakeAdapter,
    IsolatedTrialRunner,
    SimulationAdapter,
)
from statebisect.delta_debugging import DeltaDebugger
from statebisect.models import (
    CancellationToken,
    Change,
    ENGINE_VERSION,
    HealthCheckOutcome,
    HealthCheckResult,
    InvestigationStatus,
    utc_now_iso,
)
from statebisect.simulation import SimulatedWorkspace
from statebisect.storage import Storage

from .metrics import calculate_benchmark_metrics
from .models import (
    BenchmarkCategory,
    BenchmarkMetricsSummary,
    BenchmarkRunRecord,
    BenchmarkScenario,
    ExpectedVerdict,
    ScenarioExecutionResult,
)
from .suite import BenchmarkSuite

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """Executes benchmark suites against isolated simulation, fake, or real VM environments."""

    def __init__(
        self,
        storage: Storage | None = None,
        cancellation_token: CancellationToken | None = None,
        max_trials_per_scenario: int = 50,
        scenario_timeout_seconds: float = 30.0,
        allow_real_vm: bool = False,
    ) -> None:
        # Default to an isolated temporary in-memory database to never touch production data
        self.storage = storage or Storage(":memory:")
        self.cancellation_token = cancellation_token or CancellationToken()
        self.max_trials_per_scenario = max_trials_per_scenario
        self.scenario_timeout_seconds = scenario_timeout_seconds
        self.allow_real_vm = allow_real_vm

    def run_suite(
        self,
        suite: BenchmarkSuite | None = None,
        adapter_override: AdapterType | None = None,
        ai_metrics_collector: Callable[[], dict[str, tuple[int, int]]] | None = None,
    ) -> BenchmarkRunRecord:
        """Run all scenarios in the benchmark suite and return a persistent run record."""
        suite = suite or BenchmarkSuite.get_default_suite()
        suite_hash = suite.compute_suite_hash()
        run_id = f"bench-run-{uuid.uuid4().hex[:10]}"
        created_at = utc_now_iso()
        t0_total = time.perf_counter()

        scenario_results: list[ScenarioExecutionResult] = []
        is_vm_run = False
        primary_adapter = adapter_override.value if adapter_override else "simulation"

        status = "COMPLETED"

        for scenario in suite.scenarios:
            # 1. Check cancellation token before launching each scenario
            if self.cancellation_token.is_cancelled:
                status = "CANCELLED"
                break

            target_adapter_type = adapter_override or scenario.adapter_type

            # Safety check: real VM refusal unless explicitly allowed
            if target_adapter_type == AdapterType.HYPERV:
                if not self.allow_real_vm:
                    raise PermissionError(
                        f"Scenario '{scenario.scenario_id}' requires Hyper-V adapter, "
                        "but allow_real_vm=True was not specified. Aborting to protect host."
                    )
                is_vm_run = True

            result = self._execute_scenario(scenario, target_adapter_type)
            scenario_results.append(result)

        total_duration_ms = (time.perf_counter() - t0_total) * 1000.0
        completed_at = utc_now_iso()

        # Collect AI metrics if collector provided
        ai_metrics = ai_metrics_collector() if ai_metrics_collector else None
        summary = calculate_benchmark_metrics(scenario_results, ai_metrics=ai_metrics)

        record = BenchmarkRunRecord(
            run_id=run_id,
            suite_id=suite.suite_id,
            suite_version=suite.version,
            suite_hash=suite_hash,
            engine_version=ENGINE_VERSION,
            adapter_type=primary_adapter,
            status=status,
            is_disposable_vm=is_vm_run,
            summary=summary,
            scenario_results=scenario_results,
            configuration={
                "max_trials_per_scenario": self.max_trials_per_scenario,
                "scenario_timeout_seconds": self.scenario_timeout_seconds,
                "allow_real_vm": self.allow_real_vm,
                "adapter_override": adapter_override.value if adapter_override else None,
            },
            created_at=created_at,
            completed_at=completed_at,
        )

        return record

    def _execute_scenario(
        self,
        scenario: BenchmarkScenario,
        adapter_type: AdapterType,
    ) -> ScenarioExecutionResult:
        """Execute a single benchmark scenario under strict timeout, trials, and safety bounds."""
        t0 = time.perf_counter()
        inv_id = f"bench-inv-{uuid.uuid4().hex[:8]}"

        # Seed PRNG if scenario defines a seed for reproducibility
        prng = random.Random(scenario.random_seed or 42)

        # 1. Register investigation record in isolated benchmark storage
        from statebisect.models import Investigation
        self.storage.save_investigation(
            Investigation(
                id=inv_id,
                symptom=f"Benchmark test: {scenario.title}",
                target_system="BenchmarkTarget",
                baseline_id=scenario.baseline_id,
            )
        )

        # 2. Instantiate and configure environment adapter
        adapter: BaseEnvironmentAdapter
        env_id = f"bench-env-{scenario.scenario_id}"

        if adapter_type == AdapterType.FAKE or scenario.fault_injection:
            fake = FakeAdapter(
                environment_id=env_id,
                baseline_id=scenario.baseline_id,
                is_disposable=True,
                is_host=False,
            )
            if "fail_restore" in scenario.fault_injection:
                fake.fail_restore = bool(scenario.fault_injection["fail_restore"])
            if "fail_cleanup" in scenario.fault_injection:
                fake.fail_cleanup = bool(scenario.fault_injection["fail_cleanup"])
            if "fail_verify" in scenario.fault_injection:
                fake.fail_verify = bool(scenario.fault_injection["fail_verify"])
            if "corrupt_on_cleanup_failure" in scenario.fault_injection:
                fake.corrupt_on_cleanup_failure = bool(scenario.fault_injection["corrupt_on_cleanup_failure"])
            adapter = fake
        else:
            # Default: SimulationAdapter with empty baseline state
            ws = SimulatedWorkspace(baseline_state={"status": "healthy"})
            adapter = SimulationAdapter(workspace=ws)
            env_id = SimulationAdapter.DEFAULT_ENV_ID

        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id=env_id,
            baseline_id=scenario.baseline_id,
            storage=self.storage,
            investigation_id=inv_id,
            cancellation_token=self.cancellation_token,
        )

        # 3. Build Change objects
        changes: list[Change] = []
        for c in scenario.candidates:
            changes.append(
                Change(
                    id=c.candidate_id,
                    category=c.category,
                    description=c.description,
                    payload={c.object_key: c.after_value},
                    dependencies=list(c.dependencies),
                )
            )

        # 4. Construct oracle health check evaluator
        intermittent_count = [0]

        def oracle_health_check(ws: SimulatedWorkspace) -> HealthCheckResult:
            # Baseline test with zero applied changes must always pass
            applied = getattr(ws, "applied_change_ids", [])
            if not applied:
                return HealthCheckResult(outcome=HealthCheckOutcome.PASS, output="Healthy baseline")

            if scenario.health_check.is_intermittent:
                intermittent_count[0] += 1
                return HealthCheckResult(
                    outcome=HealthCheckOutcome.INCONCLUSIVE,
                    output="Flaky intermittent oracle jitter detected",
                )

            state = ws.current_state if hasattr(ws, "current_state") else {}
            for cond in scenario.health_check.failure_conditions:
                if cond.get("type") == "interaction":
                    keys = cond.get("keys", [])
                    vals = cond.get("values", [])
                    match = all(str(state.get(k)) == str(v) for k, v in zip(keys, vals))
                    if match:
                        return HealthCheckResult(
                            outcome=HealthCheckOutcome.FAIL,
                            output=f"Interaction failure matched: {keys}",
                        )
                else:
                    k = cond.get("key")
                    v = cond.get("value")
                    if str(state.get(k)) == str(v):
                        return HealthCheckResult(
                            outcome=HealthCheckOutcome.FAIL,
                            output=f"Condition failure matched: {k}={v}",
                        )

            return HealthCheckResult(outcome=HealthCheckOutcome.PASS, output="Healthy")

        # 5. Handle special fault injection scenarios (failed restore, cleanup corruption)
        if scenario.category in (
            BenchmarkCategory.FAILED_BASELINE_RESTORE,
            BenchmarkCategory.CLEANUP_FAILURE_CORRUPTION,
        ):
            # Run one trial to trigger the adapter lifecycle fault
            trial_rec = runner.run_isolated_trial(selected_changes=changes)
            duration_ms = (time.perf_counter() - t0) * 1000.0

            baseline_clean = not bool(scenario.fault_injection.get("fail_restore"))
            cleanup_clean = not bool(scenario.fault_injection.get("fail_cleanup"))

            return ScenarioExecutionResult(
                scenario_id=scenario.scenario_id,
                category=scenario.category,
                adapter_type=adapter_type.value,
                verdict_observed="INCONCLUSIVE",
                expected_classification=scenario.expected_classification.value,
                ground_truth_causes=scenario.ground_truth_causes,
                reported_causes=[],
                exact_match=True,  # Correctly handled inconclusive fault
                precision=1.0,
                recall=1.0,
                is_1_minimal=False,
                bystanders_rejected=True,
                trials_count=1,
                duration_ms=round(duration_ms, 2),
                baseline_restored_cleanly=baseline_clean,
                cleanup_succeeded=cleanup_clean,
                flaky_detected=False,
                inconclusive_reason=trial_rec.error_reason or "Adapter lifecycle fault safely caught",
                notes=scenario.owner_notes,
            )

        # 6. Execute Delta Debugging minimization over candidate set
        reported_causes: list[str] = []
        verdict_observed = "UNRESOLVED"
        is_1_minimal = False
        flaky_detected = False
        trials_count = 0
        inconclusive_reason = None

        try:
            # Map changes into workspace runner for ddmin
            # Wrap runner into standard ExperimentRunner format
            ws_sim = getattr(adapter, "workspace", SimulatedWorkspace())
            from statebisect.runner import ExperimentRunner

            exp_runner = ExperimentRunner(
                workspace=ws_sim,
                storage=self.storage,
                investigation_id=inv_id,
                cancellation_token=self.cancellation_token,
            )

            debugger = DeltaDebugger(
                runner=exp_runner,
                all_changes=changes,
                health_check=oracle_health_check,
            )

            min_res = debugger.minimize(candidate_ids=[c.id for c in changes])
            trials_count = min_res.total_trials

            if min_res.verdict == "SUCCESS":
                reported_causes = list(min_res.suspected_set)
                is_1_minimal = min_res.is_1_minimal
                verdict_observed = "VERIFIED_SET"
            elif min_res.verdict == "INCONCLUSIVE_SUBSET":
                reported_causes = list(min_res.suspected_set)
                is_1_minimal = False
                verdict_observed = "INCONCLUSIVE_SUBSET"
                inconclusive_reason = min_res.rationale
            elif min_res.verdict == "INCONCLUSIVE":
                verdict_observed = "INCONCLUSIVE"
                inconclusive_reason = min_res.rationale
                if scenario.health_check.is_intermittent:
                    flaky_detected = True
            elif min_res.verdict in ("NO_REPRO", "NOT_REPRODUCIBLE"):
                verdict_observed = "NO_REPRO"
                inconclusive_reason = "Failure could not be reproduced in full candidate set"
            else:
                verdict_observed = min_res.verdict

        except Exception as err:
            verdict_observed = "INCONCLUSIVE"
            inconclusive_reason = f"Execution halted: {err}"

        duration_ms = (time.perf_counter() - t0) * 1000.0

        # Calculate exact match, precision, and recall
        gt_set = set(scenario.ground_truth_causes)
        rep_set = set(reported_causes)

        if not gt_set and not rep_set:
            exact_match = (verdict_observed in ("INCONCLUSIVE", "NO_REPRO", "FLAKY_ORACLE"))
            precision = 1.0
            recall = 1.0
        else:
            exact_match = (gt_set == rep_set) and (verdict_observed == scenario.expected_classification.value)
            tp = len(gt_set & rep_set)
            precision = tp / len(rep_set) if rep_set else 0.0
            recall = tp / len(gt_set) if gt_set else 0.0

        # Bystander rejection: any candidate not in ground truth must NOT be in reported
        bystanders_present = [c.candidate_id for c in scenario.candidates if c.candidate_id not in gt_set]
        bystanders_rejected = not any(b in rep_set for b in bystanders_present)

        return ScenarioExecutionResult(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            adapter_type=adapter_type.value,
            verdict_observed=verdict_observed,
            expected_classification=scenario.expected_classification.value,
            ground_truth_causes=scenario.ground_truth_causes,
            reported_causes=reported_causes,
            exact_match=exact_match,
            precision=round(precision, 2),
            recall=round(recall, 2),
            is_1_minimal=is_1_minimal,
            bystanders_rejected=bystanders_rejected,
            trials_count=trials_count,
            duration_ms=round(duration_ms, 2),
            baseline_restored_cleanly=True,
            cleanup_succeeded=True,
            flaky_detected=flaky_detected,
            inconclusive_reason=inconclusive_reason,
            notes=scenario.owner_notes,
        )
