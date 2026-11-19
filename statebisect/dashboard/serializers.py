"""Serialization utilities with secret redaction and safety bounds for StateBisect API."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..adapters.models import TrialLifecycleState
from ..health_checks.base import redact_sensitive_text
from ..models import HealthCheckOutcome
from ..verification.models import TrialRole


def safe_redact(text: Any, max_len: int = 4096) -> str:
    """Redact secrets and bound string length safely."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    redacted, _ = redact_sensitive_text(text)
    if len(redacted) > max_len:
        return redacted[:max_len] + f"... [truncated {len(redacted) - max_len} chars]"
    return redacted


def sanitize_value(val: Any, max_string_len: int = 4096) -> Any:
    """Recursively sanitize, redact, and bound values for API output."""
    if val is None:
        return None
    if isinstance(val, (bool, int, float)):
        return val
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, str):
        return safe_redact(val, max_string_len)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, dict):
        return {str(k): sanitize_value(v, max_string_len) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [sanitize_value(item, max_string_len) for item in val]
    if dataclasses.is_dataclass(val):
        return sanitize_value(dataclasses.asdict(val), max_string_len)
    if hasattr(val, "to_dict") and callable(getattr(val, "to_dict")):
        return sanitize_value(val.to_dict(), max_string_len)
    return str(val)


def serialize_investigation(inv: Any) -> dict[str, Any]:
    """Serialize an Investigation record with safe bounds."""
    if not inv:
        return {}
    return {
        "id": inv.id,
        "symptom": safe_redact(inv.symptom),
        "target_system": safe_redact(inv.target_system),
        "status": inv.status.value if hasattr(inv.status, "value") else str(inv.status),
        "baseline_id": inv.baseline_id,
        "created_at": inv.created_at,
        "updated_at": inv.updated_at,
        "coverage_limitations": sanitize_value(inv.coverage_limitations),
        "scenario_id": getattr(inv, "scenario_id", ""),
        "scenario_hash": getattr(inv, "scenario_hash", ""),
        "onset_time": getattr(inv, "onset_time_utc", "") or getattr(inv, "onset_time", "") or "",
        "health_check_def": sanitize_value(getattr(inv, "health_check_def", {})),
    }


def serialize_candidate(c: Any) -> dict[str, Any]:
    """Serialize a CandidateChange record with score breakdown."""
    if not c:
        return {}
    score_breakdown = c.score_breakdown
    breakdown_dict = {
        "evidence_type_score": getattr(score_breakdown, "evidence_type_score", 0.0),
        "temporal_proximity_score": getattr(score_breakdown, "proximity_score", getattr(score_breakdown, "temporal_proximity_score", 0.0)),
        "target_relevance_score": getattr(score_breakdown, "target_relevance_score", 0.0),
        "process_provenance_score": getattr(score_breakdown, "provenance_score", getattr(score_breakdown, "process_provenance_score", 0.0)),
        "corroboration_score": getattr(score_breakdown, "corroboration_score", 0.0),
        "coverage_gap_penalty": getattr(score_breakdown, "gap_penalty", getattr(score_breakdown, "coverage_gap_penalty", 0.0)),
        "total_score": getattr(score_breakdown, "total_score", 0.0),
        "rationale": getattr(score_breakdown, "rationale", ""),
    } if score_breakdown else {}

    cand_id = getattr(c, "candidate_id", getattr(c, "id", ""))
    return {
        "id": cand_id,
        "investigation_id": c.investigation_id,
        "source_type": getattr(c, "source_type", ", ".join(getattr(c, "source_ids", [])) or "collector"),
        "component_name": getattr(c, "display_name", getattr(c, "component_name", "")),
        "change_key": getattr(c, "object_key", getattr(c, "change_key", "")),
        "category": c.category,
        "action_type": getattr(c, "action_type", "modify"),
        "evidence_type": c.evidence_type.value if hasattr(c.evidence_type, "value") else str(c.evidence_type),
        "observed_timestamp": getattr(c, "first_observed_utc", getattr(c, "observed_timestamp", "")),
        "confidence": getattr(c, "confidence", "CONFIRMED") if not hasattr(getattr(c, "confidence", None), "value") else c.confidence.value,
        "process_provenance": c.process_provenance.value if hasattr(c.process_provenance, "value") else str(c.process_provenance),
        "process_name": c.process_name,
        "process_id": c.process_id,
        "reversibility": c.reversibility.value if hasattr(c.reversibility, "value") else str(c.reversibility),
        "review_status": c.review_status.value if hasattr(c.review_status, "value") else str(c.review_status),
        "is_active": c.is_active,
        "score": c.score,
        "rank": c.rank,
        "score_breakdown": breakdown_dict,
        "before_value": sanitize_value(c.before_value),
        "after_value": sanitize_value(c.after_value),
        "supporting_evidence": sanitize_value(getattr(c, "supporting_evidence", getattr(c, "evidence_ids", []))),
        "conflicting_evidence": sanitize_value(getattr(c, "conflicting_evidence", [])),
    }


def serialize_isolated_trial(t: Any) -> dict[str, Any]:
    """Serialize an IsolatedTrialRecord."""
    if not t:
        return {}
    selected_changes = getattr(t, "selected_change_ids", getattr(t, "selected_changes", []))
    outcome = getattr(t, "outcome", getattr(t, "health_outcome", HealthCheckOutcome.INCONCLUSIVE))
    health_output = getattr(t, "output_bounded", getattr(t, "health_output", ""))
    lifecycle = getattr(t, "lifecycle_state", getattr(t, "state", TrialLifecycleState.COMPLETED))
    return {
        "trial_id": t.trial_id,
        "environment_id": t.environment_id,
        "baseline_id": t.baseline_id,
        "selected_changes": selected_changes,
        "start_time": t.start_time,
        "finish_time": t.finish_time,
        "state": lifecycle.value if hasattr(lifecycle, "value") else str(lifecycle),
        "health_outcome": outcome.value if hasattr(outcome, "value") else str(outcome),
        "health_output": safe_redact(health_output or ""),
        "duration_ms": t.duration_ms,
        "attempt_count": getattr(t, "attempt_count", 1),
        "attempts": sanitize_value(getattr(t, "attempts", [])),
        "evidence_records": sanitize_value(getattr(t, "evidence_data", getattr(t, "evidence_records", {}))),
        "restoration_duration_ms": getattr(t, "restoration_duration_ms", 0.0),
        "cleanup_duration_ms": getattr(t, "cleanup_duration_ms", 0.0),
        "is_baseline_verified": getattr(t, "restore_status", "") == "VERIFIED" or getattr(t, "is_baseline_verified", True),
        "is_cleanup_verified": getattr(t, "cleanup_status", "") == "VERIFIED" or getattr(t, "is_cleanup_verified", True),
        "error_message": safe_redact(getattr(t, "error_reason", getattr(t, "error_message", "")) or ""),
        "metadata": sanitize_value(getattr(t, "metadata", {})),
    }


def serialize_verification_run(v: Any) -> dict[str, Any]:
    """Serialize a VerificationRunRecord."""
    if not v:
        return {}
    v_id = getattr(v, "verification_id", getattr(v, "run_id", ""))
    rationale = getattr(v, "confidence_rationale", getattr(v, "rationale", ""))
    candidates = getattr(v, "candidate_ids", getattr(v, "candidate_changes", []))
    start_time = getattr(v, "created_at", getattr(v, "start_time", ""))
    finish_time = getattr(v, "finished_at", getattr(v, "finish_time", ""))
    reversal_ok = getattr(v, "reversal_passed", getattr(v, "reversal_verified", None))
    trials_list = getattr(v, "trials", [])
    nec_list = getattr(v, "necessity_details", [])
    diff_results = getattr(v, "differential_results", {})
    diff_baseline = diff_results.get("baseline_id") if isinstance(diff_results, dict) else getattr(v, "differential_baseline_id", None)
    diff_verdict = diff_results.get("verdict") if isinstance(diff_results, dict) else getattr(v, "differential_verdict", None)

    return {
        "run_id": v_id,
        "investigation_id": v.investigation_id,
        "environment_id": v.environment_id,
        "baseline_id": v.baseline_id,
        "verdict": v.verdict.value if hasattr(v.verdict, "value") else str(v.verdict),
        "rationale": rationale,
        "candidate_changes": candidates,
        "start_time": start_time,
        "finish_time": finish_time,
        "total_trials": len(trials_list),
        "differential_baseline_id": diff_baseline,
        "differential_verdict": diff_verdict.value if hasattr(diff_verdict, "value") else (str(diff_verdict) if diff_verdict else None),
        "reversal_verified": reversal_ok,
        "trials": [
            {
                "trial_id": t.trial_id,
                "role": (getattr(t, "trial_role", None) or getattr(t, "role")).value if hasattr(getattr(t, "trial_role", None) or getattr(t, "role", None), "value") else str(getattr(t, "trial_role", None) or getattr(t, "role", "")),
                "target_baseline_id": getattr(t, "baseline_id", getattr(t, "target_baseline_id", "")),
                "changes_tested": getattr(t, "target_change_ids", getattr(t, "changes_tested", [])),
                "attempt_count": getattr(t, "attempt_number", getattr(t, "attempt_count", 1)),
                "final_outcome": (getattr(t, "outcome", None) or getattr(t, "final_outcome")).value if hasattr(getattr(t, "outcome", None) or getattr(t, "final_outcome", None), "value") else str(getattr(t, "outcome", None) or getattr(t, "final_outcome", "")),
                "is_reversal": (getattr(t, "trial_role", None) or getattr(t, "role")) == TrialRole.REVERSAL_CHECK,
                "duration_ms": t.duration_ms,
                "notes": getattr(t, "output_bounded", getattr(t, "notes", "")),
            }
            for t in trials_list
        ],
        "necessity_details": [
            {
                "candidate_id": getattr(n, "change_id", getattr(n, "candidate_id", "")),
                "omitted_subset": getattr(n, "omitted_subset", []),
                "is_necessary": n.is_necessary,
                "trial_outcome": (getattr(n, "outcome_without_change", None) or getattr(n, "trial_outcome")).value if hasattr(getattr(n, "outcome_without_change", None) or getattr(n, "trial_outcome", None), "value") else str(getattr(n, "outcome_without_change", None) or getattr(n, "trial_outcome", "")),
                "rationale": getattr(n, "explanation", getattr(n, "rationale", "")),
            }
            for n in nec_list
        ],
        "scope_limitations": getattr(v, "limitations", getattr(v, "scope_limitations", [])),
        "metadata": sanitize_value(getattr(v, "metadata", {})),
    }
