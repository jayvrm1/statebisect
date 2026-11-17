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
from .monitor.models import (
    EventConfidence,
    MonitoringCoverageGap,
    MonitoringEvent,
    MonitoringSession,
    MonitoringSessionStatus,
    ProcessProvenance,
)

LATEST_SCHEMA_VERSION = 5


class Storage:
    """Manages SQLite storage, migrations, audit logging, and snapshots for StateBisect."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
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
                    scenario_id, scenario_hash, scenario_snapshot, engine_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    engine_version=excluded.engine_version
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
            )
            for row in cursor.fetchall()
        ]

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
