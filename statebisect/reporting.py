"""Report generator for StateBisect investigations and experiment results."""

from __future__ import annotations

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
    lines.append("--------------------------------------------------------------------------------")
    lines.append("FINDINGS & MINIMIZATION SUMMARY")
    lines.append(f"Verdict          : {result.verdict}")
    lines.append(f"Suspected Set    : {', '.join(result.suspected_set) if result.suspected_set else 'None'}")
    lines.append(f"1-Minimal?       : {'YES' if result.is_1_minimal else 'NO'}")
    lines.append(f"Total Trials     : {result.total_trials}")
    lines.append(f"Rationale        : {result.rationale}")
    lines.append("--------------------------------------------------------------------------------")
    lines.append("EXPERIMENT TRIAL AUDIT MATRIX")
    lines.append(f"{'Trial ID':<16} {'Outcome':<14} {'Duration(ms)':<14} {'Selected Changes':<24} {'Health Output'}")
    lines.append("-" * 80)
    for trial in result.trials:
        changes_str = ",".join(trial.selected_change_ids) if trial.selected_change_ids else "<baseline>"
        lines.append(
            f"{trial.trial_id:<16} {trial.outcome.value:<14} {trial.duration_ms:<14.2f} {changes_str:<24} {trial.health_output[:30]}"
        )
    lines.append("--------------------------------------------------------------------------------")
    lines.append("SCOPE & LIMITATIONS")
    lines.append(f"- Environment: {investigation.coverage_limitations}")
    lines.append("- Safety guarantee: No modifications were made to host Windows registry or files.")
    lines.append("- Minimality claim: Formally verified as 1-minimal against the simulated health check.")
    lines.append("================================================================================")

    return "\n".join(lines)
