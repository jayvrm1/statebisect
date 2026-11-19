"""Unit and integration tests for StateBisect Phase 8 Verification and Differential Validation."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from statebisect.adapters import (
    AdapterRegistry,
    AdapterType,
    BaselineRecord,
    EnvironmentRecord,
    EnvironmentStatus,
    FakeAdapter,
    IsolatedTrialRunner,
    SimulationAdapter,
)
from statebisect.models import (
    CancellationToken,
    Change,
    HealthCheckOutcome,
    HealthCheckResult,
    Investigation,
    InvestigationStatus,
)
from statebisect.storage import Storage
from statebisect.verification import (
    TrialRole,
    VerificationEngine,
    VerificationNecessityDetail,
    VerificationProtocolConfig,
    VerificationRunRecord,
    VerificationVerdict,
    format_verification_json,
    format_verification_text,
)


class TestVerificationProtocolBehavior(unittest.TestCase):
    """Test deterministic verification protocol verdicts, necessity, and interactions."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_verif.db")
        self.storage = Storage(self.db_path)

        self.investigation = Investigation(
            id="inv-verif-1",
            symptom="App crashed on startup",
            target_system="DemoApp",
            baseline_id="baseline-001",
        )
        self.storage.save_investigation(self.investigation)

        self.adapter = SimulationAdapter()
        self.runner = IsolatedTrialRunner(
            adapter=self.adapter,
            environment_id="sim-env-default",
            baseline_id="baseline-001",
            storage=self.storage,
            investigation_id="inv-verif-1",
        )

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_single_cause_verified_set(self):
        # 1 candidate change that modifies status to 'failed'
        changes = [
            Change(id="c-bad", category="config", description="Failing setting", payload={"key": "status", "value": "failed"})
        ]

        engine = VerificationEngine(
            runner=self.runner,
            candidate_changes=changes,
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.VERIFIED_SET)
        self.assertTrue(res.is_1_minimal)
        self.assertFalse(res.is_interaction)
        self.assertTrue(res.baseline_control_passed)
        self.assertTrue(res.candidate_set_reproduced)
        self.assertEqual(len(res.necessity_details), 1)
        self.assertTrue(res.necessity_details[0].is_necessary)
        self.assertIn("verified", res.confidence_rationale.lower())

    def test_multi_change_interaction_verified(self):
        # 2 changes: neither alone fails, but both together fail (c1: status=bad, c2: flag=active)
        # In SimulationAdapter, any change with value 'failed' triggers failure.
        # Let's test a 2-change interaction where both together cause status="failed".
        # We configure custom change payloads.
        changes = [
            Change(id="c-part1", category="config", description="Part 1", payload={"key": "mode", "value": "strict"}),
            Change(id="c-part2", category="config", description="Part 2", payload={"key": "status", "value": "failed"}),
        ]

        # For this test, let's use FakeAdapter to test exact interaction mechanics:
        fake = FakeAdapter()
        fake.register_environment("fake-env", "base-01")

        # Define health check behavior: FAIL only if BOTH c1 and c2 are in selected changes
        def interaction_check(applied):
            if "c1" in applied and "c2" in applied:
                return HealthCheckOutcome.FAIL, "Interaction crash"
            return HealthCheckOutcome.PASS, "OK"

        fake.custom_check_callback = interaction_check

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="fake-env",
            baseline_id="base-01",
            storage=self.storage,
            investigation_id="inv-verif-1",
        )

        c1 = Change(id="c1", category="config", description="Part 1", payload={})
        c2 = Change(id="c2", category="config", description="Part 2", payload={})

        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[c1, c2],
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.VERIFIED_INTERACTION)
        self.assertTrue(res.is_1_minimal)
        self.assertTrue(res.is_interaction)
        self.assertTrue(res.baseline_control_passed)
        self.assertTrue(res.candidate_set_reproduced)
        self.assertEqual(len(res.necessity_details), 2)
        self.assertTrue(res.necessity_details[0].is_necessary)
        self.assertTrue(res.necessity_details[1].is_necessary)
        self.assertIn("interaction", res.confidence_rationale.lower())

    def test_partially_supported_when_redundant_bystander_present(self):
        # 2 changes: c1 fails alone, c2 is a harmless bystander
        fake = FakeAdapter()
        fake.register_environment("fake-env", "base-01")

        def single_culprit_check(applied):
            if "c1-bad" in applied:
                return HealthCheckOutcome.FAIL, "c1-bad triggered error"
            return HealthCheckOutcome.PASS, "OK"

        fake.custom_check_callback = single_culprit_check

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="fake-env",
            baseline_id="base-01",
            storage=self.storage,
            investigation_id="inv-verif-1",
        )

        c1 = Change(id="c1-bad", category="config", description="Culprit", payload={})
        c2 = Change(id="c2-bystander", category="config", description="Bystander", payload={})

        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[c1, c2],
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.PARTIALLY_SUPPORTED)
        self.assertFalse(res.is_1_minimal)
        self.assertFalse(res.is_interaction)
        self.assertTrue(res.candidate_set_reproduced)

        # c1 is necessary (omitting it leaves only c2, which passes)
        nec_c1 = next(n for n in res.necessity_details if n.change_id == "c1-bad")
        self.assertTrue(nec_c1.is_necessary)

        # c2 is NOT necessary (omitting it leaves c1, which still fails)
        nec_c2 = next(n for n in res.necessity_details if n.change_id == "c2-bystander")
        self.assertFalse(nec_c2.is_necessary)

    def test_not_reproduced_when_candidate_set_passes(self):
        # Benign changes that don't trigger the failure
        changes = [
            Change(id="c-ok", category="config", description="Benign", payload={"key": "timeout", "value": "10"})
        ]

        engine = VerificationEngine(
            runner=self.runner,
            candidate_changes=changes,
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.NOT_REPRODUCED)
        self.assertTrue(res.baseline_control_passed)
        self.assertFalse(res.candidate_set_reproduced)
        self.assertIn("did not reproduce", res.confidence_rationale.lower())

    def test_invalid_baseline_aborts_protocol(self):
        # Baseline itself fails health check
        fake = FakeAdapter()
        fake.register_environment("fake-env", "base-01")
        fake.fail_health_check = True

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="fake-env",
            baseline_id="base-01",
            storage=self.storage,
            investigation_id="inv-verif-1",
        )

        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[Change(id="c1", category="cfg", description="d", payload={})],
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.INVALID_BASELINE)
        self.assertFalse(res.baseline_control_passed)
        self.assertIn("failed", res.confidence_rationale.lower())

    def test_repetition_disagreement_yields_inconclusive(self):
        # Intermittent health check outcome on candidate set
        fake = FakeAdapter()
        fake.register_environment("fake-env", "base-01")

        call_count = 0

        def flaky_check(applied):
            nonlocal call_count
            call_count += 1
            if not applied:
                return HealthCheckOutcome.PASS, "Baseline OK"
            # Attempt 1: FAIL, Attempt 2: PASS (disagreement)
            if call_count % 2 == 0:
                return HealthCheckOutcome.FAIL, "Flaky crash"
            return HealthCheckOutcome.PASS, "Flaky pass"

        fake.custom_check_callback = flaky_check

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="fake-env",
            baseline_id="base-01",
            storage=self.storage,
            investigation_id="inv-verif-1",
        )

        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[Change(id="c1", category="cfg", description="d", payload={})],
            config=VerificationProtocolConfig(repeat_count=2),
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.INCONCLUSIVE)
        self.assertIn("intermittent", res.confidence_rationale.lower())


