"""Tests for Phase 5 targeted, opt-in change monitoring."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from statebisect.models import CancellationToken
from statebisect.monitor import (
    DEFAULT_DURATION_SEC,
    MAX_DURATION_SEC,
    MIN_DURATION_SEC,
    BaseMonitorSource,
    EtwEvaluationSource,
    EventConfidence,
    EventLogMonitorSource,
    MonitoringCoverageGap,
    MonitoringEngine,
    MonitoringEvent,
    MonitoringSession,
    MonitoringSessionStatus,
    ProcessProvenance,
    RegistryMonitorSource,
    SysmonMonitorSource,
)
from statebisect.storage import Storage


class DummyMockSource(BaseMonitorSource):
    """Deterministic mock source for lifecycle and limit testing."""

    def __init__(self, source_id: str = "mock_source", event_count: int = 1) -> None:
        super().__init__(
            source_id=source_id,
            category="mock",
            description="Mock source for testing",
        )
        self.event_count = event_count
        self.poll_calls = 0
        self.stopped = False

    def is_supported(self) -> tuple[bool, str]:
        return True, "Mock source is always supported"

    def start(self, session_id: str, start_time_utc: str) -> list[MonitoringCoverageGap]:
        self.session_id = session_id
        self.start_time_utc = start_time_utc
        self.stopped = False
        return []

    def poll(self) -> list[MonitoringEvent]:
        self.poll_calls += 1
        events = []
        for i in range(self.event_count):
            events.append(
                MonitoringEvent(
                    event_id=f"evt-mock-{self.poll_calls}-{i}",
                    session_id=self.session_id,
                    source_id=self.source_id,
                    source_version=self.version,
                    category=self.category,
                    timestamp_utc="2026-11-17T00:00:00Z",
                    source_timestamp="2026-11-17T00:00:00Z",
                    channel_or_path="MockChannel",
                    event_code="MOCK_CHANGE",
                    process_id=1234,
                    process_name="mock.exe",
                    process_provenance=ProcessProvenance.EVENT_PAYLOAD,
                    payload={"key": f"val_{i}"},
                    confidence=EventConfidence.CONFIRMED,
                    raw_ref=f"raw-{self.poll_calls}-{i}",
                )
            )
        return events

    def stop(self) -> list[MonitoringCoverageGap]:
        self.stopped = True
        return []


class DummyFailingMonitorSource(BaseMonitorSource):
    """Mock source that raises an exception during polling."""

    def __init__(self) -> None:
        super().__init__(
            source_id="failing_mock",
            category="mock",
            description="Failing mock source",
        )

    def is_supported(self) -> tuple[bool, str]:
        return True, "Supported"

    def start(self, session_id: str, start_time_utc: str) -> list[MonitoringCoverageGap]:
        self.session_id = session_id
        return []

    def poll(self) -> list[MonitoringEvent]:
        raise RuntimeError("Simulated polling failure")

    def stop(self) -> list[MonitoringCoverageGap]:
        return []


class TestMonitorLifecycle(unittest.TestCase):
    def test_preview_does_not_start_monitoring(self):
        engine = MonitoringEngine(allow_non_windows=True)
        prev = engine.preview()
        self.assertIsInstance(prev, dict)
        self.assertEqual(prev["engine_version"], "1.0.0")
        self.assertGreaterEqual(prev["sources_count"], 4)
        self.assertEqual(prev["min_duration_sec"], MIN_DURATION_SEC)
        self.assertEqual(prev["max_duration_sec"], MAX_DURATION_SEC)

    def test_duration_bounds_validation(self):
        engine = MonitoringEngine(allow_non_windows=True)
        # Below minimum
        with self.assertRaises(ValueError):
            engine.run_session(duration_sec=0)
        # Above maximum
        with self.assertRaises(ValueError):
            engine.run_session(duration_sec=MAX_DURATION_SEC + 10)

    def test_normal_session_completion(self):
        mock_src = DummyMockSource(event_count=2)
        engine = MonitoringEngine(
            sources=[mock_src],
            poll_interval_sec=0.1,
            allow_non_windows=True,
        )
        session = engine.run_session(duration_sec=1)

        self.assertEqual(session.status, MonitoringSessionStatus.COMPLETED)
        self.assertEqual(session.stop_reason, "COMPLETED_DURATION")
        self.assertGreater(session.total_events, 0)
        self.assertTrue(mock_src.stopped)

    def test_cancellation_token_stops_session_immediately(self):
        mock_src = DummyMockSource(event_count=1)
        engine = MonitoringEngine(
            sources=[mock_src],
            poll_interval_sec=0.1,
            allow_non_windows=True,
        )
        token = CancellationToken()
        # Cancel right away
        token.cancel()

        session = engine.run_session(
            duration_sec=10,
            cancellation_token=token,
        )

        self.assertEqual(session.status, MonitoringSessionStatus.CANCELLED)
        self.assertEqual(session.stop_reason, "USER_INTERRUPT")
        self.assertTrue(mock_src.stopped)

    def test_polling_failure_is_captured_in_coverage_gaps(self):
        failing_src = DummyFailingMonitorSource()
        engine = MonitoringEngine(
            sources=[failing_src],
            poll_interval_sec=0.1,
            allow_non_windows=True,
        )
        session = engine.run_session(duration_sec=1)

        # Polling failure should be recorded as a gap, not crash the engine
        self.assertGreater(len(session.coverage_gaps), 0)
        gap = session.coverage_gaps[0]
        self.assertEqual(gap.source_id, "failing_mock")
        self.assertIn("Simulated polling failure", gap.reason)


class TestMonitorSources(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Requires Windows host")
    def test_event_log_source_windows(self):
        src = EventLogMonitorSource(channels=["Application"])
        supp, reason = src.is_supported()
        self.assertTrue(supp)

        gaps = src.start("mon-test-session", "2026-11-17T00:00:00Z")
        self.assertEqual(len(gaps), 0)

        events = src.poll()
        self.assertIsInstance(events, list)
        src.stop()

    def test_event_log_source_security_log_exclusion(self):
        src = EventLogMonitorSource(channels=["Application", "Security"])
        gaps = src.start("mon-test-session", "2026-11-17T00:00:00Z")
        self.assertEqual(len(gaps), 1)
        self.assertIn("Security log requires elevated", gaps[0].reason)

    def test_sysmon_source_detection(self):
        src = SysmonMonitorSource()
        supp, reason = src.is_supported()
        gaps = src.start("mon-test-session", "2026-11-17T00:00:00Z")
        if not supp:
            self.assertEqual(len(gaps), 1)
            self.assertEqual(gaps[0].source_id, "sysmon")
            self.assertIn("Sysmon source unavailable", gaps[0].reason)

    def test_etw_evaluation_source_safe_deferral(self):
        src = EtwEvaluationSource()
        supp, reason = src.is_supported()
        self.assertFalse(supp)
        self.assertIn("Deferred", reason)
        self.assertIn("TRACELOG_CREATE_REALTIME", reason)

        gaps = src.start("mon-test-session", "2026-11-17T00:00:00Z")
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].source_id, "etw")
        self.assertIn("TRACELOG_CREATE_REALTIME", gaps[0].reason)

    def test_registry_watcher_allowlist_enforcement(self):
        # Forbidden security path
        src_bad = RegistryMonitorSource(watch_paths=[r"HKLM\SAM\Domains\Account"])
        gaps = src_bad.start("mon-test-session", "2026-11-17T00:00:00Z")
        self.assertEqual(len(gaps), 1)
        self.assertIn("forbidden security token", gaps[0].reason)

        # Non-allowlisted arbitrary path
        src_unlisted = RegistryMonitorSource(watch_paths=[r"HKLM\SOFTWARE\ArbitraryVendor\Settings"])
        gaps2 = src_unlisted.start("mon-test-session", "2026-11-17T00:00:00Z")
        self.assertEqual(len(gaps2), 1)
        self.assertIn("not in the strict StateBisect registry allowlist", gaps2[0].reason)

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows host")
    def test_registry_watcher_process_provenance_is_unavailable(self):
        src = RegistryMonitorSource(watch_paths=[r"HKCU\Environment"])
        src.start("mon-test-session", "2026-11-17T00:00:00Z")
        ev = src._create_event(
            path_str=r"HKCU\Environment",
            event_code="VALUE_MODIFIED",
            val_name="TestVar",
            old_val="1",
            new_val="2",
            timestamp="2026-11-17T00:00:00Z",
        )
        # In standard user space, registry modifications cannot infer PID
        self.assertEqual(ev.process_provenance, ProcessProvenance.UNAVAILABLE)
        self.assertIsNone(ev.process_id)


class TestMonitorLimitsAndStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "monitor_test.db"
        self.storage = Storage(self.db_path)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_max_events_buffer_overflow_handling(self):
        # Emit 50 events per poll, max 100 events
        mock_src = DummyMockSource(event_count=50)
        engine = MonitoringEngine(
            sources=[mock_src],
            max_total_events=100,
            poll_interval_sec=0.05,
            allow_non_windows=True,
        )
        session = engine.run_session(duration_sec=5)

        self.assertLessEqual(session.total_events, 100)
        self.assertEqual(session.stop_reason, "LIMIT_EXCEEDED")
        self.assertGreater(session.loss_indicators["events_dropped"], 0)

    def test_save_and_retrieve_monitoring_session(self):
        gap = MonitoringCoverageGap(
            gap_id="gap-test-1",
            session_id="mon-pers-1",
            source_id="test_src",
            category="test_cat",
            reason="Test gap reason",
            impact="Test gap impact",
        )
        ev = MonitoringEvent(
            event_id="evt-pers-1",
            session_id="mon-pers-1",
            source_id="test_src",
            source_version="1.0.0",
            category="test_cat",
            timestamp_utc="2026-11-17T00:00:00Z",
            source_timestamp="2026-11-17T00:00:00Z",
            channel_or_path="TestPath",
            event_code="TEST_CODE",
            process_id=456,
            process_name="test.exe",
            process_provenance=ProcessProvenance.EVENT_PAYLOAD,
            payload={"key": "secret_password_value"},
            is_redacted=True,
            is_truncated=False,
            confidence=EventConfidence.CONFIRMED,
            raw_ref="raw-ref-1",
        )
        session = MonitoringSession(
            session_id="mon-pers-1",
            created_at="2026-11-17T00:00:00Z",
            started_at="2026-11-17T00:00:00Z",
            stopped_at="2026-11-17T00:00:05Z",
            duration_sec=5,
            elapsed_sec=5.02,
            status=MonitoringSessionStatus.COMPLETED,
            stop_reason="COMPLETED_DURATION",
            sources_requested=["test_src"],
            sources_active=["test_src"],
            total_events=1,
            events_by_source={"test_src": 1},
            coverage_gaps=[gap],
            loss_indicators={"events_dropped": 0, "buffer_overflows": 0, "truncations": 0, "notes": []},
            events=[ev],
        )

        self.storage.save_monitoring_session(session)

        # Retrieve session
        loaded = self.storage.get_monitoring_session("mon-pers-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.session_id, "mon-pers-1")
        self.assertEqual(loaded.status, MonitoringSessionStatus.COMPLETED)
        self.assertEqual(len(loaded.events), 1)
        self.assertEqual(loaded.events[0].event_code, "TEST_CODE")
        self.assertEqual(loaded.events[0].process_id, 456)
        self.assertEqual(loaded.events[0].process_provenance, ProcessProvenance.EVENT_PAYLOAD)
        self.assertEqual(len(loaded.coverage_gaps), 1)
        self.assertEqual(loaded.coverage_gaps[0].reason, "Test gap reason")

        # List sessions
        sessions = self.storage.list_monitoring_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].session_id, "mon-pers-1")

        # Get events
        evs = self.storage.get_monitoring_events("mon-pers-1")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].event_id, "evt-pers-1")


if __name__ == "__main__":
    unittest.main()
