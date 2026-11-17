"""Tests for SQLite persistence of investigations and experiment trials."""

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


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_statebisect.db"
        self.storage = Storage(self.db_path)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_save_and_get_investigation(self):
        inv = Investigation(
            id="inv-001",
            symptom="Service crashes on launch",
            target_system="BillingApp",
            status=InvestigationStatus.NEW,
        )
        self.storage.save_investigation(inv)

        retrieved = self.storage.get_investigation("inv-001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.id, "inv-001")
        self.assertEqual(retrieved.symptom, "Service crashes on launch")
        self.assertEqual(retrieved.status, InvestigationStatus.NEW)

    def test_save_and_get_trials(self):
        inv = Investigation(
            id="inv-002",
            symptom="High CPU usage",
            target_system="WorkerDaemon",
        )
        self.storage.save_investigation(inv)

        trial1 = ExperimentTrial(
            trial_id="tr-1",
            investigation_id="inv-002",
            selected_change_ids=["c1", "c2"],
            start_time="2026-11-17T10:00:00Z",
            finish_time="2026-11-17T10:00:01Z",
            outcome=HealthCheckOutcome.FAIL,
            health_output="Crash 0xC0000005",
            reset_status="SUCCESS",
            duration_ms=45.2,
            notes="Initial trial",
        )
        self.storage.save_trial(trial1)

        trials = self.storage.get_trials("inv-002")
        self.assertEqual(len(trials), 1)
        self.assertEqual(trials[0].trial_id, "tr-1")
        self.assertEqual(trials[0].selected_change_ids, ["c1", "c2"])
        self.assertEqual(trials[0].outcome, HealthCheckOutcome.FAIL)


if __name__ == "__main__":
    unittest.main()
