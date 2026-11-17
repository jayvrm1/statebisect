"""Deterministic and explainable candidate change ranking engine."""

from __future__ import annotations

import math
from typing import Any

from statebisect.monitor.models import ProcessProvenance

from .models import (
    CandidateChange,
    CandidateEvidenceType,
    CorrelationWindow,
    ScoreBreakdown,
)

RANKING_VERSION = "1.0.0"


class CandidateRanker:
    """Computes transparent, deterministic priority scores for investigation candidates.

    Safety Guarantee:
        Candidate ranking prioritizes which changes to test in Delta Debugging.
        It is NEVER represented as a probability of causation or proof of fault.
    """

    RANKING_VERSION = RANKING_VERSION

    def __init__(self, version: str = RANKING_VERSION) -> None:
        self.version = version

    def score_candidate(
        self,
        candidate: CandidateChange,
        target_system: str,
        window: CorrelationWindow,
    ) -> ScoreBreakdown:
        """Calculate explainable score breakdown for a single candidate."""
        # 1. Evidence Type Strength (0 - 30 pts)
        if candidate.evidence_type == CandidateEvidenceType.SNAPSHOT_DIFF:
            ev_score = 30.0
            ev_note = "Confirmed state difference between snapshots (+30)"
        elif candidate.category in ("registry", "service", "driver"):
            ev_score = 25.0
            ev_note = f"Direct system configuration change ({candidate.category}) (+25)"
        elif candidate.category == "event_log":
            ev_score = 18.0
            ev_note = "Event log observation (+18)"
        else:
            ev_score = 12.0
            ev_note = "Telemetry observation (+12)"

        # 2. Temporal Proximity to Onset (0 - 30 pts)
        if window.is_untimed_fallback or candidate.temporal_offset_sec is None:
            time_score = 12.0
            time_note = "Untimed fallback; temporal proximity not evaluated (+12)"
        elif candidate.temporal_offset_sec <= 0:
            # Occurred before or at symptom onset
            offset = abs(candidate.temporal_offset_sec)
            if offset <= 60:
                time_score = 30.0
                time_note = f"Observed {offset:.1f}s before onset (high proximity) (+30)"
            elif offset <= 300:
                time_score = 25.0
                time_note = f"Observed {offset/60:.1f}m before onset (+25)"
            elif offset <= 900:
                time_score = 20.0
                time_note = f"Observed {offset/60:.1f}m before onset (+20)"
            elif offset <= 3600:
                time_score = 15.0
                time_note = f"Observed {offset/60:.1f}m before onset (+15)"
            else:
                time_score = 8.0
                time_note = f"Observed {offset/3600:.1f}h before onset (+8)"
        else:
            # Occurred after symptom onset
            time_score = 5.0
            time_note = f"Observed {candidate.temporal_offset_sec:.1f}s after onset (may be symptom/consequence, not cause) (+5)"

        # 3. Target System Relevance (0 - 20 pts)
        target_lower = target_system.strip().lower()
        if not target_lower:
            target_score = 10.0
            target_note = "No specific target system specified (+10)"
        else:
            obj_lower = candidate.object_key.lower()
            name_lower = candidate.display_name.lower()
            desc_lower = candidate.description.lower()

            if (target_lower in obj_lower) or (target_lower in name_lower) or (target_lower in desc_lower):
                target_score = 20.0
                target_note = f"Direct match to target system '{target_system}' (+20)"
            elif any(sys_kw in obj_lower or sys_kw in desc_lower for sys_kw in ("tls", "ssl", "network", "firewall", "driver", "service")):
                target_score = 10.0
                target_note = "Matches system infrastructure dependency (+10)"
            else:
                target_score = 0.0
                target_note = f"No direct relevance to target '{target_system}' (+0)"

        # 4. Process Provenance Quality (0 - 10 pts)
        if candidate.process_provenance == ProcessProvenance.EVENT_PAYLOAD:
            prov_score = 10.0
            prov_note = "Process identity verified in event payload (+10)"
        elif candidate.process_provenance == ProcessProvenance.PROCESS_LOOKUP:
            prov_score = 6.0
            prov_note = "Process identity resolved via lookup (+6)"
        else:
            prov_score = 2.0
            prov_note = "Process identity unavailable (+2)"

        # 5. Corroboration / Multi-source (0 - 10 pts)
        if len(candidate.source_ids) > 1 or candidate.evidence_type == CandidateEvidenceType.CORROBORATED:
            corr_score = 10.0
            corr_note = "Corroborated across multiple observation sources (+10)"
        else:
            corr_score = 0.0
            corr_note = "Single source observation (+0)"

        # 6. Coverage Gap Penalty (0 to -15 pts)
        if candidate.coverage_gaps:
            gap_penalty = 10.0
            gap_note = f"Coverage gaps present in {candidate.category} category (-10)"
        else:
            gap_penalty = 0.0
            gap_note = "No coverage gaps observed in category (-0)"

        raw_total = ev_score + time_score + target_score + prov_score + corr_score - gap_penalty
        bounded_total = max(0.0, min(100.0, raw_total))

        full_rationale = "; ".join([ev_note, time_note, target_note, prov_note, corr_note, gap_note])

        return ScoreBreakdown(
            evidence_type_score=ev_score,
            proximity_score=time_score,
            target_relevance_score=target_score,
            provenance_score=prov_score,
            corroboration_score=corr_score,
            gap_penalty=gap_penalty,
            total_score=bounded_total,
            rationale=full_rationale,
        )

    calculate_score = score_candidate

    def rank_candidates(
        self,
        candidates: list[CandidateChange],
        target_system: str,
        window: CorrelationWindow,
    ) -> list[CandidateChange]:
        """Rank candidates deterministically using the scoring breakdown and tie-breakers."""
        for cand in candidates:
            breakdown = self.score_candidate(cand, target_system, window)
            cand.score_breakdown = breakdown
            cand.score = breakdown.total_score
            cand.inclusion_reason = breakdown.rationale

        # Deterministic sorting key:
        # 1. Total score descending (-score)
        # 2. Distance to onset: closest first (prefer negative offsets that occurred right before onset)
        # 3. Evidence type weight
        # 4. Candidate ID string ascending (guarantees determinism on ties)
        def sort_key(c: CandidateChange) -> tuple[float, float, int, str]:
            if c.temporal_offset_sec is not None:
                # If negative, closer to 0 is better. If positive, further penalize
                time_dist = abs(c.temporal_offset_sec) if c.temporal_offset_sec <= 0 else abs(c.temporal_offset_sec) + 100000.0
            else:
                time_dist = 50000.0

            ev_weight = 1 if c.evidence_type == CandidateEvidenceType.SNAPSHOT_DIFF else 2
            return (-c.score, time_dist, ev_weight, c.candidate_id)

        sorted_candidates = sorted(candidates, key=sort_key)

        # Assign 1-based ranks
        for idx, cand in enumerate(sorted_candidates, start=1):
            cand.rank = idx

        return sorted_candidates
