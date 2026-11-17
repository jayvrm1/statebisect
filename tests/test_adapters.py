"""Unit and integration tests for StateBisect Phase 7 isolated adapters and trial runner."""

import os
import tempfile
import unittest
from pathlib import Path

from statebisect.adapters import (
    AdapterOperationResult,
    AdapterRegistry,
    AdapterType,
    BaseEnvironmentAdapter,
    BaselineRecord,
    EnvironmentRecord,
    EnvironmentStatus,
    FakeAdapter,
    HyperVAdapter,
    IsolatedTrialRecord,
    IsolatedTrialRunner,
    RegisteredActionRegistry,
    SimulationAdapter,
    TrialLifecycleState,
    WindowsSandboxEvaluation,
)
from statebisect.delta_debugging import DeltaDebugger
from statebisect.models import (
    CancellationToken,
    Change,
    HealthCheckOutcome,
    Investigation,
    InvestigationStatus,
)
from statebisect.storage import Storage


class TestSimulationAdapter(unittest.TestCase):
    """Test SimulationAdapter deterministic execution."""

    def setUp(self):
        self.adapter = SimulationAdapter()

    def test_availability_and_metadata(self):
        self.assertTrue(self.adapter.is_available())
        self.assertEqual(self.adapter.get_adapter_type(), AdapterType.SIMULATION)
        envs = [e.environment_id for e in self.adapter.list_registered_environments()]
        self.assertIn("sim-env-default", envs)

    def test_restore_and_verify_baseline(self):
        r_restore = self.adapter.restore_baseline("sim-env-default", "baseline-001")
        self.assertTrue(r_restore.success)

        r_verify = self.adapter.verify_baseline("sim-env-default", "baseline-001")
        self.assertTrue(r_verify.success)
        env = self.adapter.inspect_environment("sim-env-default")
        self.assertIsNotNone(env)
        self.assertEqual(env.status, EnvironmentStatus.VERIFIED)

    def test_apply_change_and_health_check(self):
        self.adapter.restore_baseline("sim-env-default", "baseline-001")
        # Apply change that causes health check to fail
        r_apply = self.adapter.apply_registered_change(
            "sim-env-default",
            "simulated_state_set",
            {"category": "config", "key": "status", "value": "failed"},
        )
        self.assertTrue(r_apply.success)

        # Baseline check now fails because status is 'failed' != 'healthy'
        r_check = self.adapter.run_health_check("sim-env-default", "default", {})
        self.assertEqual(r_check.outcome, HealthCheckOutcome.FAIL)

        # Cleanup resets state
        r_clean = self.adapter.cleanup_trial("sim-env-default")
        self.assertTrue(r_clean.success)
        r_check2 = self.adapter.run_health_check("sim-env-default", "default", {})
        self.assertEqual(r_check2.outcome, HealthCheckOutcome.PASS)


class TestFakeAdapterFaultInjection(unittest.TestCase):
    """Test FakeAdapter fault injection capabilities."""

    def test_fault_injection_toggles(self):
        fake = FakeAdapter()
        fake.register_environment("env-test", "b-test")

        # Verify initial success
        self.assertTrue(fake.restore_baseline("env-test", "b-test").success)
        self.assertTrue(fake.verify_baseline("env-test", "b-test").success)
        self.assertTrue(fake.apply_registered_change("env-test", "action-1", {}).success)
        self.assertEqual(fake.run_health_check("env-test", "check-1", {}).outcome, HealthCheckOutcome.PASS)
        self.assertTrue(fake.cleanup_trial("env-test").success)

        # Test fail_restore
        fake.fail_restore = True
        self.assertFalse(fake.restore_baseline("env-test", "b-test").success)
        fake.fail_restore = False

        # Test fail_verify
        fake.fail_verify = True
        self.assertFalse(fake.verify_baseline("env-test", "b-test").success)
        fake.fail_verify = False

        # Test fail_apply
        fake.fail_apply = True
        self.assertFalse(fake.apply_registered_change("env-test", "action-1", {}).success)
        fake.fail_apply = False

        # Test fail_health_check
        fake.fail_health_check = True
        r_hc = fake.run_health_check("env-test", "check-1", {})
        self.assertEqual(r_hc.outcome, HealthCheckOutcome.FAIL)
        fake.fail_health_check = False

        # Test fail_cleanup
        fake.fail_cleanup = True
        fake.corrupt_on_cleanup_failure = True
        r_cl = fake.cleanup_trial("env-test")
        self.assertFalse(r_cl.success)
        env = fake.inspect_environment("env-test")
        self.assertIsNotNone(env)
        self.assertEqual(env.status, EnvironmentStatus.CORRUPTED)


