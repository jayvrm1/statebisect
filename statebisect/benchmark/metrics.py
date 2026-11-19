"""Empirical benchmark metrics calculation with precise denominators and uncertainty."""

from __future__ import annotations

from typing import Sequence

from .models import (
    BenchmarkCategory,
    BenchmarkMetricsSummary,
    MetricFraction,
    ScenarioExecutionResult,
)


def make_fraction(num: int, denom: int, description: str = "") -> MetricFraction:
    """Safely calculate a percentage fraction with boundary protection."""
    pct = (num / denom * 100.0) if denom > 0 else 0.0
    return MetricFraction(
        numerator=num,
        denominator=denom,
        percentage=round(pct, 2),
        description=description,
    )


def calculate_benchmark_metrics(
    results: Sequence[ScenarioExecutionResult],
    ai_metrics: dict[str, tuple[int, int]] | None = None,
) -> BenchmarkMetricsSummary:
    """Aggregate raw scenario outcomes into comprehensive, audit-ready benchmark metrics."""
    total_scenarios = len(results)
    if total_scenarios == 0:
        empty_frac = make_fraction(0, 0)
        return BenchmarkMetricsSummary(
            total_scenarios=0,
            exact_set_accuracy=empty_frac,
            element_precision=empty_frac,
            element_recall=empty_frac,
            one_minimality_rate=empty_frac,
            interaction_recognition_rate=empty_frac,
            bystander_rejection_rate=empty_frac,
            false_attribution_rate=empty_frac,
            inconclusive_correctness_rate=empty_frac,
            baseline_integrity_rate=empty_frac,
            cleanup_success_rate=empty_frac,
            total_trials=0,
            total_duration_ms=0.0,
            average_trials_per_scenario=0.0,
            average_duration_ms_per_scenario=0.0,
            diagnostic_accuracy=empty_frac,
            diagnostic_coverage=empty_frac,
            normal_restore_success_rate=empty_frac,
            normal_cleanup_success_rate=empty_frac,
            fault_containment_rate=empty_frac,
        )

    # 1. Exact Set Accuracy: cases where reported set matches ground truth exactly
    exact_matches = sum(1 for r in results if r.exact_match)
    exact_set_acc = make_fraction(
        exact_matches,
        total_scenarios,
        description="Fraction of scenarios where the reported failure-inducing set exactly matches ground truth.",
    )

    # 1b. Conclusive Diagnostic Accuracy & Coverage
    causal_scenarios = [r for r in results if r.ground_truth_causes]
    conclusive_matches = sum(
        1
        for r in causal_scenarios
        if set(r.reported_causes) == set(r.ground_truth_causes) and r.verdict_observed == "VERIFIED_SET"
    )
    diagnostic_acc = make_fraction(
        conclusive_matches,
        len(causal_scenarios),
        description="Fraction of causal scenarios where reported failure-inducing set exactly matched ground truth.",
    )

    conclusive_findings = sum(1 for r in results if r.verdict_observed == "VERIFIED_SET")
    diagnostic_cov = make_fraction(
        conclusive_findings,
        total_scenarios,
        description="Fraction of benchmark scenarios that yielded a conclusive verified cause finding.",
    )

    # 2. Element-level Precision & Recall
    # Precision = sum(TP) / (sum(TP) + sum(FP)) across all scenarios with reported causes
    # Recall = sum(TP) / (sum(TP) + sum(FN)) across all scenarios with ground truth causes
    total_tp = 0
    total_fp = 0
    total_fn = 0
    for r in results:
        gt_set = set(r.ground_truth_causes)
        rep_set = set(r.reported_causes)
        tp = len(gt_set & rep_set)
        fp = len(rep_set - gt_set)
        fn = len(gt_set - rep_set)
        total_tp += tp
        total_fp += fp
        total_fn += fn

    element_prec = make_fraction(
        total_tp,
        total_tp + total_fp,
        description="Fraction of reported causal elements that are true ground-truth causes.",
    )
    element_rec = make_fraction(
        total_tp,
        total_tp + total_fn,
        description="Fraction of true ground-truth causal elements successfully detected.",
    )

    # 3. 1-Minimality Rate: for scenarios with verified causes, fraction that are 1-minimal
    causal_scenarios = [r for r in results if r.ground_truth_causes]
    minimal_count = sum(1 for r in causal_scenarios if r.is_1_minimal)
    minimality_rate = make_fraction(
        minimal_count,
        len(causal_scenarios),
        description="Fraction of causal findings verified to be 1-minimal under the test oracle.",
    )

    # 4. Interaction Recognition: multi-cause scenarios (len(causes) >= 2) correctly recognized as a set
    interaction_scenarios = [r for r in results if len(r.ground_truth_causes) >= 2]
    interaction_success = sum(1 for r in interaction_scenarios if r.exact_match)
    interaction_rate = make_fraction(
        interaction_success,
        len(interaction_scenarios),
        description="Fraction of multi-change interaction causes recognized as composite sets.",
    )

    # 5. Bystander Rejection: scenarios with bystanders where all bystanders were excluded
    bystander_scenarios = [
        r
        for r in results
        if r.category
        in (
            BenchmarkCategory.SINGLE_CAUSE_WITH_BYSTANDERS,
            BenchmarkCategory.BYSTANDER_WITH_DEPENDENCY,
            BenchmarkCategory.FALSE_TEMPORAL_CORRELATION,
            BenchmarkCategory.POST_ONSET_CONSEQUENCE,
        )
    ]
    bystander_success = sum(1 for r in bystander_scenarios if r.bystanders_rejected)
    bystander_rate = make_fraction(
        bystander_success,
        len(bystander_scenarios),
        description="Fraction of scenarios where non-causal bystander changes were correctly excluded.",
    )

    # 6. False Attribution: cases where unrelated or post-onset changes were wrongly reported as causes
    false_attributed = sum(
        1
        for r in results
        if any(c not in r.ground_truth_causes for c in r.reported_causes)
    )
    false_attr_rate = make_fraction(
        false_attributed,
        total_scenarios,
        description="Fraction of scenarios where non-causal changes were incorrectly presented as causes.",
    )

    # 7. Inconclusive Handling Correctness: cases expected to be inconclusive that were correctly marked
    inconclusive_expected = [
        r
        for r in results
        if r.category
        in (
            BenchmarkCategory.INTERMITTENT_HEALTH_CHECK,
            BenchmarkCategory.UNREPRODUCIBLE_FAILURE,
            BenchmarkCategory.MISSING_TELEMETRY_GAPS,
            BenchmarkCategory.FAILED_BASELINE_RESTORE,
            BenchmarkCategory.CLEANUP_FAILURE_CORRUPTION,
        )
    ]
    inconclusive_correct = sum(
        1
        for r in inconclusive_expected
        if r.verdict_observed in ("INCONCLUSIVE", "NO_REPRO", "FLAKY_ORACLE", "RESTORATION_FAILED", "CORRUPTED")
    )
    inconclusive_rate = make_fraction(
        inconclusive_correct,
        len(inconclusive_expected),
        description="Fraction of uncertain, flaky, or corrupted cases correctly identified as inconclusive.",
    )

    # 8. Baseline & Cleanup Integrity (Split into Routine vs Intentional Fault Injection)
    scenarios_without_restore_fault = [
        r for r in results if r.category != BenchmarkCategory.FAILED_BASELINE_RESTORE
    ]
    normal_restore_ok = sum(1 for r in scenarios_without_restore_fault if r.baseline_restored_cleanly)
    normal_restore_frac = make_fraction(
        normal_restore_ok,
        len(scenarios_without_restore_fault),
        description="Routine baseline restore reliability on uncorrupted executions (excluding intentional fault tests).",
    )

    scenarios_without_cleanup_fault = [
        r for r in results if r.category != BenchmarkCategory.CLEANUP_FAILURE_CORRUPTION
    ]
    normal_cleanup_ok = sum(1 for r in scenarios_without_cleanup_fault if r.cleanup_succeeded)
    normal_cleanup_frac = make_fraction(
        normal_cleanup_ok,
        len(scenarios_without_cleanup_fault),
        description="Routine post-trial cleanup reliability on uncorrupted executions (excluding intentional fault tests).",
    )

    fault_scenarios = [
        r
        for r in results
        if r.category
        in (
            BenchmarkCategory.FAILED_BASELINE_RESTORE,
            BenchmarkCategory.CLEANUP_FAILURE_CORRUPTION,
        )
    ]
    faults_contained = sum(
        1
        for r in fault_scenarios
        if r.verdict_observed in ("RESTORATION_FAILED", "CORRUPTED", "INCONCLUSIVE")
    )
    fault_containment_frac = make_fraction(
        faults_contained,
        len(fault_scenarios),
        description="Fraction of intentional lifecycle faults safely caught and contained by isolation guards.",
    )

    baseline_intact = sum(1 for r in results if r.baseline_restored_cleanly)
    cleanup_intact = sum(1 for r in results if r.cleanup_succeeded)
    baseline_rate = make_fraction(
        baseline_intact,
        total_scenarios,
        description="Overall baseline restore integrity rate across all scenarios (including 1 intentional restore fault).",
    )
    cleanup_rate = make_fraction(
        cleanup_intact,
        total_scenarios,
        description="Overall post-trial cleanup integrity rate across all scenarios (including 1 intentional cleanup fault).",
    )

    # 9. Trials & Duration Totals
    total_trials = sum(r.trials_count for r in results)
    total_duration_ms = sum(r.duration_ms for r in results)
    avg_trials = total_trials / total_scenarios if total_scenarios > 0 else 0.0
    avg_dur = total_duration_ms / total_scenarios if total_scenarios > 0 else 0.0

    # 10. AI Metrics (optional)
    ai_valid_cit = None
    ai_unsupp_claim = None
    ai_inj_resist = None
    if ai_metrics:
        if "citations" in ai_metrics:
            c_num, c_denom = ai_metrics["citations"]
            ai_valid_cit = make_fraction(c_num, c_denom, "Evidence citation validity rate.")
        if "unsupported_claims" in ai_metrics:
            u_num, u_denom = ai_metrics["unsupported_claims"]
            ai_unsupp_claim = make_fraction(u_num, u_denom, "Unsupported causal claim rate.")
        if "injection_resistance" in ai_metrics:
            i_num, i_denom = ai_metrics["injection_resistance"]
            ai_inj_resist = make_fraction(i_num, i_denom, "Prompt injection resistance rate.")

    return BenchmarkMetricsSummary(
        total_scenarios=total_scenarios,
        exact_set_accuracy=exact_set_acc,
        element_precision=element_prec,
        element_recall=element_rec,
        one_minimality_rate=minimality_rate,
        interaction_recognition_rate=interaction_rate,
        bystander_rejection_rate=bystander_rate,
        false_attribution_rate=false_attr_rate,
        inconclusive_correctness_rate=inconclusive_rate,
        baseline_integrity_rate=baseline_rate,
        cleanup_success_rate=cleanup_rate,
        total_trials=total_trials,
        total_duration_ms=round(total_duration_ms, 2),
        average_trials_per_scenario=round(avg_trials, 2),
        average_duration_ms_per_scenario=round(avg_dur, 2),
        diagnostic_accuracy=diagnostic_acc,
        diagnostic_coverage=diagnostic_cov,
        normal_restore_success_rate=normal_restore_frac,
        normal_cleanup_success_rate=normal_cleanup_frac,
        fault_containment_rate=fault_containment_frac,
        ai_valid_citation_rate=ai_valid_cit,
        ai_unsupported_claim_rate=ai_unsupp_claim,
        ai_injection_resistance_rate=ai_inj_resist,
    )
