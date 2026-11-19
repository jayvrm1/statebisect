"""Unified investigation timeline generator for StateBisect dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ..models import HealthCheckOutcome
from ..storage import Storage
from ..verification.models import TrialRole
from .serializers import sanitize_value


def parse_iso_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp string safely into UTC datetime."""
    if not ts_str:
        return None
    try:
        cleaned = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class TimelineBuilder:
    """Constructs a unified, chronological, provenance-aware investigation timeline."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def build_timeline(
        self,
        investigation_id: str,
        source_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
        outcome_filter: Optional[str] = None,
        time_start: Optional[str] = None,
        time_end: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Aggregate all timeline items for an investigation."""
        inv = self.storage.get_investigation(investigation_id)
        if not inv:
            return {"total": 0, "items": [], "limit": limit, "offset": offset, "coverage_gaps": []}

        onset_str = getattr(inv, "onset_time_utc", None) or getattr(inv, "onset_time", None)
        onset_dt = parse_iso_ts(onset_str)
        start_bound = parse_iso_ts(time_start)
        end_bound = parse_iso_ts(time_end)

        items: list[dict[str, Any]] = []
        coverage_gaps: list[dict[str, Any]] = []

        # 1. Symptom onset / failure report
        if onset_str:
            items.append({
                "id": f"symptom-{inv.id}",
                "timestamp": onset_str,
                "event_type": "SYMPTOM_ONSET",
                "source": "investigation",
                "source_provenance": "USER_REPORT",
                "category": "symptom",
                "title": f"Reported Symptom: {inv.target_system}",
                "description": inv.symptom,
                "is_post_onset": False,
                "outcome": "FAIL",
                "evidence_type": "REPORTED_FAILURE",
                "is_inferred": False,
                "metadata": {
                    "target_system": inv.target_system,
                    "baseline_id": inv.baseline_id,
                },
            })

        # 2. Investigation creation
        items.append({
            "id": f"created-{inv.id}",
            "timestamp": inv.created_at,
            "event_type": "INVESTIGATION_CREATED",
            "source": "investigation",
            "source_provenance": "SYSTEM",
            "category": "investigation",
            "title": f"Investigation Initialized: {inv.id}",
            "description": f"Target: {inv.target_system}, Baseline: {inv.baseline_id}",
            "is_post_onset": bool(onset_dt and parse_iso_ts(inv.created_at) and parse_iso_ts(inv.created_at) > onset_dt),
            "outcome": None,
            "evidence_type": "RECORD",
            "is_inferred": False,
            "metadata": {},
        })

        # 3. Snapshot Diffs (Collector)
        # Check all collection runs for snapshot diffs
        collection_runs = self.storage.list_collection_runs()[:50]
        for c_run in collection_runs:
            diffs = self.storage.get_snapshot_diffs(c_run.run_id)
            for d in diffs:
                d_ts = d.observed_timestamp or c_run.start_time
                d_dt = parse_iso_ts(d_ts)
                items.append({
                    "id": f"diff-{d.diff_id}",
                    "timestamp": d_ts,
                    "event_type": "SNAPSHOT_DIFF",
                    "source": "collector",
                    "source_provenance": f"SNAPSHOT_RUN:{c_run.run_id}",
                    "category": d.category,
                    "title": f"Configuration Diff: {d.item_key} ({d.change_type.value if hasattr(d.change_type, 'value') else str(d.change_type)})",
                    "description": f"Category: {d.category}, Component: {d.component_name}",
                    "is_post_onset": bool(onset_dt and d_dt and d_dt > onset_dt),
                    "outcome": None,
                    "evidence_type": "SNAPSHOT_DIFF",
                    "is_inferred": False,
                    "metadata": {
                        "change_type": d.change_type.value if hasattr(d.change_type, "value") else str(d.change_type),
                        "before_value": sanitize_value(d.before_value),
                        "after_value": sanitize_value(d.after_value),
                    },
                })
            # Add coverage gaps from collection runs
            if hasattr(c_run, "coverage_gaps"):
                for gap in c_run.coverage_gaps:
                    coverage_gaps.append({
                        "source": "collector",
                        "category": gap.category,
                        "reason": gap.reason,
                        "details": gap.details,
                    })

        # 4. Monitoring Events
        # Check monitoring sessions
        sessions = self.storage.list_monitoring_sessions()[:20]
        for sess in sessions:
            m_events = self.storage.get_monitoring_events(sess.session_id, limit=200)
            for m in m_events:
                m_dt = parse_iso_ts(m.timestamp)
                prov_str = m.process_provenance.value if hasattr(m.process_provenance, "value") else str(m.process_provenance)
                items.append({
                    "id": f"monitor-{m.event_id}",
                    "timestamp": m.timestamp,
                    "event_type": "MONITORING_EVENT",
                    "source": "monitor",
                    "source_provenance": f"{m.source_name}:{prov_str}",
                    "category": m.category,
                    "title": f"Telemetry Event: {m.source_name} (Event ID: {m.native_event_id or 'N/A'})",
                    "description": f"Process: {m.process_name or 'N/A'} (PID: {m.process_id or 'N/A'})",
                    "is_post_onset": bool(onset_dt and m_dt and m_dt > onset_dt),
                    "outcome": None,
                    "evidence_type": "TELEMETRY_LOG",
                    "is_inferred": False,
                    "metadata": {
                        "process_name": m.process_name,
                        "process_id": m.process_id,
                        "confidence": m.confidence.value if hasattr(m.confidence, "value") else str(m.confidence),
                        "payload": sanitize_value(m.payload),
                    },
                })
            if hasattr(sess, "coverage_gaps"):
                for gap in sess.coverage_gaps:
                    coverage_gaps.append({
                        "source": "monitor",
                        "category": gap.source_name,
                        "reason": gap.reason,
                        "details": gap.details,
                    })

        # 5. Candidate Changes & Reviews
        candidates = self.storage.get_candidates(investigation_id)
        for cand in candidates:
            cand_id = getattr(cand, "candidate_id", getattr(cand, "id", ""))
            observed_ts = getattr(cand, "first_observed_utc", getattr(cand, "observed_timestamp", ""))
            comp_name = getattr(cand, "display_name", getattr(cand, "component_name", ""))
            src_prov = getattr(cand, "source_type", ", ".join(getattr(cand, "source_ids", [])) or "correlation")
            act_type = getattr(cand, "action_type", "modify")
            c_dt = parse_iso_ts(observed_ts)
            items.append({
                "id": f"cand-{cand_id}",
                "timestamp": observed_ts,
                "event_type": "CANDIDATE_GENERATED",
                "source": "correlation",
                "source_provenance": src_prov,
                "category": cand.category,
                "title": f"Candidate Change: {comp_name} (Rank #{cand.rank})",
                "description": f"Score: {cand.score:.1f}/100. Action: {act_type}. Review: {cand.review_status.value if hasattr(cand.review_status, 'value') else str(cand.review_status)}",
                "is_post_onset": bool(onset_dt and c_dt and c_dt > onset_dt),
                "outcome": None,
                "evidence_type": cand.evidence_type.value if hasattr(cand.evidence_type, "value") else str(cand.evidence_type),
                "is_inferred": True,  # Correlation is inferred ranking, not raw observation
                "metadata": {
                    "candidate_id": cand_id,
                    "rank": cand.rank,
                    "score": cand.score,
                    "is_active": cand.is_active,
                    "review_status": cand.review_status.value if hasattr(cand.review_status, "value") else str(cand.review_status),
                },
            })

            # Check reviews for this candidate
            reviews = self.storage.get_candidate_reviews(cand_id)
            for r in reviews:
                r_dt = parse_iso_ts(r.reviewed_at)
                items.append({
                    "id": f"review-{r.review_id}",
                    "timestamp": r.reviewed_at,
                    "event_type": "CANDIDATE_REVIEW",
                    "source": "user_review",
                    "source_provenance": f"USER:{r.reviewed_by}",
                    "category": "review",
                    "title": f"Candidate Review: {cand.component_name} -> {r.status.value if hasattr(r.status, 'value') else str(r.status)}",
                    "description": f"Reason: {r.reason}",
                    "is_post_onset": bool(onset_dt and r_dt and r_dt > onset_dt),
                    "outcome": None,
                    "evidence_type": "AUDIT_REVIEW",
                    "is_inferred": False,
                    "metadata": {
                        "candidate_id": cand.id,
                        "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                        "reviewer": r.reviewed_by,
                        "reason": r.reason,
                    },
                })

        # 6. Isolated Trials
        trials = self.storage.get_isolated_trials(investigation_id=investigation_id, limit=200)
        for t in trials:
            t_dt = parse_iso_ts(t.start_time)
            raw_outcome = getattr(t, "outcome", getattr(t, "health_outcome", HealthCheckOutcome.INCONCLUSIVE))
            outcome_str = raw_outcome.value if hasattr(raw_outcome, "value") else str(raw_outcome)
            selected_chgs = getattr(t, "selected_change_ids", getattr(t, "selected_changes", []))
            is_restored = getattr(t, "restore_status", "") == "VERIFIED" or getattr(t, "is_baseline_verified", True)
            is_cleaned = getattr(t, "cleanup_status", "") == "VERIFIED" or getattr(t, "is_cleanup_verified", True)
            items.append({
                "id": f"trial-{t.trial_id}",
                "timestamp": t.start_time,
                "event_type": "EXPERIMENT_TRIAL",
                "source": "trial_runner",
                "source_provenance": f"ENV:{t.environment_id}:BASELINE:{t.baseline_id}",
                "category": "experiment",
                "title": f"Trial {t.trial_id[:8]}: {outcome_str}",
                "description": f"Changes: {', '.join(selected_chgs) or '<baseline>'}. Duration: {t.duration_ms:.1f}ms. Restore verified: {is_restored}",
                "is_post_onset": bool(onset_dt and t_dt and t_dt > onset_dt),
                "outcome": outcome_str,
                "evidence_type": "ISOLATED_TRIAL",
                "is_inferred": False,
                "metadata": {
                    "trial_id": t.trial_id,
                    "environment_id": t.environment_id,
                    "baseline_id": t.baseline_id,
                    "selected_changes": selected_chgs,
                    "attempt_count": getattr(t, "attempt_count", 1),
                    "duration_ms": t.duration_ms,
                    "is_cleanup_verified": is_cleaned,
                },
            })

        # 7. Verification Runs
        verifications = self.storage.list_verification_runs(investigation_id=investigation_id)[:20]
        for v in verifications:
            v_id = getattr(v, "verification_id", getattr(v, "run_id", ""))
            v_start = getattr(v, "created_at", getattr(v, "start_time", ""))
            v_cands = getattr(v, "candidate_ids", getattr(v, "candidate_changes", []))
            v_rationale = getattr(v, "confidence_rationale", getattr(v, "rationale", ""))
            reversal_ok = getattr(v, "reversal_passed", getattr(v, "reversal_verified", False))
            v_dt = parse_iso_ts(v_start)
            verdict_str = v.verdict.value if hasattr(v.verdict, "value") else str(v.verdict)
            items.append({
                "id": f"verification-{v_id}",
                "timestamp": v_start,
                "event_type": "VERIFICATION_RUN",
                "source": "verification",
                "source_provenance": f"ENV:{v.environment_id}:BASELINE:{v.baseline_id}",
                "category": "verification",
                "title": f"Verification Run: {verdict_str}",
                "description": f"Verdict: {verdict_str}. Candidates: {', '.join(v_cands)}. Total Trials: {len(getattr(v, 'trials', []))}",
                "is_post_onset": bool(onset_dt and v_dt and v_dt > onset_dt),
                "outcome": verdict_str,
                "evidence_type": "VERIFICATION_VERDICT",
                "is_inferred": False,
                "metadata": {
                    "run_id": v_id,
                    "verdict": verdict_str,
                    "rationale": v_rationale,
                    "reversal_verified": reversal_ok,
                },
            })

            # Also add individual verification trial steps
            for vt in getattr(v, "trials", []):
                role_obj = getattr(vt, "trial_role", None) or getattr(vt, "role", None)
                vt_role = role_obj.value if hasattr(role_obj, "value") else str(role_obj or "")
                out_obj = getattr(vt, "outcome", None) or getattr(vt, "final_outcome", None)
                vt_outcome = out_obj.value if hasattr(out_obj, "value") else str(out_obj or "")
                vt_changes = getattr(vt, "target_change_ids", getattr(vt, "changes_tested", []))
                vt_notes = getattr(vt, "output_bounded", getattr(vt, "notes", ""))
                items.append({
                    "id": f"v-trial-{vt.trial_id}",
                    "timestamp": v_start,  # Bounded within the verification run
                    "event_type": "VERIFICATION_TRIAL",
                    "source": "verification",
                    "source_provenance": f"ROLE:{vt_role}",
                    "category": "verification_step",
                    "title": f"Verification Step [{vt_role}]: {vt_outcome}",
                    "description": f"Tested: {', '.join(vt_changes) or '<baseline>'}. Notes: {vt_notes}",
                    "is_post_onset": bool(onset_dt and v_dt and v_dt > onset_dt),
                    "outcome": vt_outcome,
                    "evidence_type": "VERIFICATION_STEP",
                    "is_inferred": False,
                    "metadata": {
                        "trial_id": vt.trial_id,
                        "role": vt_role,
                        "is_reversal": role_obj == TrialRole.REVERSAL_CHECK,
                        "changes_tested": vt_changes,
                        "duration_ms": vt.duration_ms,
                    },
                })

        # Apply Filters
        filtered_items: list[dict[str, Any]] = []
        for item in items:
            # Source filter
            if source_filter and source_filter.lower() != "all" and item["source"].lower() != source_filter.lower():
                continue
            # Category filter
            if category_filter and category_filter.lower() != "all" and item["category"].lower() != category_filter.lower():
                continue
            # Outcome filter
            if outcome_filter and outcome_filter.lower() != "all":
                item_out = item.get("outcome") or ""
                if item_out.upper() != outcome_filter.upper():
                    continue
            # Time range filter
            item_dt = parse_iso_ts(item.get("timestamp"))
            if start_bound and item_dt and item_dt < start_bound:
                continue
            if end_bound and item_dt and item_dt > end_bound:
                continue
            filtered_items.append(item)

        # Sort chronologically by timestamp (fallback to empty string)
        def sort_key(x: dict[str, Any]) -> tuple[datetime, str]:
            dt = parse_iso_ts(x.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)
            return (dt, x.get("id", ""))

        filtered_items.sort(key=sort_key)

        total_count = len(filtered_items)
        paginated = filtered_items[offset : offset + limit]

        return {
            "total": total_count,
            "items": paginated,
            "limit": limit,
            "offset": offset,
            "coverage_gaps": coverage_gaps,
            "onset_time": onset_str,
        }
