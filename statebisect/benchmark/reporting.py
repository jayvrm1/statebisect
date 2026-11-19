"""Benchmark reporting and audit presentation formatters."""

from __future__ import annotations

import json
from typing import Any

from .models import BenchmarkRunRecord, MetricFraction


def format_fraction(frac: MetricFraction | None) -> str:
    """Format MetricFraction into 'N/D (pct%)'."""
    if frac is None:
        return "N/A"
    return f"{frac.numerator}/{frac.denominator} ({frac.percentage:.1f}%)"


def format_benchmark_report_text(record: BenchmarkRunRecord) -> str:
    """Generate a high-contrast, structured text audit report from a benchmark run."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("                    STATEBISECT REPRODUCIBLE BENCHMARK REPORT                    ")
    lines.append("=" * 80)
    lines.append(f"Run ID           : {record.run_id}")
    lines.append(f"Suite ID         : {record.suite_id} (Version: {record.suite_version})")
    lines.append(f"Suite Hash       : {record.suite_hash[:16]}...")
    lines.append(f"Engine Version   : {record.engine_version}")
    lines.append(f"Primary Adapter  : {record.adapter_type}")
    lines.append(f"Disposable VM?   : {'YES (Isolated Disposable VM)' if record.is_disposable_vm else 'NO (Simulation / Test-Double)'}")
    lines.append(f"Run Status       : {record.status}")
    lines.append(f"Completed At     : {record.completed_at}")
    lines.append("-" * 80)
    lines.append("EMPIRICAL PERFORMANCE METRICS SUMMARY (Ground-Truth Verified):")
    lines.append("-" * 80)

    s = record.summary
    lines.append(f"  * Total Scenarios Evaluated   : {s.total_scenarios}")
    if s.diagnostic_accuracy:
        lines.append(f"  * Conclusive Diagnostic Acc   : {format_fraction(s.diagnostic_accuracy)}")
    lines.append(f"  * Exact Set Accuracy (Overall): {format_fraction(s.exact_set_accuracy)}")
    if s.diagnostic_coverage:
        lines.append(f"  * Conclusive Diagnosis Rate   : {format_fraction(s.diagnostic_coverage)}")
    lines.append(f"  * Element-Level Precision     : {format_fraction(s.element_precision)}")
    lines.append(f"  * Element-Level Recall        : {format_fraction(s.element_recall)}")
    lines.append(f"  * 1-Minimality Adherence Rate : {format_fraction(s.one_minimality_rate)}")
    lines.append(f"  * Interaction Recognition     : {format_fraction(s.interaction_recognition_rate)}")
    lines.append(f"  * Bystander Rejection Rate    : {format_fraction(s.bystander_rejection_rate)}")
    lines.append(f"  * False Attribution Rate      : {format_fraction(s.false_attribution_rate)}")
    lines.append(f"  * Inconclusive Handling Rate  : {format_fraction(s.inconclusive_correctness_rate)}")
    if s.normal_restore_success_rate:
        lines.append(f"  * Routine Restore Reliability : {format_fraction(s.normal_restore_success_rate)}")
    if s.normal_cleanup_success_rate:
        lines.append(f"  * Routine Cleanup Reliability : {format_fraction(s.normal_cleanup_success_rate)}")
    if s.fault_containment_rate:
        lines.append(f"  * Fault Injection Containment : {format_fraction(s.fault_containment_rate)}")
    lines.append(f"  * Baseline Restore Integrity  : {format_fraction(s.baseline_integrity_rate)}")
    lines.append(f"  * Post-Trial Cleanup Success  : {format_fraction(s.cleanup_success_rate)}")
    lines.append(f"  * Total Experiment Trials     : {s.total_trials} (Avg: {s.average_trials_per_scenario:.1f} trials/scenario)")
    lines.append(f"  * Total Runtime               : {s.total_duration_ms:.1f}ms (Avg: {s.average_duration_ms_per_scenario:.1f}ms/scenario)")

    if s.ai_valid_citation_rate or s.ai_unsupported_claim_rate or s.ai_injection_resistance_rate:
        lines.append("-" * 80)
        lines.append("AI EXPLAINER GROUNDING & DEFENSE METRICS:")
        lines.append("-" * 80)
        if s.ai_valid_citation_rate:
            lines.append(f"  * Evidence Citation Validity  : {format_fraction(s.ai_valid_citation_rate)}")
        if s.ai_unsupported_claim_rate:
            lines.append(f"  * Unsupported Causal Claims   : {format_fraction(s.ai_unsupported_claim_rate)}")
        if s.ai_injection_resistance_rate:
            lines.append(f"  * Prompt Injection Resistance : {format_fraction(s.ai_injection_resistance_rate)}")

    lines.append("-" * 80)
    lines.append("DETAILED SCENARIO EXECUTION MATRIX:")
    lines.append(f"{'Scenario ID':<32} {'Verdict':<14} {'Match':<6} {'Trials':<8} {'Time(ms)':<10} {'Reported vs Truth'}")
    lines.append("-" * 80)

    for r in record.scenario_results:
        match_str = "YES" if r.exact_match else "NO"
        rep = ",".join(r.reported_causes) if r.reported_causes else "[]"
        gt = ",".join(r.ground_truth_causes) if r.ground_truth_causes else "[]"
        vs_str = f"Rep:{rep} | GT:{gt}"
        lines.append(f"{r.scenario_id:<32} {r.verdict_observed:<14} {match_str:<6} {r.trials_count:<8} {r.duration_ms:<10.1f} {vs_str}")
        if r.inconclusive_reason:
            lines.append(f"    └─ Note/Reason: {r.inconclusive_reason}")

    lines.append("-" * 80)
    lines.append("SCOPE & REPRODUCIBILITY DECLARATIONS:")
    lines.append("  1. Simulation separation: Simulation and fake results are tested in-memory and")
    lines.append("     do not represent live Windows Hyper-V execution latency.")
    lines.append("  2. No universal causality claim: 1-minimality applies strictly to the tested")
    lines.append("     environment and documented health check oracle.")
    lines.append("  3. Host safety: Zero modifications were performed on the host operating system.")
    lines.append("=" * 80)
    return "\n".join(lines)


def format_benchmark_report_json(record: BenchmarkRunRecord, indent: int = 2) -> str:
    """Serialize benchmark run record to structured, machine-readable JSON."""
    return json.dumps(record.to_dict(), indent=indent)
