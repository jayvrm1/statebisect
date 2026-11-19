"""SQLite persistence layer with schema migrations, audit logging, and collection snapshots."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .collector.models import (
    CollectionRun,
    CoverageGap,
    DiffChangeType,
    SnapshotDiff,
    SnapshotRecord,
)
from .models import (
    AuditLogEntry,
    ENGINE_VERSION,
    ExperimentTrial,
    HealthCheckAttempt,
    HealthCheckOutcome,
    Investigation,
    InvestigationStatus,
    utc_now_iso,
)
from .correlation.models import (
    CandidateChange,
    CandidateEvidenceType,
    CandidateReversibility,
    CandidateReviewRecord,
    CandidateReviewStatus,
    ScoreBreakdown,
)
from .adapters.models import (
    AdapterType,
    BaselineRecord,
    EnvironmentRecord,
    EnvironmentStatus,
    IsolatedTrialRecord,
    TrialLifecycleState,
)
from .verification.models import (
    TrialRole,
    VerificationNecessityDetail,
    VerificationRunRecord,
    VerificationTrialRecord,
    VerificationVerdict,
)
from .monitor.models import (
    EventConfidence,
    MonitoringCoverageGap,
    MonitoringEvent,
    MonitoringSession,
    MonitoringSessionStatus,
    ProcessProvenance,
)

LATEST_SCHEMA_VERSION = 8


class Storage:
    """Manages SQLite storage, migrations, audit logging, and snapshots for StateBisect."""

    def __init__(self, db_path: str | Path = ":memory:", check_same_thread: bool = False) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._apply_migrations()

    def close(self) -> None:
        """Close SQLite database connection."""
        if self._conn:
            self._conn.close()

    def get_current_schema_version(self) -> int:
        """Get highest applied schema version."""
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if not cursor.fetchone():
            return 0
        row = self._conn.execute("SELECT MAX(version) as max_v FROM schema_migrations").fetchone()
        return row["max_v"] if row and row["max_v"] is not None else 0

    def _apply_migrations(self) -> None:
        """Execute transactional, repeat-safe schema migrations."""
        with self._conn:
            # 1. Ensure schema_migrations table exists
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                """
            )

            current_ver = self.get_current_schema_version()

            # Migration 1: Initial Phase 1 tables
            if current_ver < 1:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS investigations (
                        id TEXT PRIMARY KEY,
                        symptom TEXT NOT NULL,
                        target_system TEXT NOT NULL,
                        status TEXT NOT NULL,
                        baseline_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        coverage_limitations TEXT
                    );

                    CREATE TABLE IF NOT EXISTS experiment_trials (
                        trial_id TEXT PRIMARY KEY,
                        investigation_id TEXT NOT NULL,
                        selected_change_ids TEXT NOT NULL,
                        start_time TEXT NOT NULL,
                        finish_time TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        health_output TEXT NOT NULL,
                        reset_status TEXT NOT NULL,
                        duration_ms REAL NOT NULL,
                        notes TEXT,
                        FOREIGN KEY (investigation_id) REFERENCES investigations(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_trials_investigation 
                    ON experiment_trials(investigation_id);

                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (1, datetime('now'), 'Initial schema for investigations and trials');
                    """
                )
                current_ver = 1

            # Migration 2: Auditability, scenario snapshots, and structured logs
            if current_ver < 2:
                cols_cursor = self._conn.execute("PRAGMA table_info(investigations)")
                existing_cols = {row["name"] for row in cols_cursor.fetchall()}

                if "scenario_id" not in existing_cols:
                    self._conn.execute("ALTER TABLE investigations ADD COLUMN scenario_id TEXT DEFAULT ''")
                if "scenario_hash" not in existing_cols:
                    self._conn.execute("ALTER TABLE investigations ADD COLUMN scenario_hash TEXT DEFAULT ''")
                if "scenario_snapshot" not in existing_cols:
                    self._conn.execute("ALTER TABLE investigations ADD COLUMN scenario_snapshot TEXT DEFAULT ''")
                if "engine_version" not in existing_cols:
                    self._conn.execute("ALTER TABLE investigations ADD COLUMN engine_version TEXT DEFAULT '0.2.0'")

                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        log_id TEXT PRIMARY KEY,
                        investigation_id TEXT,
                        timestamp TEXT NOT NULL,
                        level TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        message TEXT NOT NULL,
                        details_json TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_audit_investigation 
                    ON audit_logs(investigation_id);

                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (2, datetime('now'), 'Add scenario snapshots, reproducibility columns, and audit logs');
                    """
                )
                current_ver = 2

            # Migration 3: Phase 3 Health Check Evidence model
            if current_ver < 3:
                trials_cursor = self._conn.execute("PRAGMA table_info(experiment_trials)")
                existing_trial_cols = {row["name"] for row in trials_cursor.fetchall()}

                if "check_type" not in existing_trial_cols:
                    self._conn.execute("ALTER TABLE experiment_trials ADD COLUMN check_type TEXT DEFAULT ''")
                if "check_version" not in existing_trial_cols:
                    self._conn.execute("ALTER TABLE experiment_trials ADD COLUMN check_version TEXT DEFAULT '1.0.0'")
                if "parameters_hash" not in existing_trial_cols:
                    self._conn.execute("ALTER TABLE experiment_trials ADD COLUMN parameters_hash TEXT DEFAULT ''")

                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS health_check_evidence (
                        evidence_id TEXT PRIMARY KEY,
                        trial_id TEXT NOT NULL,
                        investigation_id TEXT NOT NULL,
                        check_type TEXT NOT NULL,
                        check_version TEXT NOT NULL,
                        parameters_hash TEXT NOT NULL,
                        attempt_number INTEGER NOT NULL,
                        outcome TEXT NOT NULL,
                        exit_code INTEGER,
                        duration_ms REAL NOT NULL,
                        expected_value TEXT,
                        observed_value TEXT,
                        bounded_evidence TEXT,
                        error_reason TEXT,
                        is_truncated INTEGER NOT NULL,
                        is_redacted INTEGER NOT NULL,
                        start_time TEXT NOT NULL,
                        finish_time TEXT NOT NULL,
                        FOREIGN KEY (trial_id) REFERENCES experiment_trials(trial_id),
                        FOREIGN KEY (investigation_id) REFERENCES investigations(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_evidence_trial 
                    ON health_check_evidence(trial_id);

                    CREATE INDEX IF NOT EXISTS idx_evidence_investigation 
                    ON health_check_evidence(investigation_id);

                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (3, datetime('now'), 'Add detailed health check evidence attempts tracking');
                    """
                )
                current_ver = 3

            # Migration 4: Phase 4 Windows Collector Snapshots, Gaps, and Diffs
            if current_ver < 4:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS collection_runs (
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

                    CREATE TABLE IF NOT EXISTS snapshot_records (
                        record_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        category TEXT NOT NULL,
                        object_key TEXT NOT NULL,
                        data_json TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        is_redacted INTEGER NOT NULL,
                        is_truncated INTEGER NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES collection_runs(run_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_snapshot_records_run 
                    ON snapshot_records(run_id);

                    CREATE INDEX IF NOT EXISTS idx_snapshot_records_key 
                    ON snapshot_records(object_key);

                    CREATE TABLE IF NOT EXISTS coverage_gaps (
                        gap_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        category TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        impact TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES collection_runs(run_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_coverage_gaps_run 
                    ON coverage_gaps(run_id);

                    CREATE TABLE IF NOT EXISTS snapshot_diffs (
                        diff_id TEXT PRIMARY KEY,
                        before_run_id TEXT NOT NULL,
                        after_run_id TEXT NOT NULL,
                        category TEXT NOT NULL,
                        object_key TEXT NOT NULL,
                        change_type TEXT NOT NULL,
                        before_json TEXT,
                        after_json TEXT,
                        status TEXT NOT NULL,
                        notes TEXT,
                        FOREIGN KEY (before_run_id) REFERENCES collection_runs(run_id),
                        FOREIGN KEY (after_run_id) REFERENCES collection_runs(run_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_snapshot_diffs_runs 
                    ON snapshot_diffs(before_run_id, after_run_id);

                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (4, datetime('now'), 'Add Windows collection runs, snapshot records, coverage gaps, and diffs');
                    """
                )
                current_ver = 4

            # Migration 5: Phase 5 Targeted Change Monitoring Sessions, Events, and Gaps
            if current_ver < 5:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS monitoring_sessions (
                        session_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        stopped_at TEXT NOT NULL,
                        duration_sec INTEGER NOT NULL,
                        elapsed_sec REAL NOT NULL,
                        status TEXT NOT NULL,
                        stop_reason TEXT NOT NULL,
                        pre_snapshot_run_id TEXT,
                        post_snapshot_run_id TEXT,
                        sources_requested_json TEXT NOT NULL,
                        sources_active_json TEXT NOT NULL,
                        total_events INTEGER NOT NULL,
                        events_by_source_json TEXT NOT NULL,
                        loss_indicators_json TEXT NOT NULL,
                        FOREIGN KEY (pre_snapshot_run_id) REFERENCES collection_runs(run_id) ON DELETE SET NULL,
                        FOREIGN KEY (post_snapshot_run_id) REFERENCES collection_runs(run_id) ON DELETE SET NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_monitoring_sessions_status 
                    ON monitoring_sessions(status);

                    CREATE TABLE IF NOT EXISTS monitoring_events (
                        event_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        source_version TEXT NOT NULL,
                        category TEXT NOT NULL,
                        timestamp_utc TEXT NOT NULL,
                        source_timestamp TEXT NOT NULL,
                        channel_or_path TEXT NOT NULL,
                        event_code TEXT NOT NULL,
                        process_id INTEGER,
                        process_name TEXT,
                        process_provenance TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        is_redacted INTEGER NOT NULL,
                        is_truncated INTEGER NOT NULL,
                        confidence TEXT NOT NULL,
                        raw_ref TEXT,
                        FOREIGN KEY (session_id) REFERENCES monitoring_sessions(session_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_monitoring_events_session 
                    ON monitoring_events(session_id);

                    CREATE INDEX IF NOT EXISTS idx_monitoring_events_source 
                    ON monitoring_events(source_id);

                    CREATE TABLE IF NOT EXISTS monitoring_coverage_gaps (
                        gap_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        category TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        impact TEXT NOT NULL,
                        FOREIGN KEY (session_id) REFERENCES monitoring_sessions(session_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_monitoring_gaps_session 
                    ON monitoring_coverage_gaps(session_id);

                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (5, datetime('now'), 'Add Phase 5 targeted change monitoring sessions, events, and gaps');
                    """
                )
                current_ver = 5

            # Migration 6: Phase 6 Candidate Change Generation, Ranking, and Audit Reviews
            if current_ver < 6:
                tbl_check = self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='investigations'"
                ).fetchone()
                if tbl_check:
                    inv_cursor = self._conn.execute("PRAGMA table_info(investigations)")
                    existing_inv_cols = {row["name"] for row in inv_cursor.fetchall()}

                    if "onset_time_utc" not in existing_inv_cols:
                        self._conn.execute("ALTER TABLE investigations ADD COLUMN onset_time_utc TEXT")
                    if "onset_time_user" not in existing_inv_cols:
                        self._conn.execute("ALTER TABLE investigations ADD COLUMN onset_time_user TEXT")
                    if "onset_timezone" not in existing_inv_cols:
                        self._conn.execute("ALTER TABLE investigations ADD COLUMN onset_timezone TEXT DEFAULT 'UTC'")
                    if "last_known_good_utc" not in existing_inv_cols:
                        self._conn.execute("ALTER TABLE investigations ADD COLUMN last_known_good_utc TEXT")
                    if "correlation_window_json" not in existing_inv_cols:
                        self._conn.execute("ALTER TABLE investigations ADD COLUMN correlation_window_json TEXT DEFAULT '{}'")
                    if "ranking_version" not in existing_inv_cols:
                        self._conn.execute("ALTER TABLE investigations ADD COLUMN ranking_version TEXT DEFAULT '1.0.0'")
                    if "user_notes" not in existing_inv_cols:
                        self._conn.execute("ALTER TABLE investigations ADD COLUMN user_notes TEXT DEFAULT ''")
                else:
                    self._conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS investigations (
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
                            onset_time_utc TEXT,
                            onset_time_user TEXT,
                            onset_timezone TEXT DEFAULT 'UTC',
                            last_known_good_utc TEXT,
                            correlation_window_json TEXT DEFAULT '{}',
                            ranking_version TEXT DEFAULT '1.0.0',
                            user_notes TEXT DEFAULT ''
                        )
                        """
                    )

                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS investigation_candidates (
                        candidate_id TEXT PRIMARY KEY,
                        investigation_id TEXT NOT NULL,
                        evidence_type TEXT NOT NULL,
                        category TEXT NOT NULL,
                        object_key TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        first_observed_utc TEXT NOT NULL,
                        last_observed_utc TEXT NOT NULL,
                        temporal_offset_sec REAL,
                        before_value_json TEXT,
                        after_value_json TEXT,
                        process_id INTEGER,
                        process_name TEXT,
                        process_provenance TEXT NOT NULL,
                        source_ids_json TEXT NOT NULL,
                        evidence_ids_json TEXT NOT NULL,
                        coverage_gaps_json TEXT NOT NULL,
                        score REAL NOT NULL,
                        score_breakdown_json TEXT NOT NULL,
                        rank INTEGER NOT NULL,
                        review_status TEXT NOT NULL,
                        is_active INTEGER NOT NULL,
                        reversibility TEXT NOT NULL,
                        inclusion_reason TEXT NOT NULL,
                        uncertainty_note TEXT NOT NULL,
                        FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_candidates_investigation 
                    ON investigation_candidates(investigation_id);

                    CREATE INDEX IF NOT EXISTS idx_candidates_inv_rank 
                    ON investigation_candidates(investigation_id, rank);

                    CREATE TABLE IF NOT EXISTS candidate_reviews (
                        review_id TEXT PRIMARY KEY,
                        candidate_id TEXT NOT NULL,
                        investigation_id TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        previous_status TEXT NOT NULL,
                        new_status TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        reviewed_at TEXT NOT NULL,
                        FOREIGN KEY (candidate_id) REFERENCES investigation_candidates(candidate_id) ON DELETE CASCADE,
                        FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_candidate_reviews_candidate 
                    ON candidate_reviews(candidate_id);

                    CREATE INDEX IF NOT EXISTS idx_candidate_reviews_inv 
                    ON candidate_reviews(investigation_id);

                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (6, datetime('now'), 'Add Phase 6 candidate change generation, ranking, and review tracking');
                    """
                )
                current_ver = 6

            # Migration 7: Phase 7 Isolated Experiment Adapters, Environments, and Trials
            if current_ver < 7:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS registered_environments (
                        environment_id TEXT PRIMARY KEY,
                        adapter_type TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        is_disposable INTEGER NOT NULL,
                        is_host INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        baseline_ids_json TEXT NOT NULL,
                        configuration_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_environments_adapter 
                    ON registered_environments(adapter_type);

                    CREATE TABLE IF NOT EXISTS registered_baselines (
                        baseline_id TEXT NOT NULL,
                        environment_id TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        is_verified INTEGER NOT NULL,
                        checksum_or_state TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (environment_id, baseline_id),
                        FOREIGN KEY (environment_id) REFERENCES registered_environments(environment_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS isolated_trials (
                        trial_id TEXT PRIMARY KEY,
                        investigation_id TEXT NOT NULL,
                        environment_id TEXT NOT NULL,
                        baseline_id TEXT NOT NULL,
                        adapter_type TEXT NOT NULL,
                        lifecycle_state TEXT NOT NULL,
                        is_simulation INTEGER NOT NULL,
                        selected_changes_json TEXT NOT NULL,
                        start_time TEXT NOT NULL,
                        finish_time TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        output_bounded TEXT NOT NULL,
                        duration_ms REAL NOT NULL,
                        restore_status TEXT NOT NULL,
                        verify_status TEXT NOT NULL,
                        apply_status TEXT NOT NULL,
                        test_status TEXT NOT NULL,
                        cleanup_status TEXT NOT NULL,
                        error_reason TEXT,
                        evidence_json TEXT NOT NULL,
                        FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_isolated_trials_inv 
                    ON isolated_trials(investigation_id);

                    CREATE INDEX IF NOT EXISTS idx_isolated_trials_env 
                    ON isolated_trials(environment_id);

                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (7, datetime('now'), 'Add Phase 7 isolated environments, baselines, and trial executions');
                    """
                )
                current_ver = 7

            # Migration 8: Phase 8 Verification Runs, Necessity Tracking, and Trial Matrix
            if current_ver < 8:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS verification_runs (
                        verification_id TEXT PRIMARY KEY,
                        investigation_id TEXT NOT NULL,
                        environment_id TEXT NOT NULL,
                        baseline_id TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        protocol_version TEXT NOT NULL,
                        candidate_ids_json TEXT NOT NULL,
                        is_1_minimal INTEGER NOT NULL,
                        is_interaction INTEGER NOT NULL,
                        baseline_control_passed INTEGER NOT NULL,
                        candidate_set_reproduced INTEGER NOT NULL,
                        reversal_tested INTEGER NOT NULL,
                        reversal_passed INTEGER,
                        necessity_json TEXT NOT NULL,
                        differential_json TEXT NOT NULL,
                        rationale TEXT NOT NULL,
                        limitations_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL,
                        FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_verification_runs_inv 
                    ON verification_runs(investigation_id);

                    CREATE TABLE IF NOT EXISTS verification_trials (
                        trial_id TEXT PRIMARY KEY,
                        verification_id TEXT NOT NULL,
                        trial_role TEXT NOT NULL,
                        target_changes_json TEXT NOT NULL,
                        omitted_change_id TEXT,
                        attempt_number INTEGER NOT NULL,
                        environment_id TEXT NOT NULL,
                        baseline_id TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        lifecycle_state TEXT NOT NULL,
                        restore_status TEXT NOT NULL,
                        cleanup_status TEXT NOT NULL,
                        output_bounded TEXT NOT NULL,
                        duration_ms REAL NOT NULL,
                        timestamp TEXT NOT NULL,
                        FOREIGN KEY (verification_id) REFERENCES verification_runs(verification_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_verification_trials_verif 
                    ON verification_trials(verification_id);

                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (8, datetime('now'), 'Add Phase 8 verification runs, necessity tracking, and trial matrix');
                    """
                )
                current_ver = 8

    def log_audit(
        self,
        investigation_id: str,
        event_type: str,
        message: str,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Insert a structured audit log entry."""
        log_id = f"audit-{uuid.uuid4().hex[:12]}"
        timestamp = utc_now_iso()
        details_json = json.dumps(details or {})
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO audit_logs (log_id, investigation_id, timestamp, level, event_type, message, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (log_id, investigation_id, timestamp, level, event_type, message, details_json),
            )

    def get_audit_logs(self, investigation_id: str) -> list[AuditLogEntry]:
        """Fetch audit log entries for an investigation."""
        cursor = self._conn.execute(
            "SELECT * FROM audit_logs WHERE investigation_id = ? ORDER BY timestamp ASC",
            (investigation_id,),
        )
        return [
            AuditLogEntry(
                log_id=r["log_id"],
                investigation_id=r["investigation_id"] or "",
                timestamp=r["timestamp"],
                level=r["level"],
                event_type=r["event_type"],
                message=r["message"],
                details_json=r["details_json"] or "{}",
            )
            for r in cursor.fetchall()
        ]

    def save_investigation(self, inv: Investigation) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO investigations (
                    id, symptom, target_system, status, baseline_id, 
                    created_at, updated_at, coverage_limitations,
                    scenario_id, scenario_hash, scenario_snapshot, engine_version,
                    onset_time_utc, onset_time_user, onset_timezone,
                    last_known_good_utc, correlation_window_json, ranking_version, user_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    symptom=excluded.symptom,
                    target_system=excluded.target_system,
                    status=excluded.status,
                    baseline_id=excluded.baseline_id,
                    updated_at=excluded.updated_at,
                    coverage_limitations=excluded.coverage_limitations,
                    scenario_id=excluded.scenario_id,
                    scenario_hash=excluded.scenario_hash,
                    scenario_snapshot=excluded.scenario_snapshot,
                    engine_version=excluded.engine_version,
                    onset_time_utc=excluded.onset_time_utc,
                    onset_time_user=excluded.onset_time_user,
                    onset_timezone=excluded.onset_timezone,
                    last_known_good_utc=excluded.last_known_good_utc,
                    correlation_window_json=excluded.correlation_window_json,
                    ranking_version=excluded.ranking_version,
                    user_notes=excluded.user_notes
                """,
                (
                    inv.id,
                    inv.symptom,
                    inv.target_system,
                    inv.status.value,
                    inv.baseline_id,
                    inv.created_at,
                    inv.updated_at,
                    inv.coverage_limitations,
                    inv.scenario_id,
                    inv.scenario_hash,
                    inv.scenario_snapshot,
                    inv.engine_version,
                    inv.onset_time_utc,
                    inv.onset_time_user,
                    inv.onset_timezone,
                    inv.last_known_good_utc,
                    inv.correlation_window_json,
                    inv.ranking_version,
                    inv.user_notes,
                ),
            )

    def get_investigation(self, investigation_id: str) -> Investigation | None:
        cursor = self._conn.execute(
            "SELECT * FROM investigations WHERE id = ?", (investigation_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Investigation(
            id=row["id"],
            symptom=row["symptom"],
            target_system=row["target_system"],
            status=InvestigationStatus(row["status"]),
            baseline_id=row["baseline_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            coverage_limitations=row["coverage_limitations"] or "",
            scenario_id=row["scenario_id"] or "",
            scenario_hash=row["scenario_hash"] or "",
            scenario_snapshot=row["scenario_snapshot"] or "",
            engine_version=row["engine_version"] or ENGINE_VERSION,
            onset_time_utc=row["onset_time_utc"] if "onset_time_utc" in row.keys() else None,
            onset_time_user=row["onset_time_user"] if "onset_time_user" in row.keys() else None,
            onset_timezone=row["onset_timezone"] if "onset_timezone" in row.keys() and row["onset_timezone"] else "UTC",
            last_known_good_utc=row["last_known_good_utc"] if "last_known_good_utc" in row.keys() else None,
            correlation_window_json=row["correlation_window_json"] if "correlation_window_json" in row.keys() and row["correlation_window_json"] else "{}",
            ranking_version=row["ranking_version"] if "ranking_version" in row.keys() and row["ranking_version"] else "1.0.0",
            user_notes=row["user_notes"] if "user_notes" in row.keys() and row["user_notes"] else "",
        )

    def list_investigations(self) -> list[Investigation]:
        cursor = self._conn.execute(
            "SELECT * FROM investigations ORDER BY created_at DESC"
        )
        return [
            Investigation(
                id=row["id"],
                symptom=row["symptom"],
                target_system=row["target_system"],
                status=InvestigationStatus(row["status"]),
                baseline_id=row["baseline_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                coverage_limitations=row["coverage_limitations"] or "",
                scenario_id=row["scenario_id"] or "",
                scenario_hash=row["scenario_hash"] or "",
                scenario_snapshot=row["scenario_snapshot"] or "",
                engine_version=row["engine_version"] or ENGINE_VERSION,
                onset_time_utc=row["onset_time_utc"] if "onset_time_utc" in row.keys() else None,
                onset_time_user=row["onset_time_user"] if "onset_time_user" in row.keys() else None,
                onset_timezone=row["onset_timezone"] if "onset_timezone" in row.keys() and row["onset_timezone"] else "UTC",
                last_known_good_utc=row["last_known_good_utc"] if "last_known_good_utc" in row.keys() else None,
                correlation_window_json=row["correlation_window_json"] if "correlation_window_json" in row.keys() and row["correlation_window_json"] else "{}",
                ranking_version=row["ranking_version"] if "ranking_version" in row.keys() and row["ranking_version"] else "1.0.0",
                user_notes=row["user_notes"] if "user_notes" in row.keys() and row["user_notes"] else "",
            )
            for row in cursor.fetchall()
        ]

    def update_investigation_status(self, investigation_id: str, status: InvestigationStatus) -> None:
        """Update the status and updated_at timestamp of an investigation."""
        val = status.value if hasattr(status, "value") else str(status)
        now = utc_now_iso()
        with self._conn:
            self._conn.execute(
                "UPDATE investigations SET status = ?, updated_at = ? WHERE id = ?",
                (val, now, investigation_id),
            )

    def save_evidence_attempt(
        self,
        attempt: HealthCheckAttempt,
        trial_id: str,
        investigation_id: str,
        check_type: str,
        check_version: str,
        parameters_hash: str,
    ) -> None:
        """Persist a single health check attempt record."""
        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO health_check_evidence (
                    evidence_id, trial_id, investigation_id, check_type, check_version,
                    parameters_hash, attempt_number, outcome, exit_code, duration_ms,
                    expected_value, observed_value, bounded_evidence, error_reason,
                    is_truncated, is_redacted, start_time, finish_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    trial_id,
                    investigation_id,
                    check_type,
                    check_version,
                    parameters_hash,
                    attempt.attempt_number,
                    attempt.outcome.value,
                    attempt.exit_code,
                    attempt.duration_ms,
                    attempt.expected_value,
                    attempt.observed_value,
                    attempt.bounded_evidence,
                    attempt.error_reason,
                    1 if attempt.is_truncated else 0,
                    1 if attempt.is_redacted else 0,
                    attempt.start_time,
                    attempt.finish_time,
                ),
            )

    def get_evidence_for_trial(self, trial_id: str) -> list[HealthCheckAttempt]:
        """Fetch all evidence attempts recorded for a trial."""
        cursor = self._conn.execute(
            "SELECT * FROM health_check_evidence WHERE trial_id = ? ORDER BY attempt_number ASC",
            (trial_id,),
        )
        return [
            HealthCheckAttempt(
                attempt_number=r["attempt_number"],
                outcome=HealthCheckOutcome(r["outcome"]),
                start_time=r["start_time"],
                finish_time=r["finish_time"],
                duration_ms=r["duration_ms"],
                exit_code=r["exit_code"],
                expected_value=r["expected_value"] or "",
                observed_value=r["observed_value"] or "",
                bounded_evidence=r["bounded_evidence"] or "",
                error_reason=r["error_reason"] or "",
                is_truncated=bool(r["is_truncated"]),
                is_redacted=bool(r["is_redacted"]),
            )
            for r in cursor.fetchall()
        ]

    def save_trial(self, trial: ExperimentTrial) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO experiment_trials (
                    trial_id, investigation_id, selected_change_ids, start_time,
                    finish_time, outcome, health_output, reset_status, duration_ms, notes,
                    check_type, check_version, parameters_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trial_id) DO UPDATE SET
                    outcome=excluded.outcome,
                    health_output=excluded.health_output,
                    reset_status=excluded.reset_status,
                    duration_ms=excluded.duration_ms,
                    notes=excluded.notes,
                    check_type=excluded.check_type,
                    check_version=excluded.check_version,
                    parameters_hash=excluded.parameters_hash
                """,
                (
                    trial.trial_id,
                    trial.investigation_id,
                    json.dumps(trial.selected_change_ids),
                    trial.start_time,
                    trial.finish_time,
                    trial.outcome.value,
                    trial.health_output,
                    trial.reset_status,
                    trial.duration_ms,
                    trial.notes,
                    trial.check_type,
                    trial.check_version,
                    trial.parameters_hash,
                ),
            )

        for att in trial.attempts:
            self.save_evidence_attempt(
                attempt=att,
                trial_id=trial.trial_id,
                investigation_id=trial.investigation_id,
                check_type=trial.check_type,
                check_version=trial.check_version,
                parameters_hash=trial.parameters_hash,
            )

    def get_trials(self, investigation_id: str) -> list[ExperimentTrial]:
        cursor = self._conn.execute(
            "SELECT * FROM experiment_trials WHERE investigation_id = ? ORDER BY start_time ASC",
            (investigation_id,),
        )
        rows = cursor.fetchall()
        trials = []
        for r in rows:
            trial_id = r["trial_id"]
            attempts = self.get_evidence_for_trial(trial_id)
            trials.append(
                ExperimentTrial(
                    trial_id=trial_id,
                    investigation_id=r["investigation_id"],
                    selected_change_ids=json.loads(r["selected_change_ids"]),
                    start_time=r["start_time"],
                    finish_time=r["finish_time"],
                    outcome=HealthCheckOutcome(r["outcome"]),
                    health_output=r["health_output"],
                    reset_status=r["reset_status"],
                    duration_ms=r["duration_ms"],
                    notes=r["notes"] or "",
                    check_type=r["check_type"] or "",
                    check_version=r["check_version"] or "1.0.0",
                    parameters_hash=r["parameters_hash"] or "",
                    attempts=attempts,
                )
            )
        return trials

    # Phase 4 Collection & Snapshot Persistence
    def save_collection_run(self, run: CollectionRun) -> None:
        """Persist a complete snapshot collection run, its records, and coverage gaps."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO collection_runs (
                    run_id, created_at, finished_at, collector_version,
                    os_platform, machine_id, status, duration_ms, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    duration_ms=excluded.duration_ms,
                    summary_json=excluded.summary_json
                """,
                (
                    run.run_id,
                    run.created_at,
                    run.finished_at,
                    run.collector_version,
                    run.os_platform,
                    run.machine_id,
                    run.status,
                    run.duration_ms,
                    json.dumps(run.summary),
                ),
            )

            # Insert snapshot records in batch
            for rec in run.records:
                self._conn.execute(
                    """
                    INSERT INTO snapshot_records (
                        record_id, run_id, category, object_key, data_json,
                        timestamp, source_id, is_redacted, is_truncated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(record_id) DO NOTHING
                    """,
                    (
                        rec.record_id,
                        run.run_id,
                        rec.category,
                        rec.object_key,
                        json.dumps(rec.data),
                        rec.timestamp,
                        rec.source_id,
                        1 if rec.is_redacted else 0,
                        1 if rec.is_truncated else 0,
                    ),
                )

            # Insert coverage gaps
            for gap in run.coverage_gaps:
                self._conn.execute(
                    """
                    INSERT INTO coverage_gaps (
                        gap_id, run_id, source_id, category, reason, impact
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(gap_id) DO NOTHING
                    """,
                    (
                        gap.gap_id,
                        run.run_id,
                        gap.source_id,
                        gap.category,
                        gap.reason,
                        gap.impact,
                    ),
                )

    def get_collection_run(self, run_id: str) -> CollectionRun | None:
        """Fetch a collection run with all its snapshot records and coverage gaps."""
        row = self._conn.execute(
            "SELECT * FROM collection_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not row:
            return None

        # Fetch records
        rec_rows = self._conn.execute(
            "SELECT * FROM snapshot_records WHERE run_id = ? ORDER BY category, object_key",
            (run_id,),
        ).fetchall()
        records = [
            SnapshotRecord(
                record_id=r["record_id"],
                run_id=r["run_id"],
                category=r["category"],
                object_key=r["object_key"],
                data=json.loads(r["data_json"]),
                timestamp=r["timestamp"],
                source_id=r["source_id"],
                is_redacted=bool(r["is_redacted"]),
                is_truncated=bool(r["is_truncated"]),
            )
            for r in rec_rows
        ]

        # Fetch gaps
        gap_rows = self._conn.execute(
            "SELECT * FROM coverage_gaps WHERE run_id = ?", (run_id,)
        ).fetchall()
        gaps = [
            CoverageGap(
                gap_id=g["gap_id"],
                run_id=g["run_id"],
                source_id=g["source_id"],
                category=g["category"],
                reason=g["reason"],
                impact=g["impact"],
            )
            for g in gap_rows
        ]

        return CollectionRun(
            run_id=row["run_id"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
            collector_version=row["collector_version"],
            os_platform=row["os_platform"],
            machine_id=row["machine_id"],
            status=row["status"],
            duration_ms=row["duration_ms"],
            summary=json.loads(row["summary_json"] or "{}"),
            records=records,
            coverage_gaps=gaps,
        )

    def list_collection_runs(self) -> list[CollectionRun]:
        """List all collection runs without loading all record bodies."""
        rows = self._conn.execute(
            "SELECT * FROM collection_runs ORDER BY created_at DESC"
        ).fetchall()
        return [
            CollectionRun(
                run_id=r["run_id"],
                created_at=r["created_at"],
                finished_at=r["finished_at"],
                collector_version=r["collector_version"],
                os_platform=r["os_platform"],
                machine_id=r["machine_id"],
                status=r["status"],
                duration_ms=r["duration_ms"],
                summary=json.loads(r["summary_json"] or "{}"),
                records=[],
                coverage_gaps=[],
            )
            for r in rows
        ]

    def save_monitoring_session(self, session: MonitoringSession) -> None:
        """Persist a completed or cancelled monitoring session with its events and gaps."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO monitoring_sessions (
                    session_id, created_at, started_at, stopped_at, duration_sec, elapsed_sec,
                    status, stop_reason, pre_snapshot_run_id, post_snapshot_run_id,
                    sources_requested_json, sources_active_json, total_events,
                    events_by_source_json, loss_indicators_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    stopped_at = excluded.stopped_at,
                    elapsed_sec = excluded.elapsed_sec,
                    status = excluded.status,
                    stop_reason = excluded.stop_reason,
                    post_snapshot_run_id = excluded.post_snapshot_run_id,
                    total_events = excluded.total_events,
                    events_by_source_json = excluded.events_by_source_json,
                    loss_indicators_json = excluded.loss_indicators_json
                """,
                (
                    session.session_id,
                    session.created_at,
                    session.started_at,
                    session.stopped_at,
                    session.duration_sec,
                    session.elapsed_sec,
                    session.status.value,
                    session.stop_reason,
                    session.pre_snapshot_run_id,
                    session.post_snapshot_run_id,
                    json.dumps(session.sources_requested),
                    json.dumps(session.sources_active),
                    session.total_events,
                    json.dumps(session.events_by_source),
                    json.dumps(session.loss_indicators),
                ),
            )

            # Persist events
            for ev in session.events:
                self._conn.execute(
                    """
                    INSERT INTO monitoring_events (
                        event_id, session_id, source_id, source_version, category,
                        timestamp_utc, source_timestamp, channel_or_path, event_code,
                        process_id, process_name, process_provenance, payload_json,
                        is_redacted, is_truncated, confidence, raw_ref
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    (
                        ev.event_id,
                        ev.session_id,
                        ev.source_id,
                        ev.source_version,
                        ev.category,
                        ev.timestamp_utc,
                        ev.source_timestamp,
                        ev.channel_or_path,
                        ev.event_code,
                        ev.process_id,
                        ev.process_name,
                        ev.process_provenance.value,
                        json.dumps(ev.payload),
                        1 if ev.is_redacted else 0,
                        1 if ev.is_truncated else 0,
                        ev.confidence.value,
                        ev.raw_ref,
                    ),
                )

            # Persist coverage gaps
            for gap in session.coverage_gaps:
                self._conn.execute(
                    """
                    INSERT INTO monitoring_coverage_gaps (
                        gap_id, session_id, source_id, category, reason, impact
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(gap_id) DO NOTHING
                    """,
                    (
                        gap.gap_id,
                        gap.session_id,
                        gap.source_id,
                        gap.category,
                        gap.reason,
                        gap.impact,
                    ),
                )

    def get_monitoring_session(self, session_id: str) -> MonitoringSession | None:
        """Fetch a monitoring session by ID with its events and coverage gaps."""
        row = self._conn.execute(
            "SELECT * FROM monitoring_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None

        # Fetch events
        ev_rows = self._conn.execute(
            "SELECT * FROM monitoring_events WHERE session_id = ? ORDER BY timestamp_utc ASC",
            (session_id,),
        ).fetchall()
        events = [
            MonitoringEvent(
                event_id=r["event_id"],
                session_id=r["session_id"],
                source_id=r["source_id"],
                source_version=r["source_version"],
                category=r["category"],
                timestamp_utc=r["timestamp_utc"],
                source_timestamp=r["source_timestamp"],
                channel_or_path=r["channel_or_path"],
                event_code=r["event_code"],
                process_id=r["process_id"],
                process_name=r["process_name"],
                process_provenance=ProcessProvenance(r["process_provenance"]),
                payload=json.loads(r["payload_json"]),
                is_redacted=bool(r["is_redacted"]),
                is_truncated=bool(r["is_truncated"]),
                confidence=EventConfidence(r["confidence"]),
                raw_ref=r["raw_ref"] or "",
            )
            for r in ev_rows
        ]

        # Fetch gaps
        gap_rows = self._conn.execute(
            "SELECT * FROM monitoring_coverage_gaps WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        gaps = [
            MonitoringCoverageGap(
                gap_id=g["gap_id"],
                session_id=g["session_id"],
                source_id=g["source_id"],
                category=g["category"],
                reason=g["reason"],
                impact=g["impact"],
            )
            for g in gap_rows
        ]

        return MonitoringSession(
            session_id=row["session_id"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            stopped_at=row["stopped_at"],
            duration_sec=row["duration_sec"],
            elapsed_sec=row["elapsed_sec"],
            status=MonitoringSessionStatus(row["status"]),
            stop_reason=row["stop_reason"],
            pre_snapshot_run_id=row["pre_snapshot_run_id"],
            post_snapshot_run_id=row["post_snapshot_run_id"],
            sources_requested=json.loads(row["sources_requested_json"]),
            sources_active=json.loads(row["sources_active_json"]),
            total_events=row["total_events"],
            events_by_source=json.loads(row["events_by_source_json"]),
            loss_indicators=json.loads(row["loss_indicators_json"]),
            events=events,
            coverage_gaps=gaps,
        )

    def list_monitoring_sessions(self) -> list[MonitoringSession]:
        """List summary of monitoring sessions without loading all individual event bodies."""
        rows = self._conn.execute(
            "SELECT * FROM monitoring_sessions ORDER BY started_at DESC"
        ).fetchall()
        return [
            MonitoringSession(
                session_id=r["session_id"],
                created_at=r["created_at"],
                started_at=r["started_at"],
                stopped_at=r["stopped_at"],
                duration_sec=r["duration_sec"],
                elapsed_sec=r["elapsed_sec"],
                status=MonitoringSessionStatus(r["status"]),
                stop_reason=r["stop_reason"],
                pre_snapshot_run_id=r["pre_snapshot_run_id"],
                post_snapshot_run_id=r["post_snapshot_run_id"],
                sources_requested=json.loads(r["sources_requested_json"]),
                sources_active=json.loads(r["sources_active_json"]),
                total_events=r["total_events"],
                events_by_source=json.loads(r["events_by_source_json"]),
                loss_indicators=json.loads(r["loss_indicators_json"]),
                events=[],
                coverage_gaps=[],
            )
            for r in rows
        ]

    def get_monitoring_events(self, session_id: str, limit: int = 1000) -> list[MonitoringEvent]:
        """Fetch event records for a session with optional limit."""
        rows = self._conn.execute(
            "SELECT * FROM monitoring_events WHERE session_id = ? ORDER BY timestamp_utc ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            MonitoringEvent(
                event_id=r["event_id"],
                session_id=r["session_id"],
                source_id=r["source_id"],
                source_version=r["source_version"],
                category=r["category"],
                timestamp_utc=r["timestamp_utc"],
                source_timestamp=r["source_timestamp"],
                channel_or_path=r["channel_or_path"],
                event_code=r["event_code"],
                process_id=r["process_id"],
                process_name=r["process_name"],
                process_provenance=ProcessProvenance(r["process_provenance"]),
                payload=json.loads(r["payload_json"]),
                is_redacted=bool(r["is_redacted"]),
                is_truncated=bool(r["is_truncated"]),
                confidence=EventConfidence(r["confidence"]),
                raw_ref=r["raw_ref"] or "",
            )
            for r in rows
        ]

    def save_snapshot_diffs(self, diffs: list[SnapshotDiff]) -> None:
        """Persist snapshot comparison difference records."""
        with self._conn:
            for d in diffs:
                self._conn.execute(
                    """
                    INSERT INTO snapshot_diffs (
                        diff_id, before_run_id, after_run_id, category, object_key,
                        change_type, before_json, after_json, status, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(diff_id) DO NOTHING
                    """,
                    (
                        d.diff_id,
                        d.before_run_id,
                        d.after_run_id,
                        d.category,
                        d.object_key,
                        d.change_type.value,
                        json.dumps(d.before_data) if d.before_data is not None else None,
                        json.dumps(d.after_data) if d.after_data is not None else None,
                        d.status,
                        d.notes,
                    ),
                )

    def get_snapshot_diffs(self, before_run_id: str, after_run_id: str) -> list[SnapshotDiff]:
        """Fetch stored snapshot differences between two collection runs."""
        rows = self._conn.execute(
            "SELECT * FROM snapshot_diffs WHERE before_run_id = ? AND after_run_id = ? ORDER BY category, object_key",
            (before_run_id, after_run_id),
        ).fetchall()
        return [
            SnapshotDiff(
                diff_id=r["diff_id"],
                before_run_id=r["before_run_id"],
                after_run_id=r["after_run_id"],
                category=r["category"],
                object_key=r["object_key"],
                change_type=DiffChangeType(r["change_type"]),
                before_data=json.loads(r["before_json"]) if r["before_json"] else None,
                after_data=json.loads(r["after_json"]) if r["after_json"] else None,
                status=r["status"],
                notes=r["notes"] or "",
            )
            for r in rows
        ]

    # --- Phase 6 Candidate Changes & Review Audit Records ---

    def _row_to_candidate(self, row: sqlite3.Row) -> CandidateChange:
        sb_dict = json.loads(row["score_breakdown_json"])
        breakdown = ScoreBreakdown(
            evidence_type_score=sb_dict.get("evidence_type_score", 0.0),
            proximity_score=sb_dict.get("proximity_score", 0.0),
            target_relevance_score=sb_dict.get("target_relevance_score", 0.0),
            provenance_score=sb_dict.get("provenance_score", 0.0),
            corroboration_score=sb_dict.get("corroboration_score", 0.0),
            gap_penalty=sb_dict.get("gap_penalty", 0.0),
            total_score=sb_dict.get("total_score", 0.0),
            rationale=sb_dict.get("rationale", ""),
        )
        return CandidateChange(
            candidate_id=row["candidate_id"],
            investigation_id=row["investigation_id"],
            evidence_type=CandidateEvidenceType(row["evidence_type"]),
            category=row["category"],
            object_key=row["object_key"],
            display_name=row["display_name"],
            description=row["description"],
            first_observed_utc=row["first_observed_utc"],
            last_observed_utc=row["last_observed_utc"],
            temporal_offset_sec=row["temporal_offset_sec"],
            before_value=json.loads(row["before_value_json"]) if row["before_value_json"] is not None else None,
            after_value=json.loads(row["after_value_json"]) if row["after_value_json"] is not None else None,
            process_id=row["process_id"],
            process_name=row["process_name"],
            process_provenance=ProcessProvenance(row["process_provenance"]),
            source_ids=json.loads(row["source_ids_json"]),
            evidence_ids=json.loads(row["evidence_ids_json"]),
            coverage_gaps=json.loads(row["coverage_gaps_json"]),
            score=row["score"],
            score_breakdown=breakdown,
            rank=row["rank"],
            review_status=CandidateReviewStatus(row["review_status"]),
            is_active=bool(row["is_active"]),
            reversibility=CandidateReversibility(row["reversibility"]),
            inclusion_reason=row["inclusion_reason"],
            uncertainty_note=row["uncertainty_note"],
        )

    def save_candidates(self, investigation_id: str, candidates: list[CandidateChange]) -> None:
        """Persist a list of candidate changes for an investigation."""
        with self._conn:
            for cand in candidates:
                self._conn.execute(
                    """
                    INSERT INTO investigation_candidates (
                        candidate_id, investigation_id, evidence_type, category, object_key,
                        display_name, description, first_observed_utc, last_observed_utc,
                        temporal_offset_sec, before_value_json, after_value_json,
                        process_id, process_name, process_provenance,
                        source_ids_json, evidence_ids_json, coverage_gaps_json,
                        score, score_breakdown_json, rank, review_status, is_active,
                        reversibility, inclusion_reason, uncertainty_note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        score=excluded.score,
                        score_breakdown_json=excluded.score_breakdown_json,
                        rank=excluded.rank,
                        review_status=excluded.review_status,
                        is_active=excluded.is_active,
                        reversibility=excluded.reversibility,
                        inclusion_reason=excluded.inclusion_reason,
                        uncertainty_note=excluded.uncertainty_note
                    """,
                    (
                        cand.candidate_id,
                        investigation_id,
                        cand.evidence_type.value,
                        cand.category,
                        cand.object_key,
                        cand.display_name,
                        cand.description,
                        cand.first_observed_utc,
                        cand.last_observed_utc,
                        cand.temporal_offset_sec,
                        json.dumps(cand.before_value) if cand.before_value is not None else None,
                        json.dumps(cand.after_value) if cand.after_value is not None else None,
                        cand.process_id,
                        cand.process_name,
                        cand.process_provenance.value,
                        json.dumps(cand.source_ids),
                        json.dumps(cand.evidence_ids),
                        json.dumps(cand.coverage_gaps),
                        cand.score,
                        json.dumps(cand.score_breakdown.to_dict()),
                        cand.rank,
                        cand.review_status.value,
                        1 if cand.is_active else 0,
                        cand.reversibility.value,
                        cand.inclusion_reason,
                        cand.uncertainty_note,
                    ),
                )

    def get_candidates(self, investigation_id: str, active_only: bool = False) -> list[CandidateChange]:
        """Fetch candidates for an investigation, ordered by rank ascending."""
        query = "SELECT * FROM investigation_candidates WHERE investigation_id = ?"
        params: list[Any] = [investigation_id]
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY rank ASC, score DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_candidate(r) for r in rows]

    def get_candidate(self, candidate_id: str) -> CandidateChange | None:
        """Fetch a single candidate by its ID."""
        row = self._conn.execute(
            "SELECT * FROM investigation_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_candidate(row)

    def update_candidate_review_status(
        self, candidate_id: str, new_status: CandidateReviewStatus, is_active: bool
    ) -> None:
        """Update candidate review status and active flag."""
        with self._conn:
            self._conn.execute(
                """
                UPDATE investigation_candidates
                SET review_status = ?, is_active = ?
                WHERE candidate_id = ?
                """,
                (new_status.value, 1 if is_active else 0, candidate_id),
            )

    def record_candidate_review(self, review: CandidateReviewRecord) -> None:
        """Record an immutable audit log entry for a candidate review decision."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO candidate_reviews (
                    review_id, candidate_id, investigation_id, decision,
                    previous_status, new_status, reason, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review.review_id,
                    review.candidate_id,
                    review.investigation_id,
                    review.decision,
                    review.previous_status.value,
                    review.new_status.value,
                    review.reason,
                    review.reviewed_at,
                ),
            )

    def get_candidate_reviews(self, candidate_id: str) -> list[CandidateReviewRecord]:
        """Fetch full review history audit log for a candidate."""
        rows = self._conn.execute(
            "SELECT * FROM candidate_reviews WHERE candidate_id = ? ORDER BY reviewed_at ASC",
            (candidate_id,),
        ).fetchall()
        return [
            CandidateReviewRecord(
                review_id=r["review_id"],
                candidate_id=r["candidate_id"],
                investigation_id=r["investigation_id"],
                decision=r["decision"],
                previous_status=CandidateReviewStatus(r["previous_status"]),
                new_status=CandidateReviewStatus(r["new_status"]),
                reason=r["reason"],
                reviewed_at=r["reviewed_at"],
            )
            for r in rows
        ]

    # --- Phase 7 Isolated Environments, Baselines & Trials ---

    def register_environment(self, env: EnvironmentRecord) -> None:
        """Persist or update an explicitly registered experiment environment."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO registered_environments (
                    environment_id, adapter_type, display_name, is_disposable,
                    is_host, status, baseline_ids_json, configuration_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(environment_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    status=excluded.status,
                    baseline_ids_json=excluded.baseline_ids_json,
                    configuration_json=excluded.configuration_json
                """,
                (
                    env.environment_id,
                    env.adapter_type.value,
                    env.display_name,
                    1 if env.is_disposable else 0,
                    1 if env.is_host else 0,
                    env.status.value,
                    json.dumps(env.baseline_ids),
                    json.dumps(env.configuration),
                    env.created_at,
                ),
            )

    def get_environment(self, environment_id: str) -> EnvironmentRecord | None:
        """Fetch a registered environment by its ID."""
        row = self._conn.execute(
            "SELECT * FROM registered_environments WHERE environment_id = ?",
            (environment_id,),
        ).fetchone()
        if not row:
            return None
        return EnvironmentRecord(
            environment_id=row["environment_id"],
            adapter_type=AdapterType(row["adapter_type"]),
            display_name=row["display_name"],
            is_disposable=bool(row["is_disposable"]),
            is_host=bool(row["is_host"]),
            status=EnvironmentStatus(row["status"]),
            baseline_ids=json.loads(row["baseline_ids_json"]),
            configuration=json.loads(row["configuration_json"]),
            created_at=row["created_at"],
        )

    def list_environments(self, adapter_type: str | None = None) -> list[EnvironmentRecord]:
        """List registered environments, optionally filtered by adapter type."""
        query = "SELECT * FROM registered_environments"
        params: list[Any] = []
        if adapter_type:
            query += " WHERE adapter_type = ?"
            params.append(adapter_type.upper())
        query += " ORDER BY created_at ASC"

        rows = self._conn.execute(query, params).fetchall()
        return [
            EnvironmentRecord(
                environment_id=r["environment_id"],
                adapter_type=AdapterType(r["adapter_type"]),
                display_name=r["display_name"],
                is_disposable=bool(r["is_disposable"]),
                is_host=bool(r["is_host"]),
                status=EnvironmentStatus(r["status"]),
                baseline_ids=json.loads(r["baseline_ids_json"]),
                configuration=json.loads(r["configuration_json"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def update_environment_status(self, environment_id: str, status: EnvironmentStatus) -> None:
        """Update lifecycle/integrity status of an environment."""
        with self._conn:
            self._conn.execute(
                "UPDATE registered_environments SET status = ? WHERE environment_id = ?",
                (status.value, environment_id),
            )

    def register_baseline(self, baseline: BaselineRecord) -> None:
        """Persist or update a registered baseline / checkpoint record."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO registered_baselines (
                    baseline_id, environment_id, display_name, is_verified, checksum_or_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(environment_id, baseline_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    is_verified=excluded.is_verified,
                    checksum_or_state=excluded.checksum_or_state
                """,
                (
                    baseline.baseline_id,
                    baseline.environment_id,
                    baseline.display_name,
                    1 if baseline.is_verified else 0,
                    baseline.checksum_or_state,
                    baseline.created_at,
                ),
            )

    def get_baseline(self, environment_id: str, baseline_id: str) -> BaselineRecord | None:
        """Fetch a registered baseline by environment ID and baseline ID."""
        row = self._conn.execute(
            "SELECT * FROM registered_baselines WHERE environment_id = ? AND baseline_id = ?",
            (environment_id, baseline_id),
        ).fetchone()
        if not row:
            return None
        return BaselineRecord(
            baseline_id=row["baseline_id"],
            environment_id=row["environment_id"],
            display_name=row["display_name"],
            is_verified=bool(row["is_verified"]),
            checksum_or_state=row["checksum_or_state"],
            created_at=row["created_at"],
        )

    def list_baselines(self, environment_id: str) -> list[BaselineRecord]:
        """List registered baselines for an environment."""
        rows = self._conn.execute(
            "SELECT * FROM registered_baselines WHERE environment_id = ? ORDER BY created_at ASC",
            (environment_id,),
        ).fetchall()
        return [
            BaselineRecord(
                baseline_id=r["baseline_id"],
                environment_id=r["environment_id"],
                display_name=r["display_name"],
                is_verified=bool(r["is_verified"]),
                checksum_or_state=r["checksum_or_state"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def save_isolated_trial(self, trial: IsolatedTrialRecord) -> None:
        """Persist an isolated trial execution audit record."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO isolated_trials (
                    trial_id, investigation_id, environment_id, baseline_id,
                    adapter_type, lifecycle_state, is_simulation, selected_changes_json,
                    start_time, finish_time, outcome, output_bounded, duration_ms,
                    restore_status, verify_status, apply_status, test_status,
                    cleanup_status, error_reason, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trial_id) DO UPDATE SET
                    lifecycle_state=excluded.lifecycle_state,
                    finish_time=excluded.finish_time,
                    outcome=excluded.outcome,
                    output_bounded=excluded.output_bounded,
                    duration_ms=excluded.duration_ms,
                    restore_status=excluded.restore_status,
                    verify_status=excluded.verify_status,
                    apply_status=excluded.apply_status,
                    test_status=excluded.test_status,
                    cleanup_status=excluded.cleanup_status,
                    error_reason=excluded.error_reason,
                    evidence_json=excluded.evidence_json
                """,
                (
                    trial.trial_id,
                    trial.investigation_id,
                    trial.environment_id,
                    trial.baseline_id,
                    trial.adapter_type.value,
                    trial.lifecycle_state.value,
                    1 if trial.is_simulation else 0,
                    json.dumps(trial.selected_change_ids),
                    trial.start_time,
                    trial.finish_time,
                    trial.outcome.value,
                    trial.output_bounded,
                    trial.duration_ms,
                    trial.restore_status,
                    trial.verify_status,
                    trial.apply_status,
                    trial.test_status,
                    trial.cleanup_status,
                    trial.error_reason,
                    json.dumps(trial.evidence_data),
                ),
            )

    def get_isolated_trials(
        self,
        investigation_id: str | None = None,
        environment_id: str | None = None,
        limit: int | None = None,
    ) -> list[IsolatedTrialRecord]:
        """Fetch isolated trial records filtered by investigation_id or environment_id."""
        conditions: list[str] = []
        params: list[Any] = []
        if investigation_id:
            conditions.append("investigation_id = ?")
            params.append(investigation_id)
        if environment_id:
            conditions.append("environment_id = ?")
            params.append(environment_id)

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        order_clause = " ORDER BY start_time DESC" if limit else " ORDER BY start_time ASC"
        limit_clause = " LIMIT ?" if (limit is not None and limit > 0) else ""

        if limit is not None and limit > 0:
            params.append(limit)

        sql = f"SELECT * FROM isolated_trials{where_clause}{order_clause}{limit_clause}"
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_isolated_trial(r) for r in rows]

    def get_isolated_trial(self, trial_id: str) -> IsolatedTrialRecord | None:
        """Fetch a single isolated trial record by ID."""
        row = self._conn.execute(
            "SELECT * FROM isolated_trials WHERE trial_id = ?",
            (trial_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_isolated_trial(row)

    def _row_to_isolated_trial(self, row: sqlite3.Row) -> IsolatedTrialRecord:
        return IsolatedTrialRecord(
            trial_id=row["trial_id"],
            investigation_id=row["investigation_id"],
            environment_id=row["environment_id"],
            baseline_id=row["baseline_id"],
            adapter_type=AdapterType(row["adapter_type"]),
            lifecycle_state=TrialLifecycleState(row["lifecycle_state"]),
            is_simulation=bool(row["is_simulation"]),
            selected_change_ids=json.loads(row["selected_changes_json"]),
            start_time=row["start_time"],
            finish_time=row["finish_time"],
            outcome=HealthCheckOutcome(row["outcome"]),
            output_bounded=row["output_bounded"],
            duration_ms=row["duration_ms"],
            restore_status=row["restore_status"],
            verify_status=row["verify_status"],
            apply_status=row["apply_status"],
            test_status=row["test_status"],
            cleanup_status=row["cleanup_status"],
            error_reason=row["error_reason"],
            evidence_data=json.loads(row["evidence_json"]) if row["evidence_json"] else {},
        )

    def save_verification_run(self, record: VerificationRunRecord) -> None:
        """Persist a complete verification run record and all associated trial records."""
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO verification_runs (
                    verification_id, investigation_id, environment_id, baseline_id,
                    verdict, protocol_version, candidate_ids_json, is_1_minimal,
                    is_interaction, baseline_control_passed, candidate_set_reproduced,
                    reversal_tested, reversal_passed, necessity_json, differential_json,
                    rationale, limitations_json, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.verification_id,
                    record.investigation_id,
                    record.environment_id,
                    record.baseline_id,
                    record.verdict.value,
                    record.protocol_version,
                    json.dumps(record.candidate_ids),
                    1 if record.is_1_minimal else 0,
                    1 if record.is_interaction else 0,
                    1 if record.baseline_control_passed else 0,
                    1 if record.candidate_set_reproduced else 0,
                    1 if record.reversal_tested else 0,
                    None if record.reversal_passed is None else (1 if record.reversal_passed else 0),
                    json.dumps([n.to_dict() for n in record.necessity_details]),
                    json.dumps(record.differential_results),
                    record.confidence_rationale,
                    json.dumps(record.limitations),
                    record.created_at,
                    record.finished_at,
                ),
            )

            for trial in record.trials:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO verification_trials (
                        trial_id, verification_id, trial_role, target_changes_json,
                        omitted_change_id, attempt_number, environment_id, baseline_id,
                        outcome, lifecycle_state, restore_status, cleanup_status,
                        output_bounded, duration_ms, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trial.trial_id,
                        record.verification_id,
                        trial.trial_role.value,
                        json.dumps(trial.target_change_ids),
                        trial.omitted_change_id,
                        trial.attempt_number,
                        trial.environment_id,
                        trial.baseline_id,
                        trial.outcome.value,
                        trial.lifecycle_state,
                        trial.restore_status,
                        trial.cleanup_status,
                        trial.output_bounded,
                        trial.duration_ms,
                        trial.timestamp,
                    ),
                )

    def get_verification_run(self, verification_id: str) -> VerificationRunRecord | None:
        """Retrieve a verification run record and all its associated trials."""
        row = self._conn.execute(
            "SELECT * FROM verification_runs WHERE verification_id = ?",
            (verification_id,),
        ).fetchone()
        if not row:
            return None

        trial_rows = self._conn.execute(
            "SELECT * FROM verification_trials WHERE verification_id = ? ORDER BY timestamp ASC",
            (verification_id,),
        ).fetchall()
        trials = [self._row_to_verification_trial(r) for r in trial_rows]
        return self._row_to_verification_run(row, trials)

    def list_verification_runs(self, investigation_id: str | None = None) -> list[VerificationRunRecord]:
        """List verification runs, optionally filtered by investigation ID."""
        if investigation_id:
            rows = self._conn.execute(
                "SELECT * FROM verification_runs WHERE investigation_id = ? ORDER BY created_at DESC",
                (investigation_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM verification_runs ORDER BY created_at DESC"
            ).fetchall()

        results = []
        for r in rows:
            trial_rows = self._conn.execute(
                "SELECT * FROM verification_trials WHERE verification_id = ? ORDER BY timestamp ASC",
                (r["verification_id"],),
            ).fetchall()
            trials = [self._row_to_verification_trial(tr) for tr in trial_rows]
            results.append(self._row_to_verification_run(r, trials))
        return results

    def _row_to_verification_run(self, row: sqlite3.Row, trials: list[VerificationTrialRecord]) -> VerificationRunRecord:
        raw_nec = json.loads(row["necessity_json"]) if row["necessity_json"] else []
        necessity_details = [
            VerificationNecessityDetail(
                change_id=n["change_id"],
                is_necessary=bool(n["is_necessary"]),
                outcome_without_change=HealthCheckOutcome(n["outcome_without_change"]),
                explanation=n["explanation"],
            )
            for n in raw_nec
        ]
        rev_passed_val = row["reversal_passed"]
        reversal_passed = None if rev_passed_val is None else bool(rev_passed_val)

        return VerificationRunRecord(
            verification_id=row["verification_id"],
            investigation_id=row["investigation_id"],
            environment_id=row["environment_id"],
            baseline_id=row["baseline_id"],
            verdict=VerificationVerdict(row["verdict"]),
            protocol_version=row["protocol_version"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
            candidate_ids=json.loads(row["candidate_ids_json"]),
            is_1_minimal=bool(row["is_1_minimal"]),
            is_interaction=bool(row["is_interaction"]),
            baseline_control_passed=bool(row["baseline_control_passed"]),
            candidate_set_reproduced=bool(row["candidate_set_reproduced"]),
            reversal_tested=bool(row["reversal_tested"]),
            reversal_passed=reversal_passed,
            necessity_details=necessity_details,
            differential_results=json.loads(row["differential_json"]) if row["differential_json"] else {},
            trials=trials,
            confidence_rationale=row["rationale"],
            limitations=json.loads(row["limitations_json"]) if row["limitations_json"] else [],
        )

    def _row_to_verification_trial(self, row: sqlite3.Row) -> VerificationTrialRecord:
        return VerificationTrialRecord(
            trial_id=row["trial_id"],
            verification_id=row["verification_id"],
            trial_role=TrialRole(row["trial_role"]),
            target_change_ids=json.loads(row["target_changes_json"]),
            omitted_change_id=row["omitted_change_id"],
            attempt_number=row["attempt_number"],
            environment_id=row["environment_id"],
            baseline_id=row["baseline_id"],
            outcome=HealthCheckOutcome(row["outcome"]),
            lifecycle_state=row["lifecycle_state"],
            restore_status=row["restore_status"],
            cleanup_status=row["cleanup_status"],
            output_bounded=row["output_bounded"],
            duration_ms=row["duration_ms"],
            timestamp=row["timestamp"],
        )