class TestActionRegistrySafety(unittest.TestCase):
    """Test safe action allowlist and rejection of arbitrary commands."""

    def setUp(self):
        self.registry = RegisteredActionRegistry()

    def test_default_actions_registered(self):
        actions = self.registry.list_actions()
        action_ids = [a.action_id for a in actions]
        self.assertIn("simulated_state_set", action_ids)
        self.assertIn("fixture_config_set", action_ids)

    def test_unregistered_action_rejected(self):
        with self.assertRaises(PermissionError) as ctx:
            self.registry.validate_action_invocation("arbitrary_dangerous_action", {})
        self.assertIn("not registered in the action allowlist", str(ctx.exception))

    def test_raw_command_keys_strictly_forbidden(self):
        for bad_key in ["command", "cmd", "shell", "powershell", "script", "raw_exec", "run"]:
            with self.assertRaises(ValueError) as ctx:
                self.registry.validate_action_invocation(
                    "simulated_state_set",
                    {bad_key: "rmdir /s /q C:\\", "category": "reg"},
                )
            self.assertIn("Disallowed executable keys detected", str(ctx.exception))


class TestIsolatedTrialRunner(unittest.TestCase):
    """Test IsolatedTrialRunner state transitions and safety invariants."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_runner.db"
        self.storage = Storage(self.db_path)
        self.inv = Investigation(
            id="inv-iso-1",
            symptom="App crash on launch",
            target_system="TestApp",
            status=InvestigationStatus.NEW,
            baseline_id="baseline-001",
        )
        self.storage.save_investigation(self.inv)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_successful_trial_lifecycle(self):
        adapter = SimulationAdapter()
        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id="sim-env-default",
            baseline_id="baseline-001",
            storage=self.storage,
            investigation_id="inv-iso-1",
        )

        # Baseline check (no changes) -> should PASS
        trial = runner.run_isolated_trial(selected_changes=[])
        self.assertEqual(trial.lifecycle_state, TrialLifecycleState.COMPLETED)
        self.assertEqual(trial.outcome, HealthCheckOutcome.PASS)
        self.assertEqual(trial.restore_status, "SUCCESS")
        self.assertEqual(trial.cleanup_status, "SUCCESS")

        # Trial with failing change
        trial2 = runner.run_isolated_trial(
            selected_changes=[Change(id="c-fail", category="config", description="Failing setting", payload={"key": "status", "value": "failed"})]
        )
        self.assertEqual(trial2.lifecycle_state, TrialLifecycleState.FAILED)
        self.assertEqual(trial2.outcome, HealthCheckOutcome.FAIL)

    def test_restore_failure_halts_trial_before_apply(self):
        fake = FakeAdapter()
        fake.register_environment("env-fake", "base-fake")
        fake.fail_restore = True

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="env-fake",
            baseline_id="base-fake",
            storage=self.storage,
            investigation_id="inv-iso-1",
        )

        trial = runner.run_isolated_trial(selected_changes=["some_change"])
        self.assertEqual(trial.lifecycle_state, TrialLifecycleState.ENVIRONMENT_CORRUPTED)
        self.assertEqual(trial.outcome, HealthCheckOutcome.INCONCLUSIVE)
        self.assertIn("restore failure", (trial.error_reason or "").lower())
        # Apply was never called!
        apply_calls = [c for c in fake.call_history if c[0] == "apply_change"]
        self.assertEqual(len(apply_calls), 0)

    def test_verify_failure_halts_trial(self):
        fake = FakeAdapter()
        fake.register_environment("env-fake", "base-fake")
        fake.fail_verify = True

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="env-fake",
            baseline_id="base-fake",
            storage=self.storage,
            investigation_id="inv-iso-1",
        )

        trial = runner.run_isolated_trial(selected_changes=["some_change"])
        self.assertEqual(trial.lifecycle_state, TrialLifecycleState.ENVIRONMENT_CORRUPTED)
        self.assertEqual(trial.outcome, HealthCheckOutcome.INCONCLUSIVE)
        self.assertIn("verify failure", (trial.error_reason or "").lower())
        apply_calls = [c for c in fake.call_history if c[0] == "apply_change"]
        self.assertEqual(len(apply_calls), 0)

    def test_cleanup_failure_marks_environment_corrupted_and_refuses_next_trial(self):
        fake = FakeAdapter()
        fake.register_environment("env-fake", "base-fake")
        fake.fail_cleanup = True
        fake.corrupt_on_cleanup_failure = True

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="env-fake",
            baseline_id="base-fake",
            storage=self.storage,
            investigation_id="inv-iso-1",
        )

        # First trial fails cleanup
        trial = runner.run_isolated_trial(selected_changes=[])
        self.assertEqual(trial.lifecycle_state, TrialLifecycleState.ENVIRONMENT_CORRUPTED)
        self.assertTrue(trial.cleanup_status.startswith("FAILED"))

        # Second trial is immediately refused because environment is marked corrupted
        trial2 = runner.run_isolated_trial(selected_changes=[])
        self.assertEqual(trial2.lifecycle_state, TrialLifecycleState.ENVIRONMENT_CORRUPTED)
        self.assertIn("CORRUPTED", trial2.error_reason or "")

    def test_cancellation_token_stops_trial(self):
        token = CancellationToken()
        token.cancel("User requested stop")

        adapter = SimulationAdapter()
        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id="sim-env-default",
            baseline_id="baseline-001",
            storage=self.storage,
            investigation_id="inv-iso-1",
            cancellation_token=token,
        )

        trial = runner.run_isolated_trial(selected_changes=[])
        self.assertEqual(trial.lifecycle_state, TrialLifecycleState.CANCELLED)
        self.assertEqual(trial.outcome, HealthCheckOutcome.INCONCLUSIVE)


class TestDeltaDebuggerWithIsolatedRunner(unittest.TestCase):
    """Test full Delta Debugging integration with IsolatedTrialRunner."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_dd_iso.db"
        self.storage = Storage(self.db_path)
        self.inv = Investigation(
            id="inv-dd-iso",
            symptom="Simulated service timeout",
            target_system="SimService",
            status=InvestigationStatus.NEW,
            baseline_id="baseline-001",
        )
        self.storage.save_investigation(self.inv)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_delta_debugging_finds_minimal_failure_inducing_change(self):
        adapter = SimulationAdapter()
        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id="sim-env-default",
            baseline_id="baseline-001",
            storage=self.storage,
            investigation_id="inv-dd-iso",
        )

        # 4 candidate changes, where Change 3 causes failure
        changes = [
            Change(id="chg-1", category="config", description="Timeout setting", payload={"key": "timeout", "value": "30"}),
            Change(id="chg-2", category="config", description="Retries setting", payload={"key": "retries", "value": "3"}),
            Change(id="chg-3-bad", category="config", description="Failing status setting", payload={"key": "status", "value": "failed"}),
            Change(id="chg-4", category="config", description="Debug setting", payload={"key": "debug", "value": "true"}),
        ]

        debugger = DeltaDebugger(
            runner=runner,
            all_changes=changes,
            health_check=lambda ws: HealthCheckResult(outcome=HealthCheckOutcome.PASS, output=""),
        )
        result = debugger.minimize(candidate_ids=[c.id for c in changes])

        self.assertEqual(result.verdict, "SUCCESS")
        self.assertEqual(len(result.suspected_set), 1)
        self.assertEqual(result.suspected_set[0], "chg-3-bad")
        self.assertGreater(result.total_trials, 1)