class TestVerificationSafetyAndEnvironmentIntegrity(unittest.TestCase):
    """Test safety invariants: host refusal, non-disposable refusal, corruption lockout, and cancellation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_safety.db")
        self.storage = Storage(self.db_path)
        self.investigation = Investigation(id="inv-safe", symptom="Sym", target_system="Sys")
        self.storage.save_investigation(self.investigation)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_host_environment_refused(self):
        fake = FakeAdapter()
        # Register environment with is_host=True
        env = EnvironmentRecord(
            environment_id="host-env",
            adapter_type=AdapterType.FAKE,
            display_name="Host Machine",
            is_disposable=True,
            is_host=True,
            status=EnvironmentStatus.REGISTERED,
        )
        fake._environments["host-env"] = env

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="host-env",
            baseline_id="b-1",
            storage=self.storage,
            investigation_id="inv-safe",
        )
        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[Change(id="c1", category="c", description="d", payload={})],
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.INCONCLUSIVE)
        self.assertIn("host", res.confidence_rationale.lower())

    def test_non_disposable_environment_refused(self):
        fake = FakeAdapter()
        env = EnvironmentRecord(
            environment_id="prod-env",
            adapter_type=AdapterType.FAKE,
            display_name="Prod Non-Disposable",
            is_disposable=False,
            is_host=False,
            status=EnvironmentStatus.REGISTERED,
        )
        fake._environments["prod-env"] = env

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="prod-env",
            baseline_id="b-1",
            storage=self.storage,
            investigation_id="inv-safe",
        )
        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[Change(id="c1", category="c", description="d", payload={})],
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.INCONCLUSIVE)
        self.assertIn("disposable", res.confidence_rationale.lower())

    def test_corrupted_environment_refuses_verification(self):
        fake = FakeAdapter()
        fake.register_environment("corrupt-env", "b-1")
        fake._environments["corrupt-env"].status = EnvironmentStatus.CORRUPTED

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="corrupt-env",
            baseline_id="b-1",
            storage=self.storage,
            investigation_id="inv-safe",
        )
        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[Change(id="c1", category="c", description="d", payload={})],
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.ENVIRONMENT_CORRUPTED)
        self.assertIn("corrupted", res.confidence_rationale.lower())

    def test_cancellation_token_halts_verification(self):
        adapter = SimulationAdapter()
        token = CancellationToken()
        token.cancel("User aborted verification run.")

        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id="sim-env-default",
            baseline_id="baseline-001",
            storage=self.storage,
            investigation_id="inv-safe",
            cancellation_token=token,
        )
        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[Change(id="c1", category="c", description="d", payload={})],
            cancellation_token=token,
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.CANCELLED)
        self.assertIn("cancelled", res.confidence_rationale.lower())


class TestDifferentialValidationAndReversal(unittest.TestCase):
    """Test differential baseline comparison and safe reversal validation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_diff.db")
        self.storage = Storage(self.db_path)
        self.investigation = Investigation(id="inv-diff", symptom="Sym", target_system="Sys")
        self.storage.save_investigation(self.investigation)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_differential_baseline_comparison(self):
        fake = FakeAdapter()
        fake.register_environment("fake-env", "base-win10")
        fake.register_baseline("fake-env", "base-win11")

        # Define behavior: reproduces on base-win10, but fails to reproduce on base-win11
        current_baseline = "base-win10"

        def restore_hook(env_id, base_id):
            nonlocal current_baseline
            current_baseline = base_id
            return None

        fake.restore_hook = restore_hook

        def diff_check(applied):
            if applied and current_baseline == "base-win10":
                return HealthCheckOutcome.FAIL, "Win10 crash"
            return HealthCheckOutcome.PASS, "OK"

        fake.custom_check_callback = diff_check

        runner = IsolatedTrialRunner(
            adapter=fake,
            environment_id="fake-env",
            baseline_id="base-win10",
            storage=self.storage,
            investigation_id="inv-diff",
        )

        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[Change(id="c1", category="c", description="d", payload={})],
            config=VerificationProtocolConfig(
                differential_baseline_ids=["base-win11"]
            ),
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.VERIFIED_SET)
        self.assertIn("base-win11", res.differential_results)
        diff_info = res.differential_results["base-win11"]
        self.assertEqual(diff_info["status"], "TESTED")
        self.assertFalse(diff_info["reproduced"])  # Did not reproduce on Win11
        self.assertTrue(any("baseline-dependent" in lim.lower() for lim in res.limitations))

    def test_safe_reversal_validation(self):
        adapter = SimulationAdapter()
        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id="sim-env-default",
            baseline_id="baseline-001",
            storage=self.storage,
            investigation_id="inv-diff",
        )

        def mock_reversal(adp, env_id):
            # Reversal resets status key back to healthy
            ws = adp.get_workspace(env_id)
            if ws:
                ws._current_state["status"] = "healthy"
                ws._current_state["config.status"] = "healthy"
            return True

        change = Change(id="c-bad", category="cfg", description="Bad", payload={"key": "status", "value": "failed"})

        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[change],
            config=VerificationProtocolConfig(enable_reversal=True),
            reversal_action_fn=mock_reversal,
        )
        res = engine.verify()

        self.assertEqual(res.verdict, VerificationVerdict.VERIFIED_SET)
        self.assertTrue(res.reversal_tested)
        self.assertTrue(res.reversal_passed)


