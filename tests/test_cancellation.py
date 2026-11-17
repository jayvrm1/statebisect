"""Tests for execution cancellation, cleanup guarantees, and corruption detection."""

import unittest
from statebisect.delta_debugging import DeltaDebugger
from statebisect.models import (
    CancellationToken,
    Change,
    HealthCheckOutcome,
    HealthCheckResult,
    Investigation,
    InvestigationStatus,
)
from statebisect.runner import ExperimentRunner
from statebisect.simulation import BaselineIntegrityError, SimulatedWorkspace
from statebisect.storage import Storage


class TestCancellationAndCleanup(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.inv = Investigation(id="inv-cancel-test", symptom="Test", target_system="TestApp")
        self.storage.save_investigation(self.inv)
        self.workspace = SimulatedWorkspace(baseline_state={"status": "OK"})

    def tearDown(self):
        self.storage.close()

    def test_cancellation_token_stops_runner(self):
        token = CancellationToken()
        runner = ExperimentRunner(self.workspace, self.storage, self.inv.id, cancellation_token=token)

        token.cancel("User aborted run")
        trial = runner.run_trial([], lambda ws: HealthCheckResult(HealthCheckOutcome.PASS, "OK"))

        self.assertEqual(trial.outcome, HealthCheckOutcome.INCONCLUSIVE)
        self.assertIn("Cancelled: User aborted run", trial.health_output)
        # Ensure workspace remains clean
        self.assertTrue(self.workspace.verify_baseline())

    def test_cancellation_during_delta_debugging_stops_subsequent_trials(self):
        token = CancellationToken()
        runner = ExperimentRunner(self.workspace, self.storage, self.inv.id, cancellation_token=token)

        changes = [Change(id=f"c{i}", category="test", description=f"ch {i}") for i in range(1, 9)]

        trial_counter = [0]

        def dynamic_check(ws):
            trial_counter[0] += 1
            if trial_counter[0] == 2:
                # Cancel on second trial (full candidate set check)
                token.cancel("Abort requested during trial 2")
            if len(ws.applied_change_ids) > 0:
                return HealthCheckResult(HealthCheckOutcome.FAIL, "Fault")
            return HealthCheckResult(HealthCheckOutcome.PASS, "OK")

        debugger = DeltaDebugger(runner, changes, dynamic_check)
        result = debugger.minimize([c.id for c in changes])

        self.assertEqual(result.verdict, "CANCELLED")
        self.assertFalse(result.is_1_minimal)
        self.assertIn("cancelled by user", result.rationale.lower())
        # Trials should be halted early, not continuing all 18 trials
        self.assertLess(result.total_trials, 6)
        self.assertTrue(self.workspace.verify_baseline())

    def test_cleanup_failure_halts_run_and_reports_corruption(self):
        # Workspace that simulates a corrupted state upon reset
        class CorruptedWorkspace(SimulatedWorkspace):
            def reset_to_baseline(self):
                raise BaselineIntegrityError("Disk failure simulated during baseline restoration")

        bad_ws = CorruptedWorkspace(baseline_state={"val": 1})
        runner = ExperimentRunner(bad_ws, self.storage, self.inv.id)

        changes = [Change(id="c1", category="c", description="1")]
        debugger = DeltaDebugger(
            runner, changes, lambda ws: HealthCheckResult(HealthCheckOutcome.FAIL, "fail")
        )

        result = debugger.minimize(["c1"])
        self.assertEqual(result.verdict, "SIMULATION_CORRUPTED")
        self.assertFalse(result.is_1_minimal)
        self.assertIn("Baseline cleanup failed", result.rationale)


if __name__ == "__main__":
    unittest.main()