class TestHyperVAdapterSafety(unittest.TestCase):
    """Test HyperVAdapter safety invariants and graceful fallback."""

    def test_host_registration_refused(self):
        adapter = HyperVAdapter()
        with self.assertRaises(ValueError) as ctx:
            adapter.register_disposable_vm("host-vm", "Host", is_host=True)
        self.assertIn("Refusing to register host machine", str(ctx.exception))

    def test_non_disposable_registration_refused(self):
        adapter = HyperVAdapter()
        with self.assertRaises(ValueError) as ctx:
            adapter.register_disposable_vm("prod-vm", "Prod", is_disposable=False)
        self.assertIn("must be disposable", str(ctx.exception))

    def test_invalid_vm_name_injection_refused(self):
        adapter = HyperVAdapter()
        for bad_name in ["vm;rmdir", "vm$(whoami)", "vm`whoami`", "vm&whoami", "vm|whoami", 'vm"']:
            with self.assertRaises(ValueError) as ctx:
                adapter.register_disposable_vm(bad_name, bad_name)
            self.assertIn("Invalid VM identifier", str(ctx.exception))

    def test_unregistered_vm_operations_fail_cleanly(self):
        adapter = HyperVAdapter()
        res = adapter.restore_baseline("unregistered-vm", "chk-1")
        self.assertFalse(res.success)
        self.assertIn("not registered", res.error_reason or res.output)


class TestWindowsSandboxEvaluation(unittest.TestCase):
    """Test Windows Sandbox capability evaluation report."""

    def test_sandbox_evaluation_report(self):
        report = WindowsSandboxEvaluation.get_evaluation_report()
        self.assertEqual(report["adapter_type"], AdapterType.WINDOWS_SANDBOX.value)
        self.assertEqual(report["status"], "DEFERRED")
        self.assertIn("learn.microsoft.com", report["primary_source"])
        self.assertIn("SimulationAdapter", report["recommendation"])
        self.assertGreater(len(report["reasons_deferred"]), 0)


if __name__ == "__main__":
    unittest.main()
