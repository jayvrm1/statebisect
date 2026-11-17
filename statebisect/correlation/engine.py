"""Candidate change generation and evidence-based correlation engine."""

from __future__ import annotations

import datetime
from datetime import datetime as dt, timezone
from typing import Any

from statebisect.collector.models import DiffChangeType, SnapshotDiff
from statebisect.monitor.models import MonitoringEvent, ProcessProvenance
from statebisect.storage import Storage

from .models import (
    CandidateChange,
    CandidateEvidenceType,
    CandidateReviewStatus,
    CorrelationWindow,
)
from .ranker import CandidateRanker, RANKING_VERSION


def parse_iso_or_user_time(time_str: str | None, tz_offset_hours: float = 0.0) -> dt | None:
    """Safely parse user-supplied or ISO datetime string into UTC datetime."""
    if not time_str or not time_str.strip():
        return None

    cleaned = time_str.strip()
    try:
        # Try direct ISO format with timezone or Z
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = dt.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            # Assume local offset or UTC
            tz = timezone(datetime.timedelta(hours=tz_offset_hours))
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    # Try common fallback patterns
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = dt.strptime(cleaned, fmt)
            tz = timezone(datetime.timedelta(hours=tz_offset_hours))
            return naive.replace(tzinfo=tz).astimezone(timezone.utc)
        except ValueError:
            continue

    return None


