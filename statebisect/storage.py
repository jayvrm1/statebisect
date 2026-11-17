"""SQLite persistence layer for investigations and experiment trial history."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import ExperimentTrial, HealthCheckOutcome, Investigation, InvestigationStatus


class Storage:
    """Manages SQLite storage for StateBisect audit and trial history."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_db()

    def close(self) -> None:
        """Close SQLite database connection."""
        if self._conn:
            self._conn.close()

    def _init_db(self) -> None:
        with self._conn:
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
                """
            )

    def save_investigation(self, inv: Investigation) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO investigations (
                    id, symptom, target_system, status, baseline_id, 
                    created_at, updated_at, coverage_limitations
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    symptom=excluded.symptom,
                    target_system=excluded.target_system,
                    status=excluded.status,
                    baseline_id=excluded.baseline_id,
                    updated_at=excluded.updated_at,
                    coverage_limitations=excluded.coverage_limitations
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
        )

    def save_trial(self, trial: ExperimentTrial) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO experiment_trials (
                    trial_id, investigation_id, selected_change_ids, start_time,
                    finish_time, outcome, health_output, reset_status, duration_ms, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trial_id) DO UPDATE SET
                    outcome=excluded.outcome,
                    health_output=excluded.health_output,
                    reset_status=excluded.reset_status,
                    duration_ms=excluded.duration_ms,
                    notes=excluded.notes
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
                ),
            )

    def get_trials(self, investigation_id: str) -> list[ExperimentTrial]:
        cursor = self._conn.execute(
            "SELECT * FROM experiment_trials WHERE investigation_id = ? ORDER BY start_time ASC",
            (investigation_id,),
        )
        rows = cursor.fetchall()
        return [
            ExperimentTrial(
                trial_id=r["trial_id"],
                investigation_id=r["investigation_id"],
                selected_change_ids=json.loads(r["selected_change_ids"]),
                start_time=r["start_time"],
                finish_time=r["finish_time"],
                outcome=HealthCheckOutcome(r["outcome"]),
                health_output=r["health_output"],
                reset_status=r["reset_status"],
                duration_ms=r["duration_ms"],
                notes=r["notes"] or "",
            )
            for r in rows
        ]
