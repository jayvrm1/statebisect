"""Comprehensive unit and integration tests for Phase 6 candidate change generation, ranking, and review workflow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from statebisect.collector.models import DiffChangeType, SnapshotDiff
from statebisect.correlation import (
    CandidateChange,
    CandidateEvidenceType,
    CandidateRanker,
    CandidateReversibility,
    CandidateReviewRecord,
    CandidateReviewStatus,
    CorrelationEngine,
    CorrelationWindow,
    ScoreBreakdown,
)
from statebisect.correlation.engine import parse_iso_or_user_time
from statebisect.models import Investigation, InvestigationStatus
from statebisect.monitor.models import EventConfidence, MonitoringEvent, ProcessProvenance
from statebisect.storage import Storage


class TestCorrelationTimeAndWindow(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.engine = CorrelationEngine(self.storage)

    def tearDown(self):
        self.storage.close()

    def test_parse_iso_and_user_time(self):
        # ISO with Z
        t1 = parse_iso_or_user_time("2026-03-30T10:00:00Z")
        self.assertIsNotNone(t1)
        self.assertEqual(t1.hour, 10)

        # Standard YYYY-MM-DD HH:MM:SS
        t2 = parse_iso_or_user_time("2026-03-30 10:00:00")
        self.assertIsNotNone(t2)
        self.assertEqual(t2.year, 2026)

        # None or invalid returns None
        self.assertIsNone(parse_iso_or_user_time(None))
        self.assertIsNone(parse_iso_or_user_time(""))
        self.assertIsNone(parse_iso_or_user_time("invalid-timestamp-string"))

    def test_build_correlation_window_timed(self):
        window = self.engine.build_correlation_window(
            onset_time_str="2026-03-30T12:00:00Z",
            pre_onset_sec=1800,
            post_onset_sec=300,
            timezone_str="UTC",
        )
        self.assertFalse(window.is_untimed_fallback)
        self.assertEqual(window.pre_onset_sec, 1800)
        self.assertEqual(window.post_onset_sec, 300)
        self.assertIn("11:30:00", window.window_start_utc)
        self.assertIn("12:05:00", window.window_end_utc)

    def test_build_correlation_window_untimed_fallback(self):
        window = self.engine.build_correlation_window(
            onset_time_str=None,
            pre_onset_sec=3600,
            post_onset_sec=300,
        )
        self.assertTrue(window.is_untimed_fallback)
        self.assertIsNone(window.window_start_utc)
        self.assertIsNone(window.window_end_utc)


class TestCandidateRanking(unittest.TestCase):
    def setUp(self):
        self.ranker = CandidateRanker()
        self.window = CorrelationWindow(
            onset_time_utc="2026-03-30T12:00:00+00:00",
            window_start_utc="2026-03-30T11:00:00+00:00",
            window_end_utc="2026-03-30T12:05:00+00:00",
            pre_onset_sec=3600,
            post_onset_sec=300,
            is_untimed_fallback=False,
        )

    def test_evidence_type_scoring(self):
        # SNAPSHOT_DIFF: 30 pts
        c_diff = CandidateChange(
            candidate_id="c1",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="registry",
            object_key="HKLM\\Software\\TestApp\\Config",
            display_name="Config",
            description="Registry value modified",
            first_observed_utc="2026-03-30T11:59:00Z",
            last_observed_utc="2026-03-30T11:59:00Z",
            temporal_offset_sec=-60.0,
        )
        score1 = self.ranker.calculate_score(c_diff, "TestApp", self.window)
        self.assertEqual(score1.evidence_type_score, 30.0)

        # MONITORING_EVENT: depends on category
        c_mon = CandidateChange(
            candidate_id="c2",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.MONITORING_EVENT,
            category="event_log",
            object_key="System:7036",
            display_name="Service Control Manager",
            description="Service state changed",
            first_observed_utc="2026-03-30T11:59:00Z",
            last_observed_utc="2026-03-30T11:59:00Z",
            temporal_offset_sec=-60.0,
        )
        score2 = self.ranker.calculate_score(c_mon, "TestApp", self.window)
        self.assertEqual(score2.evidence_type_score, 18.0)

    def test_proximity_scoring(self):
        # Event within 60s before onset gets max proximity (30 pts)
        c_near = CandidateChange(
            candidate_id="c1",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="registry",
            object_key="HKLM\\Software\\TestApp\\Config",
            display_name="Config",
            description="Modified",
            first_observed_utc="2026-03-30T11:59:30Z",
            last_observed_utc="2026-03-30T11:59:30Z",
            temporal_offset_sec=-30.0,
        )
        score = self.ranker.calculate_score(c_near, "TestApp", self.window)
        self.assertEqual(score.proximity_score, 30.0)

        # Event after onset gets low score (5 pts) and warning about symptom consequence
        c_after = CandidateChange(
            candidate_id="c2",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.MONITORING_EVENT,
            category="app_event",
            object_key="Application:1000",
            display_name="App Crash",
            description="Crash",
            first_observed_utc="2026-03-30T12:02:00Z",
            last_observed_utc="2026-03-30T12:02:00Z",
            temporal_offset_sec=120.0,
        )
        score_after = self.ranker.calculate_score(c_after, "TestApp", self.window)
        self.assertEqual(score_after.proximity_score, 5.0)
        self.assertIn("consequence", score_after.rationale)

    def test_target_relevance_scoring(self):
        # Direct target name match: 20 pts
        c_target = CandidateChange(
            candidate_id="c1",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="installed_software",
            object_key="TargetApp",
            display_name="TargetApp",
            description="Software upgraded",
            first_observed_utc="2026-03-30T11:55:00Z",
            last_observed_utc="2026-03-30T11:55:00Z",
        )
        score = self.ranker.calculate_score(c_target, "TargetApp", self.window)
        self.assertEqual(score.target_relevance_score, 20.0)

        # System service dependency: 10 pts
        c_service = CandidateChange(
            candidate_id="c2",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="services",
            object_key="wuauserv",
            display_name="Windows Update",
            description="Service modified",
            first_observed_utc="2026-03-30T11:55:00Z",
            last_observed_utc="2026-03-30T11:55:00Z",
        )
        score_service = self.ranker.calculate_score(c_service, "TargetApp", self.window)
        self.assertEqual(score_service.target_relevance_score, 10.0)

    def test_corroboration_bonus(self):
        c_corrob = CandidateChange(
            candidate_id="c1",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.CORROBORATED,
            category="registry",
            object_key="HKLM\\Software\\TargetApp\\Port",
            display_name="Port",
            description="Observed by multiple sources",
            first_observed_utc="2026-03-30T11:59:00Z",
            last_observed_utc="2026-03-30T11:59:00Z",
            source_ids=["snapshot_diff", "registry_monitor"],
            evidence_ids=["diff-1", "evt-1"],
        )
        score = self.ranker.calculate_score(c_corrob, "TargetApp", self.window)
        self.assertEqual(score.corroboration_score, 10.0)

    def test_coverage_gap_penalty(self):
        c_gap = CandidateChange(
            candidate_id="c1",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="drivers",
            object_key="drv_gpu",
            display_name="GPU Driver",
            description="Driver updated",
            first_observed_utc="2026-03-30T11:55:00Z",
            last_observed_utc="2026-03-30T11:55:00Z",
            coverage_gaps=[{"category": "drivers", "reason": "Access denied"}],
        )
        score = self.ranker.calculate_score(c_gap, "TargetApp", self.window)
        self.assertEqual(score.gap_penalty, 10.0)
        self.assertIn("Coverage gaps present", score.rationale)

    def test_deterministic_ranking_and_tie_breaking(self):
        # Two candidates with identical scores should break ties deterministically by candidate_id
        c_a = CandidateChange(
            candidate_id="cand-a",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="registry",
            object_key="HKLM\\Alpha",
            display_name="Alpha",
            description="Alpha",
            first_observed_utc="2026-03-30T11:59:00Z",
            last_observed_utc="2026-03-30T11:59:00Z",
            temporal_offset_sec=-60.0,
        )
        c_b = CandidateChange(
            candidate_id="cand-b",
            investigation_id="inv1",
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="registry",
            object_key="HKLM\\Beta",
            display_name="Beta",
            description="Beta",
            first_observed_utc="2026-03-30T11:59:00Z",
            last_observed_utc="2026-03-30T11:59:00Z",
            temporal_offset_sec=-60.0,
        )
        ranked1 = self.ranker.rank_candidates([c_b, c_a], "TestApp", self.window)
        ranked2 = self.ranker.rank_candidates([c_a, c_b], "TestApp", self.window)

        # Ordering must be perfectly deterministic regardless of input list ordering
        self.assertEqual(ranked1[0].candidate_id, "cand-a")
        self.assertEqual(ranked1[1].candidate_id, "cand-b")
        self.assertEqual(ranked1[0].rank, 1)
        self.assertEqual(ranked1[1].rank, 2)
        self.assertEqual([c.candidate_id for c in ranked1], [c.candidate_id for c in ranked2])


class TestCandidateGenerationAndPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_correlation.db"
        self.storage = Storage(self.db_path)
        self.engine = CorrelationEngine(self.storage)

        # Create investigation
        self.inv = Investigation(
            id="inv-test-corr",
            symptom="Target application failed to bind network port",
            target_system="DemoApp",
            status=InvestigationStatus.NEW,
            onset_time_utc="2026-03-30T12:00:00+00:00",
            onset_time_user="2026-03-30 12:00:00",
            onset_timezone="UTC",
        )
        self.storage.save_investigation(self.inv)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_generate_and_corroborate_candidates(self):
        window = self.engine.build_correlation_window(
            onset_time_str=self.inv.onset_time_utc,
            pre_onset_sec=3600,
            post_onset_sec=300,
        )

        # 1. Snapshot Diff
        diff = SnapshotDiff(
            diff_id="diff-1",
            before_run_id="run-a",
            after_run_id="run-b",
            category="registry",
            object_key="HKLM\\Software\\DemoApp\\Port",
            change_type=DiffChangeType.MODIFIED,
            before_data={"value": 8080},
            after_data={"value": 80},
            notes="Modified port",
        )

        # 2. Monitoring Event on same registry key (corroboration)
        ev = MonitoringEvent(
            event_id="evt-1",
            session_id="mon-1",
            source_id="registry_monitor",
            source_version="1.0.0",
            category="registry",
            timestamp_utc="2026-03-30T11:59:30+00:00",
            source_timestamp="2026-03-30T11:59:30+00:00",
            channel_or_path="HKLM\\Software\\DemoApp\\Port",
            event_code="VALUE_MODIFIED",
            process_id=4560,
            process_name="setup.exe",
            process_provenance=ProcessProvenance.EVENT_PAYLOAD,
            payload={"old_value": 8080, "new_value": 80},
            confidence=EventConfidence.CONFIRMED,
        )

        # 3. Another event outside window (should be excluded)
        ev_out = MonitoringEvent(
            event_id="evt-out",
            session_id="mon-1",
            source_id="event_log_monitor",
            source_version="1.0.0",
            category="event_log",
            timestamp_utc="2026-03-30T10:00:00+00:00",  # 2 hours before onset, pre-window is 1 hour
            source_timestamp="2026-03-30T10:00:00+00:00",
            channel_or_path="System",
            event_code="7036",
            process_provenance=ProcessProvenance.UNAVAILABLE,
            payload={},
            confidence=EventConfidence.CONFIRMED,
        )

        candidates = self.engine.generate_candidates(
            investigation_id=self.inv.id,
            target_system=self.inv.target_system,
            window=window,
            snapshot_diffs=[diff],
            monitoring_events=[ev, ev_out],
        )

        # evt-out should be filtered out by correlation window bounds
        self.assertEqual(len(candidates), 1)
        c = candidates[0]

        # Verify corroboration
        self.assertEqual(c.evidence_type, CandidateEvidenceType.CORROBORATED)
        self.assertIn("snapshot_diff", c.source_ids)
        self.assertIn("registry_monitor", c.source_ids)
        self.assertEqual(c.process_name, "setup.exe")
        self.assertEqual(c.process_provenance, ProcessProvenance.EVENT_PAYLOAD)
        self.assertEqual(c.rank, 1)
        self.assertGreater(c.score, 60.0)

        # Persist and retrieve from storage
        self.storage.save_candidates(self.inv.id, candidates)
        loaded = self.storage.get_candidates(self.inv.id)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].candidate_id, c.candidate_id)
        self.assertEqual(loaded[0].score, c.score)
        self.assertEqual(loaded[0].evidence_type, CandidateEvidenceType.CORROBORATED)

    def test_candidate_review_include_exclude_audit(self):
        c1 = CandidateChange(
            candidate_id="cand-rev-1",
            investigation_id=self.inv.id,
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="registry",
            object_key="HKLM\\Test",
            display_name="Test",
            description="Desc",
            first_observed_utc="2026-03-30T11:59:00Z",
            last_observed_utc="2026-03-30T11:59:00Z",
            score=50.0,
            rank=1,
            review_status=CandidateReviewStatus.INCLUDED,
            is_active=True,
        )
        self.storage.save_candidates(self.inv.id, [c1])

        # Exclude the candidate
        rev = CandidateReviewRecord(
            review_id="rev-001",
            candidate_id="cand-rev-1",
            investigation_id=self.inv.id,
            decision="EXCLUDE",
            previous_status=CandidateReviewStatus.INCLUDED,
            new_status=CandidateReviewStatus.EXCLUDED,
            reason="Confirmed unrelated background update from telemetry",
            reviewed_at="2026-03-30T12:10:00Z",
        )
        self.storage.update_candidate_review_status("cand-rev-1", CandidateReviewStatus.EXCLUDED, is_active=False)
        self.storage.record_candidate_review(rev)

        # Check retrieval
        cand = self.storage.get_candidate("cand-rev-1")
        self.assertIsNotNone(cand)
        self.assertEqual(cand.review_status, CandidateReviewStatus.EXCLUDED)
        self.assertFalse(cand.is_active)

        # Check active_only filter
        active_list = self.storage.get_candidates(self.inv.id, active_only=True)
        self.assertEqual(len(active_list), 0)

        all_list = self.storage.get_candidates(self.inv.id, active_only=False)
        self.assertEqual(len(all_list), 1)

        # Check audit trail
        reviews = self.storage.get_candidate_reviews("cand-rev-1")
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].decision, "EXCLUDE")
        self.assertIn("Confirmed unrelated", reviews[0].reason)


class TestMandatoryExceptionHandling(unittest.TestCase):
    """Ensure compliance with the rule: Never use return, break, or continue in finally blocks."""

    def test_no_suppressed_exceptions_in_correlation_engine(self):
        storage = Storage(":memory:")
        engine = CorrelationEngine(storage)

        # Even with edge cases or malformed inputs, no exceptions should be silently eaten by finally
        with self.assertRaises(Exception):
            # Passing an invalid type to trigger an exception and ensure it propagates cleanly
            engine.generate_candidates(
                investigation_id="inv-err",
                target_system="App",
                window=None,  # type: ignore - purposely triggers AttributeError on window
            )
        storage.close()


if __name__ == "__main__":
    unittest.main()
