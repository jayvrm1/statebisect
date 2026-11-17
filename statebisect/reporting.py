"""Report generator for StateBisect investigations and experiment results."""

from __future__ import annotations

import json
from typing import Any

from .models import Investigation, MinimizationResult


def generate_text_report(investigation: Investigation, result: MinimizationResult) -> str:
    """Generate a clean, structured diagnostic text report."""
    lines: list[str] = []

    lines.append("================================================================================")
    lines.append("                        STATEBISECT INVESTIGATION REPORT                        ")
    lines.append("            [SIMULATION ENVIRONMENT - NOT A LIVE WINDOWS DIAGNOSIS]            ")
    lines.append("================================================================================")
    lines.append(f"Investigation ID : {investigation.id}")
    lines.append(f"Target System    : {investigation.target_system}")
    lines.append(f"Reported Symptom : {investigation.symptom}")
    lines.append(f"Investigation At : {investigation.created_at}")
    lines.append(f"Baseline ID      : {investigation.baseline_id}")
    lines.append(f"Status           : {investigation.status.value}")
    if investigation.scenario_id:
        lines.append(f"Scenario ID      : {investigation.scenario_id}")
        lines.append(f"Scenario Hash    : {investigation.scenario_hash[:16]}... (SHA-256)")
        lines.append(f"Engine Version   : {investigation.engine_version}")
    lines.append("--------------------------------------------------------------------------------")
    lines.append("FINDINGS & MINIMIZATION SUMMARY")
    lines.append(f"Verdict          : {result.verdict}")
    lines.append(f"Suspected Set    : {', '.join(result.suspected_set) if result.suspected_set else 'None'}")
    lines.append(f"1-Minimal?       : {'YES' if result.is_1_minimal else 'NO'}")
    lines.append(f"Total Trials     : {result.total_trials}")
    lines.append(f"Rationale        : {result.rationale}")
    lines.append("--------------------------------------------------------------------------------")
    lines.append("EXPERIMENT TRIAL AUDIT MATRIX")
    lines.append(
        f"{'Trial ID':<16} {'Outcome':<14} {'Duration(ms)':<14} {'Atts':<6} {'Selected Changes':<20} {'Health Output'}"
    )
    lines.append("-" * 80)
    for trial in result.trials:
        changes_str = ",".join(trial.selected_change_ids) if trial.selected_change_ids else "<baseline>"
        atts_count = len(trial.attempts) if trial.attempts else 1
        lines.append(
            f"{trial.trial_id:<16} {trial.outcome.value:<14} {trial.duration_ms:<14.2f} {atts_count:<6} {changes_str:<20} {trial.health_output[:25]}"
        )
    lines.append("--------------------------------------------------------------------------------")
    lines.append("SCOPE & LIMITATIONS")
    lines.append(f"- Environment: {investigation.coverage_limitations}")
    lines.append("- Safety guarantee: No modifications were made to host Windows registry or files.")
    lines.append(
        "- Minimality guarantee: 1-minimal means removing any single change from the suspected set "
        "resolves the failure. It is not guaranteed to be the globally smallest set across all combinations."
    )
    lines.append("================================================================================")

    return "\n".join(lines)


def generate_json_report(investigation: Investigation, result: MinimizationResult) -> dict[str, Any]:
    """Generate structured JSON dictionary for export."""
    return {
        "report_version": "1.0.0",
        "simulation": True,
        "investigation": {
            "id": investigation.id,
            "target_system": investigation.target_system,
            "symptom": investigation.symptom,
            "status": investigation.status.value,
            "baseline_id": investigation.baseline_id,
            "scenario_id": investigation.scenario_id,
            "scenario_hash": investigation.scenario_hash,
            "engine_version": investigation.engine_version,
            "created_at": investigation.created_at,
            "updated_at": investigation.updated_at,
            "coverage_limitations": investigation.coverage_limitations,
        },
        "findings": {
            "verdict": result.verdict,
            "suspected_set": result.suspected_set,
            "is_1_minimal": result.is_1_minimal,
            "total_trials": result.total_trials,
            "rationale": result.rationale,
            "minimality_explanation": (
                "1-minimal guarantees removing any single component cures the failure; "
                "it does not guarantee absolute global minimum across all 2^N subsets."
            ),
        },
        "trials": [
            {
                "trial_id": t.trial_id,
                "selected_changes": t.selected_change_ids,
                "outcome": t.outcome.value,
                "health_output": t.health_output,
                "reset_status": t.reset_status,
                "duration_ms": t.duration_ms,
                "start_time": t.start_time,
                "finish_time": t.finish_time,
                "notes": t.notes,
                "check_type": t.check_type,
                "check_version": t.check_version,
                "parameters_hash": t.parameters_hash,
                "attempts": [
                    {
                        "attempt_number": att.attempt_number,
                        "outcome": att.outcome.value,
                        "start_time": att.start_time,
                        "finish_time": att.finish_time,
                        "duration_ms": att.duration_ms,
                        "exit_code": att.exit_code,
                        "expected_value": att.expected_value,
                        "observed_value": att.observed_value,
                        "bounded_evidence": att.bounded_evidence,
                        "error_reason": att.error_reason,
                        "is_truncated": att.is_truncated,
                        "is_redacted": att.is_redacted,
                    }
                    for att in t.attempts
                ],
            }
            for t in result.trials
        ],
    }
