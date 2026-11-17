"""SQLite persistence layer with schema migrations and structured audit logging."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

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

LATEST_SCHEMA_VERSION = 3


class Storage:
    """Manages SQLite storage, migrations, and audit logging for StateBisect."""

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
                # Add check metadata to experiment_trials if missing
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

        # Save any associated individual attempt records
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