class TestVerificationReportingAndPersistence(unittest.TestCase):
    """Test text/JSON reporting and SQLite persistence round-trips."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_rep.db")
        self.storage = Storage(self.db_path)
        self.investigation = Investigation(id="inv-rep", symptom="Sym", target_system="Sys")
        self.storage.save_investigation(self.investigation)

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_text_and_json_report_formatting(self):
        adapter = SimulationAdapter()
        runner = IsolatedTrialRunner(
            adapter=adapter,
            environment_id="sim-env-default",
            baseline_id="baseline-001",
            storage=self.storage,
            investigation_id="inv-rep",
        )
        change = Change(id="c-bad", category="cfg", description="Bad", payload={"key": "status", "value": "failed"})

        engine = VerificationEngine(
            runner=runner,
            candidate_changes=[change],
        )
        res = engine.verify()

        text_rep = format_verification_text(res)
        self.assertIn("STATEBISECT EVIDENCE-BACKED VERIFICATION REPORT", text_rep)
        self.assertIn("VERIFIED_SET", text_rep)
        self.assertIn("NECESSITY ANALYSIS", text_rep)
        self.assertIn("c-bad", text_rep)

        json_rep = format_verification_json(res)
        parsed = json.loads(json_rep)
        self.assertEqual(parsed["verdict"], "VERIFIED_SET")
        self.assertTrue(parsed["is_1_minimal"])
        self.assertEqual(len(parsed["trials"]), len(res.trials))

        # Test persistence round-trip
        fetched = self.storage.get_verification_run(res.verification_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.verdict, VerificationVerdict.VERIFIED_SET)
        self.assertEqual(len(fetched.trials), len(res.trials))
        self.assertEqual(len(fetched.necessity_details), 1)

        # Test list_verification_runs
        runs = self.storage.list_verification_runs("inv-rep")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].verification_id, res.verification_id)


class TestVerificationCLIIntegration(unittest.TestCase):
    """Test CLI commands: preview-verification, run-verification, inspect-verification, export-verification."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_cli.db")
        self.storage = Storage(self.db_path)
        self.investigation = Investigation(id="inv-cli-v", symptom="Crash", target_system="TestSys")
        self.storage.save_investigation(self.investigation)

        # Register candidate changes
        from statebisect.correlation.models import CandidateChange, CandidateEvidenceType, CandidateReversibility
        cand = CandidateChange(
            candidate_id="cand-c1",
            investigation_id="inv-cli-v",
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="config",
            object_key="status",
            display_name="Failing status config",
            description="Causes crash",
            first_observed_utc="2026-03-30T10:00:00Z",
            last_observed_utc="2026-03-30T10:00:00Z",
            after_value="failed",
            score=25.0,
            reversibility=CandidateReversibility.SIMULATABLE,
        )
        self.storage.save_candidates("inv-cli-v", [cand])

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def run_cli(self, args: list[str]) -> tuple[int, str, str]:
        import io
        import sys
        from statebisect.cli import main

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        old_out = sys.stdout
        old_err = sys.stderr
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        try:
            code = main(args)
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
        return code, stdout_buf.getvalue(), stderr_buf.getvalue()

    def test_cli_verification_workflow(self):
        # 1. preview-verification
        code, out, _ = self.run_cli(["preview-verification", "inv-cli-v", "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn("STATEBISECT VERIFICATION PROTOCOL PREVIEW", out)
        self.assertIn("cand-c1", out)

        # 2. run-verification
        code, out, _ = self.run_cli(["run-verification", "inv-cli-v", "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn("STATEBISECT EVIDENCE-BACKED VERIFICATION REPORT", out)
        self.assertIn("VERIFIED_SET", out)
        self.assertIn("cand-c1", out)

        # Retrieve verification ID from database
        runs = self.storage.list_verification_runs("inv-cli-v")
        self.assertEqual(len(runs), 1)
        verif_id = runs[0].verification_id

        # 3. inspect-verification
        code, out, _ = self.run_cli(["inspect-verification", verif_id, "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED_SET", out)
        self.assertIn(verif_id, out)

        # 4. export-verification to file
        export_file = os.path.join(self.temp_dir.name, "report.json")
        code, out, _ = self.run_cli([
            "export-verification", verif_id,
            "--format", "json",
            "-o", export_file,
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(export_file))
        content = Path(export_file).read_text(encoding="utf-8")
        parsed = json.loads(content)
        self.assertEqual(parsed["verdict"], "VERIFIED_SET")


if __name__ == "__main__":
    unittest.main()