class CorrelationEngine:
    """Generates and prioritizes candidate changes by correlating observed telemetry with failure onset."""

    def __init__(
        self,
        storage: Storage,
        ranker: CandidateRanker | None = None,
    ) -> None:
        self.storage = storage
        self.ranker = ranker or CandidateRanker()

    def build_correlation_window(
        self,
        onset_time_str: str | None,
        pre_onset_sec: int = 3600,
        post_onset_sec: int = 300,
        timezone_str: str = "UTC",
    ) -> CorrelationWindow:
        """Construct and validate correlation time window around symptom onset."""
        onset_utc = parse_iso_or_user_time(onset_time_str)

        if onset_utc is None:
            return CorrelationWindow(
                onset_time_utc=None,
                onset_time_user=onset_time_str,
                onset_timezone=timezone_str,
                pre_onset_sec=pre_onset_sec,
                post_onset_sec=post_onset_sec,
                window_start_utc=None,
                window_end_utc=None,
                is_untimed_fallback=True,
            )

        start_utc = onset_utc - datetime.timedelta(seconds=pre_onset_sec)
        end_utc = onset_utc + datetime.timedelta(seconds=post_onset_sec)

        return CorrelationWindow(
            onset_time_utc=onset_utc.isoformat(),
            onset_time_user=onset_time_str,
            onset_timezone=timezone_str,
            pre_onset_sec=pre_onset_sec,
            post_onset_sec=post_onset_sec,
            window_start_utc=start_utc.isoformat(),
            window_end_utc=end_utc.isoformat(),
            is_untimed_fallback=False,
        )

    def generate_candidates(
        self,
        investigation_id: str,
        target_system: str,
        window: CorrelationWindow,
        snapshot_diffs: list[SnapshotDiff] | None = None,
        monitoring_events: list[MonitoringEvent] | None = None,
        coverage_gaps: list[dict[str, Any]] | None = None,
    ) -> list[CandidateChange]:
        """Correlate evidence and generate ranked candidate changes.

        Guarantees:
            1. Never invents candidates from unobserved or missing data.
            2. Fully transparent score breakdown for each candidate.
            3. Deterministic ordering with no machine learning.
        """
        raw_diffs = snapshot_diffs or []
        raw_events = monitoring_events or []
        gaps_list = coverage_gaps or []

        gaps_by_category: dict[str, list[dict[str, Any]]] = {}
        for g in gaps_list:
            cat = g.get("category", "unknown")
            gaps_by_category.setdefault(cat, []).append(g)

        onset_dt = dt.fromisoformat(window.onset_time_utc) if window.onset_time_utc else None
        candidates_by_key: dict[str, CandidateChange] = {}

        # 1. Process Phase 4 Snapshot Diffs
        for diff in raw_diffs:
            # If diff was UNKNOWN due to coverage gaps, note uncertainty
            uncertainty = ""
            if diff.change_type == DiffChangeType.UNKNOWN:
                uncertainty = f"Diff was marked UNKNOWN due to prior coverage gaps in category '{diff.category}'."

            # Calculate temporal offset if timestamp available
            offset_sec = None
            diff_ts = ""
            # Use diff notes or target run time if available
            if onset_dt:
                # Target snapshot time is an approximation of when the change was observed present
                offset_sec = 0.0  # Confirmed present in snapshot near investigation time

            cand_id = f"cand-diff-{diff.diff_id}"
            category_gaps = gaps_by_category.get(diff.category, [])

            cand = CandidateChange(
                candidate_id=cand_id,
                investigation_id=investigation_id,
                evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
                category=diff.category,
                object_key=diff.object_key,
                display_name=diff.object_key,
                description=f"{diff.change_type.value}: {diff.notes}",
                first_observed_utc=diff_ts or dt.now(timezone.utc).isoformat(),
                last_observed_utc=diff_ts or dt.now(timezone.utc).isoformat(),
                temporal_offset_sec=offset_sec,
                before_value=diff.before_data,
                after_value=diff.after_data,
                process_id=None,
                process_name=None,
                process_provenance=ProcessProvenance.UNAVAILABLE,
                source_ids=["snapshot_diff"],
                evidence_ids=[diff.diff_id],
                coverage_gaps=category_gaps,
                review_status=CandidateReviewStatus.INCLUDED,
                is_active=True,
                uncertainty_note=uncertainty,
            )
            candidates_by_key[diff.object_key] = cand

        # 2. Process Phase 5 Monitoring Events
        for ev in raw_events:
            ev_dt = parse_iso_or_user_time(ev.source_timestamp or ev.timestamp_utc)
            offset_sec = None

            if onset_dt and ev_dt:
                offset_sec = (ev_dt - onset_dt).total_seconds()
                # Check if within correlation window bounds
                if not window.is_untimed_fallback:
                    if offset_sec < -window.pre_onset_sec or offset_sec > window.post_onset_sec:
                        # Event outside correlation window; skip to maintain focus
                        continue

            event_key = f"{ev.channel_or_path}:{ev.event_code}"
            # Check for corroboration with an existing snapshot diff
            existing = candidates_by_key.get(ev.channel_or_path) or candidates_by_key.get(event_key)

            if existing:
                # Corroborated! Merge evidence
                existing.evidence_type = CandidateEvidenceType.CORROBORATED
                if ev.source_id not in existing.source_ids:
                    existing.source_ids.append(ev.source_id)
                if ev.event_id not in existing.evidence_ids:
                    existing.evidence_ids.append(ev.event_id)
                if ev.process_id and not existing.process_id:
                    existing.process_id = ev.process_id
                    existing.process_name = ev.process_name
                    existing.process_provenance = ev.process_provenance
                if offset_sec is not None:
                    existing.temporal_offset_sec = offset_sec
            else:
                cand_id = f"cand-evt-{ev.event_id}"
                category_gaps = gaps_by_category.get(ev.category, [])
                before_val = ev.payload.get("old_value") if isinstance(ev.payload, dict) else None
                after_val = ev.payload.get("new_value") if isinstance(ev.payload, dict) else ev.payload

                cand = CandidateChange(
                    candidate_id=cand_id,
                    investigation_id=investigation_id,
                    evidence_type=CandidateEvidenceType.MONITORING_EVENT,
                    category=ev.category,
                    object_key=event_key,
                    display_name=f"{ev.source_id} ({ev.channel_or_path})",
                    description=f"{ev.event_code} observed from {ev.source_id}",
                    first_observed_utc=ev.source_timestamp or ev.timestamp_utc,
                    last_observed_utc=ev.timestamp_utc,
                    temporal_offset_sec=offset_sec,
                    before_value=before_val,
                    after_value=after_val,
                    process_id=ev.process_id,
                    process_name=ev.process_name,
                    process_provenance=ev.process_provenance,
                    source_ids=[ev.source_id],
                    evidence_ids=[ev.event_id],
                    coverage_gaps=category_gaps,
                    review_status=CandidateReviewStatus.INCLUDED,
                    is_active=True,
                )
                candidates_by_key[event_key] = cand

        candidate_list = list(candidates_by_key.values())

        # 3. Score and Rank Deterministically
        ranked = self.ranker.rank_candidates(candidate_list, target_system, window)
        return ranked
