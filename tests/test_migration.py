"""Tests for SQLite schema versioning, migrations, and audit logging."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from statebisect.models import (
    ExperimentTrial,
    HealthCheckOutcome,
    Investigation,
    InvestigationStatus,
)
from statebisect.storage import Storage


class TestDatabaseMigrations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "migration_test.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fresh_schema_initialization(self):
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 5)

            # Check that all tables exist
            cursor = storage._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            self.assertIn("investigations", tables)
            self.assertIn("experiment_trials", tables)
            self.assertIn("schema_migrations", tables)
            self.assertIn("audit_logs", tables)
            self.assertIn("health_check_evidence", tables)
            self.assertIn("collection_runs", tables)
            self.assertIn("snapshot_records", tables)
            self.assertIn("coverage_gaps", tables)
            self.assertIn("snapshot_diffs", tables)
            self.assertIn("monitoring_sessions", tables)
            self.assertIn("monitoring_events", tables)
            self.assertIn("monitoring_coverage_gaps", tables)
        finally:
            storage.close()

    def test_migrate_phase1_database_without_data_loss(self):
        # 1. Create a pure Phase 1 schema manually
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.executescript(
                """
                CREATE TABLE investigations (
                    id TEXT PRIMARY KEY,
                    symptom TEXT NOT NULL,
                    target_system TEXT NOT NULL,
                    status TEXT NOT NULL,
                    baseline_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    coverage_limitations TEXT
                );

                CREATE TABLE experiment_trials (
                    trial_id TEXT PRIMARY KEY,
                    investigation_id TEXT NOT NULL,
                    selected_change_ids TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    finish_time TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    health_output TEXT NOT NULL,
                    reset_status TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    notes TEXT
                );

                INSERT INTO investigations (id, symptom, target_system, status, baseline_id, created_at, updated_at)
                VALUES ('phase1-inv', 'Phase 1 legacy issue', 'LegacyApp', 'NEW', 'base-0', '2026-11-17T00:00:00Z', '2026-11-17T00:00:00Z');

                INSERT INTO experiment_trials (trial_id, investigation_id, selected_change_ids, start_time, finish_time, outcome, health_output, reset_status, duration_ms)
                VALUES ('trial-legacy', 'phase1-inv', '["c1"]', '2026-11-17T00:00:00Z', '2026-11-17T00:00:01Z', 'FAIL', 'Crashed', 'SUCCESS', 10.5);
                """
            )
        conn.close()

        # 2. Open with StateBisect Storage (migrating to v5)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 5)

            # 3. Verify existing legacy data is preserved intact
            inv = storage.get_investigation("phase1-inv")
            self.assertIsNotNone(inv)
            self.assertEqual(inv.symptom, "Phase 1 legacy issue")
            self.assertEqual(inv.target_system, "LegacyApp")
            self.assertEqual(inv.scenario_id, "")  # Default value populated

            trials = storage.get_trials("phase1-inv")
            self.assertEqual(len(trials), 1)
            self.assertEqual(trials[0].trial_id, "trial-legacy")
            self.assertEqual(trials[0].outcome, HealthCheckOutcome.FAIL)

            # 4. Verify audit_logs and health_check_evidence are ready
            storage.log_audit("phase1-inv", "MIGRATION_VERIFIED", "Legacy records verified")
            logs = storage.get_audit_logs("phase1-inv")
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].event_type, "MIGRATION_VERIFIED")
        finally:
            storage.close()

    def test_migrate_phase3_to_phase4_adds_collector_tables(self):
        # 1. Create a schema up to v3
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                INSERT INTO schema_migrations VALUES (1, '2026-11-17T00:00:00Z', 'Phase 1 baseline');
                INSERT INTO schema_migrations VALUES (2, '2026-11-17T00:00:00Z', 'Phase 2 scenarios and audit logs');
                INSERT INTO schema_migrations VALUES (3, '2026-11-17T00:00:00Z', 'Phase 3 health check evidence');

                CREATE TABLE investigations (
                    id TEXT PRIMARY KEY,
                    symptom TEXT NOT NULL,
                    target_system TEXT NOT NULL,
                    status TEXT NOT NULL,
                    baseline_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    coverage_limitations TEXT,
                    scenario_id TEXT
                );
                CREATE TABLE experiment_trials (
                    trial_id TEXT PRIMARY KEY,
                    investigation_id TEXT NOT NULL,
                    selected_change_ids TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    finish_time TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    health_output TEXT NOT NULL,
                    reset_status TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    notes TEXT
                );
                CREATE TABLE audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    investigation_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT NOT NULL
                );
                CREATE TABLE health_check_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trial_id TEXT NOT NULL,
                    attempt_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    stdout_sample TEXT,
                    stderr_sample TEXT,
                    duration_ms REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    error_reason TEXT
                );
                INSERT INTO health_check_evidence (trial_id, attempt_index, status, duration_ms, timestamp)
                VALUES ('t-1', 1, 'PASS', 15.0, '2026-11-17T00:00:00Z');
                """
            )
        conn.close()

        # 2. Open with Storage (applies v4)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 5)
            # Verify Phase 3 evidence preserved
            cursor = storage._conn.execute(
                "SELECT status FROM health_check_evidence WHERE trial_id = 't-1'"
            )
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "PASS")

            # Verify v4 and v5 tables exist and are functional
            runs = storage.list_collection_runs()
            self.assertEqual(len(runs), 0)
            sessions = storage.list_monitoring_sessions()
            self.assertEqual(len(sessions), 0)
        finally:
            storage.close()

    def test_migrate_phase4_to_phase5_monitoring_tables(self):
        # 1. Create a schema up to v4
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                INSERT INTO schema_migrations VALUES (1, '2026-11-17T00:00:00Z', 'Phase 1');
                INSERT INTO schema_migrations VALUES (2, '2026-11-17T00:00:00Z', 'Phase 2');
                INSERT INTO schema_migrations VALUES (3, '2026-11-17T00:00:00Z', 'Phase 3');
                INSERT INTO schema_migrations VALUES (4, '2026-11-17T00:00:00Z', 'Phase 4');

                CREATE TABLE collection_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    collector_version TEXT NOT NULL,
                    os_platform TEXT NOT NULL,
                    machine_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    summary_json TEXT
                );
                INSERT INTO collection_runs VALUES ('coll-legacy', '2026-11-17T00:00:00Z', '2026-11-17T00:00:01Z', '1.0.0', 'Windows', 'mach-1', 'SUCCESS', 12.0, '{}');
                """
            )
        conn.close()

        # 2. Open with Storage (applies v5)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 5)
            # Verify Phase 4 run preserved
            runs = storage.list_collection_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].run_id, "coll-legacy")

            # Verify v5 tables exist
            sessions = storage.list_monitoring_sessions()
            self.assertEqual(len(sessions), 0)
        finally:
            storage.close()

    def test_migrations_are_repeat_safe(self):
        storage = Storage(self.db_path)
        try:
            storage._apply_migrations()
            storage._apply_migrations()
            self.assertEqual(storage.get_current_schema_version(), 5)
        finally:
            storage.close()


if __name__ == "__main__":
    unittest.main()
