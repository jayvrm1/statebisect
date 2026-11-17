"""Tests for the experiment runner and cleanup safety."""

import unittest
from statebisect.models import Change, HealthCheckOutcome, HealthCheckResult, Investigation
from statebisect.runner import ExperimentRunner
from statebisect.simulation import SimulatedWorkspace
from statebisect.storage import Storage


class TestExperimentRunner(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.inv = Investigation(id="inv-test", symptom="Test", target_system="TestApp")
        self.storage.save_investigation(self.inv)
        self.workspace = SimulatedWorkspace(baseline_state={"val": 100})
        self.runner = ExperimentRunner(self.workspace, self.storage, self.inv.id)

    def test_trial_resets_workspace_before_and_after(self):
        # Pollute workspace before run to verify runner resets before trial
        self.workspace._current_state["val"] = 999
        self.workspace._applied_change_ids.append("leak")

        def check(ws):
            # Inside the trial, state should reflect baseline + change
            self.assertEqual(ws.current_state["val"], 200)
            return HealthCheckResult(outcome=HealthCheckOutcome.PASS, output="OK")

        c = Change(id="c1", category="cfg", description="Update val", payload={"val": 200})
        trial = self.runner.run_trial([c], check)

        self.assertEqual(trial.outcome, HealthCheckOutcome.PASS)
        self.assertEqual(trial.reset_status, "SUCCESS")
        # Ensure workspace restored to baseline after trial
        self.assertTrue(self.workspace.verify_baseline())
        self.assertEqual(self.workspace.current_state["val"], 100)

    def test_cleanup_occurs_when_health_check_raises_exception(self):
        def crashing_check(ws):
            raise RuntimeError("Unexpected simulator crash")

        c = Change(id="c1", category="cfg", description="Bad", payload={"val": 500})
        trial = self.runner.run_trial([c], crashing_check)

        # Must record inconclusive and capture exception message
        self.assertEqual(trial.outcome, HealthCheckOutcome.INCONCLUSIVE)
        self.assertIn("RuntimeError", trial.health_output)
        self.assertEqual(trial.reset_status, "SUCCESS")
        # Must restore baseline despite exception
        self.assertTrue(self.workspace.verify_baseline())
        self.assertEqual(self.workspace.current_state["val"], 100)

    def test_trial_saved_to_sqlite(self):
        def check(ws):
            return HealthCheckResult(outcome=HealthCheckOutcome.FAIL, output="Service down")

        trial = self.runner.run_trial([], check)
        persisted = self.storage.get_trials(self.inv.id)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0].trial_id, trial.trial_id)
        self.assertEqual(persisted[0].outcome, HealthCheckOutcome.FAIL)


if __name__ == "__main__":
    unittest.main()

