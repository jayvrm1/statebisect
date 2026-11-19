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
            self.assertEqual(storage.get_current_schema_version(), 10)

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
            self.assertIn("investigation_candidates", tables)
            self.assertIn("candidate_reviews", tables)
            self.assertIn("registered_environments", tables)
            self.assertIn("registered_baselines", tables)
            self.assertIn("isolated_trials", tables)
            self.assertIn("verification_runs", tables)
            self.assertIn("verification_trials", tables)
            self.assertIn("ai_explanations", tables)
            self.assertIn("benchmark_runs", tables)
            self.assertIn("benchmark_results", tables)
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

        # 2. Open with StateBisect Storage (migrating to v8)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 10)

            # 3. Verify existing legacy data is preserved intact
            inv = storage.get_investigation("phase1-inv")
            self.assertIsNotNone(inv)
            self.assertEqual(inv.symptom, "Phase 1 legacy issue")
            self.assertEqual(inv.target_system, "LegacyApp")
            self.assertEqual(inv.scenario_id, "")  # Default value populated
            self.assertEqual(inv.onset_timezone, "UTC")

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

        # 2. Open with Storage (applies v4, v5, v6, v7, v8)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 10)
            # Verify Phase 3 evidence preserved
            cursor = storage._conn.execute(
                "SELECT status FROM health_check_evidence WHERE trial_id = 't-1'"
            )
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "PASS")

            # Verify v4, v5, v6 tables exist and are functional
            runs = storage.list_collection_runs()
            self.assertEqual(len(runs), 0)
            sessions = storage.list_monitoring_sessions()
            self.assertEqual(len(sessions), 0)
            candidates = storage.get_candidates("non-existent")
            self.assertEqual(len(candidates), 0)
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

        # 2. Open with Storage (applies v5, v6, v7, v8)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 10)
            # Verify Phase 4 run preserved
            runs = storage.list_collection_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].run_id, "coll-legacy")

            # Verify v5 tables exist
            sessions = storage.list_monitoring_sessions()
            self.assertEqual(len(sessions), 0)
        finally:
            storage.close()

    def test_migrate_phase5_to_phase6_candidates_tables(self):
        # 1. Create a schema up to v5
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
                INSERT INTO schema_migrations VALUES (5, '2026-11-17T00:00:00Z', 'Phase 5');

                CREATE TABLE investigations (
                    id TEXT PRIMARY KEY,
                    symptom TEXT NOT NULL,
                    target_system TEXT NOT NULL,
                    status TEXT NOT NULL,
                    baseline_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    coverage_limitations TEXT,
                    scenario_id TEXT,
                    scenario_hash TEXT,
                    scenario_snapshot TEXT,
                    engine_version TEXT
                );
                INSERT INTO investigations (id, symptom, target_system, status, baseline_id, created_at, updated_at)
                VALUES ('inv-p5', 'Test v5 symptom', 'TargetApp', 'NEW', 'base-1', '2026-11-17T00:00:00Z', '2026-11-17T00:00:00Z');
                """
            )
        conn.close()

        # 2. Open with Storage (applies v6, v7, v8)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 10)
            inv = storage.get_investigation("inv-p5")
            self.assertIsNotNone(inv)
            self.assertEqual(inv.symptom, "Test v5 symptom")
            self.assertEqual(inv.onset_timezone, "UTC")
            self.assertEqual(inv.ranking_version, "1.0.0")

            candidates = storage.get_candidates("inv-p5")
            self.assertEqual(len(candidates), 0)
        finally:
            storage.close()

    def test_migrate_phase6_to_phase7_isolated_adapters(self):
        # 1. Create a schema up to v6
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
                INSERT INTO schema_migrations VALUES (5, '2026-11-17T00:00:00Z', 'Phase 5');
                INSERT INTO schema_migrations VALUES (6, '2026-11-17T00:00:00Z', 'Phase 6');

                CREATE TABLE investigations (
                    id TEXT PRIMARY KEY,
                    symptom TEXT NOT NULL,
                    target_system TEXT NOT NULL,
                    status TEXT NOT NULL,
                    baseline_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    coverage_limitations TEXT,
                    scenario_id TEXT,
                    scenario_hash TEXT,
                    scenario_snapshot TEXT,
                    engine_version TEXT,
                    failure_onset_timestamp TEXT,
                    onset_timezone TEXT DEFAULT 'UTC',
                    ranking_version TEXT DEFAULT '1.0.0',
                    correlation_window_start TEXT,
                    correlation_window_end TEXT
                );
                INSERT INTO investigations (id, symptom, target_system, status, baseline_id, created_at, updated_at)
                VALUES ('inv-p6', 'Test v6 symptom', 'TargetApp', 'NEW', 'base-1', '2026-11-17T00:00:00Z', '2026-11-17T00:00:00Z');
                """
            )
        conn.close()

        # 2. Open with Storage (applies v7, v8)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 10)

            # Verify tables exist
            cursor = storage._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cursor.fetchall()}
            self.assertIn("registered_environments", tables)
            self.assertIn("registered_baselines", tables)
            self.assertIn("isolated_trials", tables)
            self.assertIn("verification_runs", tables)
            self.assertIn("verification_trials", tables)

            # Verify storage operations
            from statebisect.adapters.models import (
                AdapterType,
                EnvironmentRecord,
                EnvironmentStatus,
                BaselineRecord,
                IsolatedTrialRecord,
                TrialLifecycleState,
            )
            env = EnvironmentRecord(
                environment_id="test-env-1",
                adapter_type=AdapterType.SIMULATION,
                display_name="Test Env",
                is_disposable=True,
                is_host=False,
                status=EnvironmentStatus.REGISTERED,
            )
            storage.register_environment(env)
            fetched_env = storage.get_environment("test-env-1")
            self.assertIsNotNone(fetched_env)
            self.assertEqual(fetched_env.display_name, "Test Env")

            base = BaselineRecord(
                baseline_id="b-1",
                environment_id="test-env-1",
                display_name="Clean Base",
                checksum_or_state="chk-1",
            )
            storage.register_baseline(base)
            fetched_base = storage.get_baseline("test-env-1", "b-1")
            self.assertIsNotNone(fetched_base)
            self.assertEqual(fetched_base.checksum_or_state, "chk-1")
        finally:
            storage.close()

    def test_migrate_phase7_to_phase8_verification_tables(self):
        # 1. Setup schema up to v7
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                INSERT INTO schema_migrations VALUES (7, datetime('now'), 'v7');
                CREATE TABLE investigations (id TEXT PRIMARY KEY, symptom TEXT, target_system TEXT, status TEXT, baseline_id TEXT, created_at TEXT, updated_at TEXT);
                INSERT INTO investigations VALUES ('inv-8', 'Sym', 'Target', 'NEW', 'b-1', '2026-11-17T00:00:00Z', '2026-11-17T00:00:00Z');
                """
            )
        conn.close()

        # 2. Open with Storage (applies v8)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 10)
            cursor = storage._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cursor.fetchall()}
            self.assertIn("verification_runs", tables)
            self.assertIn("verification_trials", tables)

            from statebisect.verification.models import (
                VerificationRunRecord,
                VerificationTrialRecord,
                VerificationVerdict,
                TrialRole,
                VerificationNecessityDetail,
            )
            from statebisect.models import HealthCheckOutcome

            run_rec = VerificationRunRecord(
                verification_id="verif-test-1",
                investigation_id="inv-8",
                environment_id="sim-env-default",
                baseline_id="b-1",
                verdict=VerificationVerdict.VERIFIED_SET,
                candidate_ids=["c1"],
                is_1_minimal=True,
                baseline_control_passed=True,
                candidate_set_reproduced=True,
                necessity_details=[
                    VerificationNecessityDetail(
                        change_id="c1",
                        is_necessary=True,
                        outcome_without_change=HealthCheckOutcome.PASS,
                        explanation="Omitting restored PASS",
                    )
                ],
                confidence_rationale="Empirically verified",
            )
            trial_rec = VerificationTrialRecord(
                trial_id="tr-v1",
                verification_id="verif-test-1",
                trial_role=TrialRole.CANDIDATE_SET,
                target_change_ids=["c1"],
                outcome=HealthCheckOutcome.FAIL,
            )
            run_rec.trials.append(trial_rec)

            storage.save_verification_run(run_rec)
            fetched = storage.get_verification_run("verif-test-1")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.verdict, VerificationVerdict.VERIFIED_SET)
            self.assertEqual(len(fetched.trials), 1)
            self.assertEqual(fetched.trials[0].trial_role, TrialRole.CANDIDATE_SET)
            self.assertEqual(len(fetched.necessity_details), 1)
            self.assertTrue(fetched.necessity_details[0].is_necessary)
        finally:
            storage.close()

    def test_migrate_phase8_to_phase9_ai_explanations_table(self):
        # 1. Setup schema up to v8
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                INSERT INTO schema_migrations VALUES (8, datetime('now'), 'v8');
                CREATE TABLE investigations (id TEXT PRIMARY KEY, symptom TEXT, target_system TEXT, status TEXT, baseline_id TEXT, created_at TEXT, updated_at TEXT);
                INSERT INTO investigations VALUES ('inv-9', 'Sym', 'Target', 'NEW', 'b-1', '2026-11-17T00:00:00Z', '2026-11-17T00:00:00Z');
                """
            )
        conn.close()

        # 2. Open with Storage (applies v9)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 10)
            cursor = storage._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cursor.fetchall()}
            self.assertIn("ai_explanations", tables)

            from statebisect.ai.models import AiExplanationRecord, ExplanationStatus
            rec = AiExplanationRecord(
                explanation_id="exp-mig-1",
                investigation_id="inv-9",
                provider_id="mock",
                model_id="mock-v1",
                api_version="v1",
                prompt_template_version="1.0.0",
                question="Why did it fail?",
                context_hash="hash123",
                evidence_ids=["ev-1"],
                is_remote=False,
                user_confirmed_remote=False,
                status=ExplanationStatus.SUCCESS,
                short_answer="Clean answer",
                structured_response={"key": "val"},
                duration_ms=15.0,
            )
            storage.save_ai_explanation(rec)
            fetched = storage.get_ai_explanation("exp-mig-1")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.explanation_id, "exp-mig-1")
            self.assertEqual(fetched.status, ExplanationStatus.SUCCESS)
            self.assertEqual(len(storage.list_ai_explanations("inv-9")), 1)
        finally:
            storage.close()

    def test_migrate_phase9_to_phase10_benchmark_tables(self):
        # 1. Setup schema up to v9
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                INSERT INTO schema_migrations VALUES (9, datetime('now'), 'v9');
                CREATE TABLE investigations (id TEXT PRIMARY KEY, symptom TEXT, target_system TEXT, status TEXT, baseline_id TEXT, created_at TEXT, updated_at TEXT);
                INSERT INTO investigations VALUES ('inv-10', 'Sym', 'Target', 'NEW', 'b-1', '2026-11-17T00:00:00Z', '2026-11-17T00:00:00Z');
                """
            )
        conn.close()

        # 2. Open with Storage (applies v10)
        storage = Storage(self.db_path)
        try:
            self.assertEqual(storage.get_current_schema_version(), 10)
            cursor = storage._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cursor.fetchall()}
            self.assertIn("benchmark_runs", tables)
            self.assertIn("benchmark_results", tables)

            from statebisect.benchmark import BenchmarkRunner
            runner = BenchmarkRunner()
            record = runner.run_suite()
            storage.save_benchmark_run(record)

            fetched = storage.get_benchmark_run(record.run_id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.run_id, record.run_id)
            self.assertEqual(len(fetched.scenario_results), 15)
        finally:
            storage.close()

    def test_migrations_are_repeat_safe(self):
        storage = Storage(self.db_path)
        try:
            storage._apply_migrations()
            storage._apply_migrations()
            self.assertEqual(storage.get_current_schema_version(), 10)
        finally:
            storage.close()


if __name__ == "__main__":
    unittest.main()
