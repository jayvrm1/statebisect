"""Evidence-backed reporting generators for Phase 8 Verification."""

from __future__ import annotations

import json
from typing import Any

from .models import VerificationRunRecord


def format_verification_text(record: VerificationRunRecord) -> str:
    """Format a VerificationRunRecord as a human-readable diagnostic text report."""
    lines: list[str] = []

    lines.append("=" * 80)
    lines.append("                  STATEBISECT EVIDENCE-BACKED VERIFICATION REPORT")
    lines.append("          [CONTROLLED EXPERIMENT EVALUATION — NOT A GENERIC HEURISTIC]")
    lines.append("=" * 80)
    lines.append(f"Verification ID   : {record.verification_id}")
    lines.append(f"Investigation ID  : {record.investigation_id}")
    lines.append(f"Environment ID    : {record.environment_id}")
    lines.append(f"Baseline ID       : {record.baseline_id}")
    lines.append(f"Protocol Version  : {record.protocol_version}")
    lines.append(f"Started At (UTC)  : {record.created_at}")
    lines.append(f"Finished At (UTC) : {record.finished_at}")
    lines.append("-" * 80)
    lines.append("VERDICT & SUMMARY")
    lines.append(f"Verification Verdict       : {record.verdict.value}")
    lines.append(f"Baseline Control Healthy?  : {'YES' if record.baseline_control_passed else 'NO'}")
    lines.append(f"Candidate Set Reproduced?  : {'YES' if record.candidate_set_reproduced else 'NO'}")
    lines.append(f"Is 1-Minimal?              : {'YES' if record.is_1_minimal else 'NO'}")
    lines.append(f"Is Multi-Change Interaction: {'YES' if record.is_interaction else 'NO'}")
    if record.reversal_tested:
        lines.append(f"Reversal Check Passed?     : {'YES' if record.reversal_passed else 'NO'}")

    lines.append("-" * 80)
    lines.append("CONFIDENCE RATIONALE")
    lines.append(f"  {record.confidence_rationale}")

    if record.necessity_details:
        lines.append("-" * 80)
        lines.append("NECESSITY ANALYSIS (Omitting individual candidate members)")
        lines.append(f"{'Change ID':<20} {'Necessary?':<12} {'Outcome w/o Change':<20} {'Explanation'}")
        lines.append("-" * 80)
        for n in record.necessity_details:
            nec_str = "YES" if n.is_necessary else "NO"
            lines.append(f"{n.change_id:<20} {nec_str:<12} {n.outcome_without_change.value:<20} {n.explanation}")

    if record.differential_results:
        lines.append("-" * 80)
        lines.append("DIFFERENTIAL BASELINE COMPARISON")
        lines.append(f"{'Baseline ID':<20} {'Status':<16} {'Control':<10} {'Reproduced?':<14} {'Notes'}")
        lines.append("-" * 80)
        for base_id, res in record.differential_results.items():
            repro_str = "YES" if res.get("reproduced") else "NO"
            lines.append(
                f"{base_id:<20} {res.get('status', ''):<16} {res.get('control_outcome', ''):<10} "
                f"{repro_str:<14} {res.get('notes', '')}"
            )

    lines.append("-" * 80)
    lines.append("EXPERIMENTAL TRIAL AUDIT MATRIX")
    lines.append(
        f"{'Trial ID':<16} {'Role':<22} {'Changes':<16} {'Omitted':<12} {'Outcome':<10} {'Dur(ms)':<8} {'Cleanup'}"
    )
    lines.append("-" * 80)
    for t in record.trials:
        chgs_str = ",".join(t.target_change_ids) if t.target_change_ids else "<none>"
        if len(chgs_str) > 15:
            chgs_str = chgs_str[:12] + "..."
        omitted_str = t.omitted_change_id or "-"
        lines.append(
            f"{t.trial_id:<16} {t.trial_role.value:<22} {chgs_str:<16} {omitted_str:<12} "
            f"{t.outcome.value:<10} {t.duration_ms:<8.1f} {t.cleanup_status}"
        )

    lines.append("-" * 80)
    lines.append("SCOPE & LIMITATIONS")
    for lim in record.limitations:
        lines.append(f"  * {lim}")
    lines.append("=" * 80)

    return "\n".join(lines)


def format_verification_json(record: VerificationRunRecord) -> str:
    """Format a VerificationRunRecord as a formatted JSON document."""
    return json.dumps(record.to_dict(), indent=2)
