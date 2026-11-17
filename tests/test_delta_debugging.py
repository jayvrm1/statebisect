"""Tests for Delta Debugging (ddmin) algorithm."""

import unittest
from statebisect.delta_debugging import DeltaDebugger
from statebisect.models import (
    Change,
    HealthCheckOutcome,
    HealthCheckResult,
    Investigation,
)
from statebisect.runner import ExperimentRunner
from statebisect.simulation import SimulatedWorkspace
from statebisect.storage import Storage


class TestDeltaDebugging(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.inv = Investigation(id="inv-ddmin", symptom="App failure", target_system="AppX")
        self.storage.save_investigation(self.inv)
        self.workspace = SimulatedWorkspace(baseline_state={"status": "OK"})
        self.runner = ExperimentRunner(self.workspace, self.storage, self.inv.id)

    def test_single_change_cause_isolation(self):
        # 8 candidate changes, c4 causes failure
        changes = [
            Change(id=f"c{i}", category="cfg", description=f"Change {i}", payload={f"k{i}": f"v{i}"})
            for i in range(1, 9)
        ]

        def health_check(ws):
            # c4 fails the check
            if "c4" in ws.applied_change_ids:
                return HealthCheckResult(HealthCheckOutcome.FAIL, "Failure triggered by c4")
            return HealthCheckResult(HealthCheckOutcome.PASS, "System operational")

        dd = DeltaDebugger(self.runner, changes, health_check)
        result = dd.minimize([c.id for c in changes])

        self.assertEqual(result.verdict, "SUCCESS")
        self.assertEqual(result.suspected_set, ["c4"])
        self.assertTrue(result.is_1_minimal)
        self.assertIn("Isolated 1-minimal failure-inducing set", result.rationale)

    def test_two_change_interaction_isolation(self):
        # 8 candidate changes, c3 AND c6 together cause failure
        changes = [
            Change(id=f"c{i}", category="cfg", description=f"Change {i}", payload={f"k{i}": f"v{i}"})
            for i in range(1, 9)
        ]

        def health_check(ws):
            applied = set(ws.applied_change_ids)
            if "c3" in applied and "c6" in applied:
                return HealthCheckResult(
                    HealthCheckOutcome.FAIL, "Fault triggered by combination of c3 and c6"
                )
            return HealthCheckResult(HealthCheckOutcome.PASS, "System operational")

        dd = DeltaDebugger(self.runner, changes, health_check)
        result = dd.minimize([c.id for c in changes])

        self.assertEqual(result.verdict, "SUCCESS")
        self.assertEqual(sorted(result.suspected_set), ["c3", "c6"])
        self.assertTrue(result.is_1_minimal)
        self.assertIn("interaction set", result.rationale)

    def test_baseline_failure_aborts_early(self):
        changes = [Change(id="c1", category="c", description="1")]

        def broken_baseline_check(ws):
            return HealthCheckResult(HealthCheckOutcome.FAIL, "Baseline already broken")

        dd = DeltaDebugger(self.runner, changes, broken_baseline_check)
        result = dd.minimize(["c1"])

        self.assertEqual(result.verdict, "BASELINE_FAIL")
        self.assertEqual(result.suspected_set, [])
        self.assertFalse(result.is_1_minimal)

    def test_full_candidate_set_passes_reports_not_reproducible(self):
        changes = [Change(id="c1", category="c", description="1")]

        def always_pass_check(ws):
            return HealthCheckResult(HealthCheckOutcome.PASS, "All good")

        dd = DeltaDebugger(self.runner, changes, always_pass_check)
        result = dd.minimize(["c1"])

        self.assertEqual(result.verdict, "NOT_REPRODUCIBLE")
        self.assertEqual(result.suspected_set, [])
        self.assertFalse(result.is_1_minimal)

    def test_inconclusive_initial_check_stops_safely(self):
        changes = [Change(id="c1", category="c", description="1")]

        def inconclusive_check(ws):
            if len(ws.applied_change_ids) == 0:
                return HealthCheckResult(HealthCheckOutcome.PASS, "Baseline OK")
            return HealthCheckResult(HealthCheckOutcome.INCONCLUSIVE, "Telemetry timeout")

        dd = DeltaDebugger(self.runner, changes, inconclusive_check)
        result = dd.minimize(["c1"])

        self.assertEqual(result.verdict, "INCONCLUSIVE")
        self.assertIn("INCONCLUSIVE", result.rationale)
        self.assertFalse(result.is_1_minimal)


if __name__ == "__main__":
    unittest.main()

