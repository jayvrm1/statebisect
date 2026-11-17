"""Tests for Phase 4 read-only Windows Collector MVP, Sources, Differ, and Storage."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from statebisect.collector import (
    BaseCollectionSource,
    CollectionResult,
    CollectionRun,
    CoverageGap,
    DiffChangeType,
    DriversSource,
    EventLogsSource,
    InstalledSoftwareSource,
    NetworkSource,
    OsInfoSource,
    PlatformNotSupportedError,
    ServicesSource,
    SnapshotDiff,
    SnapshotRecord,
    SourceStatus,
    WindowsCollector,
    WindowsUpdatesSource,
    compare_snapshots,
)
from statebisect.storage import Storage


class DummyFailingSource(BaseCollectionSource):
    def __init__(self) -> None:
        super().__init__(
            source_id="failing_dummy",
            category="dummy",
            description="Failing dummy source",
        )

    def _collect_internal(self, run_id: str, lookback_sec: int, max_records: int) -> CollectionResult:
        raise PermissionError("Access is denied to dummy resource")


class DummyHealthySource(BaseCollectionSource):
    def __init__(self) -> None:
        super().__init__(
            source_id="healthy_dummy",
            category="dummy",
            description="Healthy dummy source",
        )

    def _collect_internal(self, run_id: str, lookback_sec: int, max_records: int) -> CollectionResult:
        rec = SnapshotRecord(
            record_id="dummy-1",
            run_id=run_id,
            category=self.category,
            object_key="item-1",
            data={"version": "1.0"},
            timestamp="2026-11-17T00:00:00Z",
            source_id=self.source_id,
        )
        return CollectionResult(
            source_id=self.source_id,
            category=self.category,
            status=SourceStatus.SUCCESS,
            records=[rec],
        )


class TestCollectorSafetyAndMetadata(unittest.TestCase):
    def test_read_only_declaration_and_version(self):
        collector = WindowsCollector(allow_non_windows=True)
        self.assertTrue(collector.read_only_declaration)
        self.assertEqual(collector.version, "1.0.0")

    def test_non_windows_refusal_by_default(self):
        with patch("sys.platform", "linux"):
            with self.assertRaises(PlatformNotSupportedError) as ctx:
                WindowsCollector(allow_non_windows=False)
            self.assertIn("requires Microsoft Windows", str(ctx.exception))

    def test_allow_non_windows_flag_bypasses_check(self):
        with patch("sys.platform", "linux"):
            collector = WindowsCollector(allow_non_windows=True)
            self.assertIsNotNone(collector)

    def test_collect_preview_dry_run(self):
        collector = WindowsCollector(allow_non_windows=True)
        preview = collector.preview()
        self.assertIsInstance(preview, dict)
        self.assertIn("sources", preview)
        self.assertGreaterEqual(len(preview["sources"]), 7)
        source_ids = {p["source_id"] for p in preview["sources"]}
        self.assertIn("os_info", source_ids)
        self.assertIn("installed_software", source_ids)
        self.assertIn("windows_updates", source_ids)
        self.assertIn("services", source_ids)
        self.assertIn("drivers", source_ids)
        self.assertIn("network", source_ids)
        self.assertIn("event_logs", source_ids)


class TestCollectorSources(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Requires Windows host")
    def test_os_info_source_real_windows(self):
        source = OsInfoSource()
        res = source.collect()
        self.assertEqual(res.status, SourceStatus.SUCCESS)
        self.assertEqual(len(res.coverage_gaps), 0)
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertEqual(rec.category, "os")
        self.assertIn("build", rec.data)
        self.assertIn("machine_id", rec.data)
        self.assertTrue(rec.data["machine_id"].startswith("machine-"))
        self.assertEqual(len(rec.data["machine_id"]), 24)  # "machine-" + 16 hex chars

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows host")
    def test_installed_software_source_real_windows(self):
        source = InstalledSoftwareSource()
        res = source.collect(max_records=50)
        self.assertIn(res.status, [SourceStatus.SUCCESS, SourceStatus.PARTIAL])
        self.assertGreater(len(res.records), 0)
        rec = res.records[0]
        self.assertEqual(rec.category, "software")
        self.assertTrue(rec.object_key)

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows host")
    def test_services_and_drivers_sources_real_windows(self):
        services_src = ServicesSource()
        s_res = services_src.collect(max_records=10)
        self.assertEqual(s_res.status, SourceStatus.SUCCESS)
        self.assertGreater(len(s_res.records), 0)
        self.assertEqual(s_res.records[0].category, "service")

        drivers_src = DriversSource()
        d_res = drivers_src.collect(max_records=10)
        self.assertEqual(d_res.status, SourceStatus.SUCCESS)
        self.assertGreater(len(d_res.records), 0)
        self.assertEqual(d_res.records[0].category, "driver")

    def test_event_logs_security_log_exclusion(self):
        source = EventLogsSource()
        # Default channels must NOT include Security log
        self.assertNotIn("Security", source.channels)
        self.assertIn("Application", source.channels)
        self.assertIn("System", source.channels)

    def test_permission_error_yields_coverage_gap_without_raising(self):
        source = DummyFailingSource()
        res = source.collect()
        self.assertEqual(len(res.records), 0)
        self.assertEqual(len(res.coverage_gaps), 1)
        gap = res.coverage_gaps[0]
        self.assertEqual(gap.source_id, "failing_dummy")
        self.assertEqual(gap.category, "dummy")
        self.assertIn("Access denied", gap.reason)
        self.assertEqual(res.status, SourceStatus.UNAVAILABLE)


class TestSnapshotDiffer(unittest.TestCase):
    def setUp(self):
        self.rec1 = SnapshotRecord(
            record_id="rec-1",
            run_id="run-1",
            category="software",
            object_key="AppA",
            data={"version": "1.0.0"},
            timestamp="2026-11-17T00:00:00Z",
            source_id="software",
        )
        self.rec2 = SnapshotRecord(
            record_id="rec-2",
            run_id="run-1",
            category="software",
            object_key="AppB",
            data={"version": "2.0.0"},
            timestamp="2026-11-17T00:00:00Z",
            source_id="software",
        )

    def test_diff_identical_snapshots(self):
        run_a = CollectionRun(
            run_id="run-a",
            created_at="2026-11-17T00:00:00Z",
            finished_at="2026-11-17T00:00:01Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="SUCCESS",
            duration_ms=10.0,
            records=[self.rec1, self.rec2],
        )
        run_b = CollectionRun(
            run_id="run-b",
            created_at="2026-11-17T00:00:02Z",
            finished_at="2026-11-17T00:00:03Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="SUCCESS",
            duration_ms=10.0,
            records=[self.rec1, self.rec2],
        )

        diffs = compare_snapshots(run_a, run_b)
        self.assertEqual(len(diffs), 0)

    def test_diff_added_removed_modified(self):
        rec1_modified = SnapshotRecord(
            record_id="rec-1m",
            run_id="run-2",
            category="software",
            object_key="AppA",
            data={"version": "1.1.0"},
            timestamp="2026-11-17T00:00:02Z",
            source_id="software",
        )
        rec3 = SnapshotRecord(
            record_id="rec-3",
            run_id="run-2",
            category="software",
            object_key="AppC",
            data={"version": "3.0.0"},
            timestamp="2026-11-17T00:00:02Z",
            source_id="software",
        )

        run_before = CollectionRun(
            run_id="run-1",
            created_at="2026-11-17T00:00:00Z",
            finished_at="2026-11-17T00:00:01Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="SUCCESS",
            duration_ms=10.0,
            records=[self.rec1, self.rec2],
        )
        # After: AppA is modified, AppB is removed, AppC is added
        run_after = CollectionRun(
            run_id="run-2",
            created_at="2026-11-17T00:00:02Z",
            finished_at="2026-11-17T00:00:03Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="SUCCESS",
            duration_ms=10.0,
            records=[rec1_modified, rec3],
        )

        diffs = compare_snapshots(run_before, run_after)
        self.assertEqual(len(diffs), 3)

        diff_by_key = {d.object_key: d for d in diffs}
        self.assertEqual(diff_by_key["AppA"].change_type, DiffChangeType.MODIFIED)
        self.assertEqual(diff_by_key["AppB"].change_type, DiffChangeType.REMOVED)
        self.assertEqual(diff_by_key["AppC"].change_type, DiffChangeType.ADDED)

    def test_diff_unknown_when_coverage_gap_exists(self):
        gap = CoverageGap(
            gap_id="gap-sw",
            run_id="run-1",
            source_id="installed_software",
            category="software",
            reason="Access denied reading HKCU uninstall hive",
            impact="Partial inventory",
        )
        run_before = CollectionRun(
            run_id="run-1",
            created_at="2026-11-17T00:00:00Z",
            finished_at="2026-11-17T00:00:01Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="PARTIAL",
            duration_ms=10.0,
            records=[self.rec1],
            coverage_gaps=[gap],
        )
        # Second run doesn't have rec1, and category had gap in run_before
        run_after = CollectionRun(
            run_id="run-2",
            created_at="2026-11-17T00:00:02Z",
            finished_at="2026-11-17T00:00:03Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="SUCCESS",
            duration_ms=10.0,
            records=[],
            coverage_gaps=[],
        )

        diffs = compare_snapshots(run_before, run_after)
        # Because category software had a coverage gap in run_before, disappearance of rec1 must be UNKNOWN
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].change_type, DiffChangeType.UNKNOWN)
        self.assertEqual(diffs[0].category, "software")
        self.assertEqual(diffs[0].status, "INCONCLUSIVE")
        self.assertIn("coverage gaps", diffs[0].notes)


class TestCollectorStorageIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "collector_test.db"
        self.storage = Storage(self.db_path)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_save_and_retrieve_collection_run(self):
        gap = CoverageGap(
            gap_id="gap-001",
            run_id="coll-test-1",
            source_id="test_src",
            category="test_cat",
            reason="Simulated access limitation",
            impact="Omitted test records",
        )
        rec = SnapshotRecord(
            record_id="rec-001",
            run_id="coll-test-1",
            category="test_cat",
            object_key="item-1",
            data={"key": "val"},
            timestamp="2026-11-17T00:00:00Z",
            source_id="test_src",
        )
        run = CollectionRun(
            run_id="coll-test-1",
            created_at="2026-11-17T00:00:00Z",
            finished_at="2026-11-17T00:00:01Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="PARTIAL",
            duration_ms=10.0,
            records=[rec],
            coverage_gaps=[gap],
        )

        self.storage.save_collection_run(run)

        loaded_run = self.storage.get_collection_run("coll-test-1")
        self.assertIsNotNone(loaded_run)
        self.assertEqual(loaded_run.run_id, "coll-test-1")
        self.assertEqual(loaded_run.status, "PARTIAL")
        self.assertEqual(len(loaded_run.records), 1)
        self.assertEqual(loaded_run.records[0].object_key, "item-1")
        self.assertEqual(len(loaded_run.coverage_gaps), 1)
        self.assertEqual(loaded_run.coverage_gaps[0].reason, "Simulated access limitation")

        runs = self.storage.list_collection_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_id, "coll-test-1")

    def test_save_and_retrieve_snapshot_diffs(self):
        run_a = CollectionRun(
            run_id="run-a",
            created_at="2026-11-17T00:00:00Z",
            finished_at="2026-11-17T00:00:01Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="SUCCESS",
            duration_ms=10.0,
        )
        run_b = CollectionRun(
            run_id="run-b",
            created_at="2026-11-17T00:00:02Z",
            finished_at="2026-11-17T00:00:03Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="machine-1234567890abcdef",
            status="SUCCESS",
            duration_ms=10.0,
        )
        self.storage.save_collection_run(run_a)
        self.storage.save_collection_run(run_b)

        diff = SnapshotDiff(
            diff_id="diff-1",
            before_run_id="run-a",
            after_run_id="run-b",
            category="software",
            object_key="App1",
            change_type=DiffChangeType.MODIFIED,
            before_data={"v": 1},
            after_data={"v": 2},
            status="CONFIRMED",
            notes="Version updated",
        )
        self.storage.save_snapshot_diffs([diff])

        loaded = self.storage.get_snapshot_diffs("run-a", "run-b")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].diff_id, "diff-1")
        self.assertEqual(loaded[0].change_type, DiffChangeType.MODIFIED)
        self.assertEqual(loaded[0].object_key, "App1")


if __name__ == "__main__":
    unittest.main()
