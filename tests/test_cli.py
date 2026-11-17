"""Tests for the StateBisect Command-Line Interface (CLI)."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from statebisect.cli import main


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "cli_test.db")
        self.scenarios_dir = Path("scenarios").resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, args: list[str]) -> tuple[int, str, str]:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            code = main(args)
        return code, stdout_buf.getvalue(), stderr_buf.getvalue()

    def test_cli_help(self):
        code, out, _ = self.run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("StateBisect", out)
        self.assertIn("list-scenarios", out)

    def test_list_scenarios(self):
        code, out, _ = self.run_cli(["list-scenarios", str(self.scenarios_dir)])
        self.assertEqual(code, 0)
        self.assertIn("single_cause.json", out)
        self.assertIn("interaction.json", out)
        self.assertIn("invalid_scenario.json", out)
        self.assertIn("INVALID", out)

    def test_validate_scenario_valid(self):
        code, out, _ = self.run_cli(
            ["validate-scenario", str(self.scenarios_dir / "single_cause.json")]
        )
        self.assertEqual(code, 0)
        self.assertIn("Scenario is valid", out)
        self.assertIn("scenario-tls-downgrade", out)

    def test_validate_scenario_invalid(self):
        code, _, err = self.run_cli(
            ["validate-scenario", str(self.scenarios_dir / "invalid_scenario.json")]
        )
        self.assertEqual(code, 1)
        self.assertIn("Validation Error", err)

    def test_run_scenario_success(self):
        code, out, _ = self.run_cli(
            ["run-scenario", str(self.scenarios_dir / "single_cause.json"), "--db", self.db_path]
        )
        self.assertEqual(code, 0)
        self.assertIn("STATEBISECT INVESTIGATION REPORT", out)
        self.assertIn("Suspected Set    : C4", out)
        self.assertIn("1-Minimal?       : YES", out)

    def test_inspect_investigation(self):
        # First run a scenario
        self.run_cli(
            ["run-scenario", str(self.scenarios_dir / "single_cause.json"), "--db", self.db_path]
        )
        # Inspect database
        from statebisect.storage import Storage

        storage = Storage(self.db_path)
        invs = storage.list_investigations()
        storage.close()

        self.assertGreater(len(invs), 0)
        target_id = invs[0].id

        code, out, _ = self.run_cli(
            ["inspect-investigation", target_id, "--db", self.db_path]
        )
        self.assertEqual(code, 0)
        self.assertIn(f"INVESTIGATION: {target_id}", out)
        self.assertIn("TRIALS RECORDED", out)
        self.assertIn("AUDIT LOG EVENTS", out)

    def test_export_report_text_and_json(self):
        # Run scenario
        self.run_cli(
            ["run-scenario", str(self.scenarios_dir / "single_cause.json"), "--db", self.db_path]
        )
        from statebisect.storage import Storage

        storage = Storage(self.db_path)
        inv_id = storage.list_investigations()[0].id
        storage.close()

        # Text export to file
        report_file = Path(self.temp_dir.name) / "report.txt"
        code, out, _ = self.run_cli(
            ["export-report", inv_id, "--format", "text", "-o", str(report_file), "--db", self.db_path]
        )
        self.assertEqual(code, 0)
        self.assertTrue(report_file.exists())
        self.assertIn("STATEBISECT INVESTIGATION REPORT", report_file.read_text(encoding="utf-8"))

        # JSON export to stdout
        code, out, _ = self.run_cli(
            ["export-report", inv_id, "--format", "json", "--db", self.db_path]
        )
        self.assertEqual(code, 0)
        json_data = json.loads(out)
        self.assertEqual(json_data["report_version"], "1.0.0")
        self.assertEqual(json_data["findings"]["suspected_set"], ["C4"])

    def test_path_traversal_rejected_in_cli(self):
        code, _, err = self.run_cli(["validate-scenario", "../escape.json"])
        self.assertEqual(code, 1)
        self.assertIn("Path traversal", err)

    def test_cli_collect_preview(self):
        code, out, _ = self.run_cli(["collect-preview"])
        self.assertEqual(code, 0)
        self.assertIn("STATEBISECT WINDOWS COLLECTOR PREVIEW", out)
        self.assertIn("READ-ONLY GUARANTEE", out)
        self.assertIn("os_info", out)
        self.assertIn("installed_software", out)

    def test_cli_collect_windows_and_inspect_and_diff(self):
        # 1. Run collection with small limit
        code, out, err = self.run_cli([
            "collect-windows",
            "--db", self.db_path,
            "--max-records", "5",
            "--lookback-minutes", "10",
        ])
        self.assertEqual(code, 0)
        self.assertIn("COLLECTION RUN SUMMARY", out)
        self.assertIn("Run ID", out)

        # 2. Extract Run ID
        from statebisect.storage import Storage
        storage = Storage(self.db_path)
        runs = storage.list_collection_runs()
        storage.close()
        self.assertEqual(len(runs), 1)
        run_1_id = runs[0].run_id

        # 3. List collections
        code, out, _ = self.run_cli(["list-collections", "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn(run_1_id, out)

        # 4. Inspect collection
        code, out, _ = self.run_cli(["inspect-collection", run_1_id, "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn(f"COLLECTION RUN: {run_1_id}", out)
        self.assertIn("RECORDS SUMMARY BY CATEGORY:", out)

        # 5. Run a second collection for diffing
        code, _, _ = self.run_cli([
            "collect-windows",
            "--db", self.db_path,
            "--max-records", "5",
            "--lookback-minutes", "10",
        ])
        self.assertEqual(code, 0)
        storage = Storage(self.db_path)
        runs = storage.list_collection_runs()
        storage.close()
        self.assertEqual(len(runs), 2)
        run_2_id = runs[0].run_id

        # 6. Compare snapshots
        code, out, _ = self.run_cli([
            "compare-snapshots",
            run_1_id,
            run_2_id,
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn("SNAPSHOT COMPARISON MATRIX", out)
        self.assertIn(f"Before Run  : {run_1_id}", out)
        self.assertIn(f"After Run   : {run_2_id}", out)

    def test_cli_monitor_preview(self):
        code, out, _ = self.run_cli(["monitor-preview"])
        self.assertEqual(code, 0)
        self.assertIn("STATEBISECT TARGETED CHANGE MONITORING PREVIEW", out)
        self.assertIn("event_log", out)
        self.assertIn("registry_watcher", out)
        self.assertIn("sysmon", out)
        self.assertIn("etw", out)

    def test_cli_monitor_start_and_inspect(self):
        # 1. Run short 1s monitor session with specific source to keep fast
        code, out, _ = self.run_cli([
            "monitor-start",
            "--duration", "1",
            "--sources", "registry_watcher,etw",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn("MONITORING SESSION SUMMARY", out)
        self.assertIn("Session ID", out)

        # 2. Extract Session ID from database
        from statebisect.storage import Storage
        storage = Storage(self.db_path)
        sessions = storage.list_monitoring_sessions()
        storage.close()
        self.assertEqual(len(sessions), 1)
        sess_id = sessions[0].session_id

        # 3. List monitor sessions
        code, out, _ = self.run_cli(["list-monitor-sessions", "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn(sess_id, out)

        # 4. Inspect session
        code, out, _ = self.run_cli(["inspect-monitor-session", sess_id, "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn(f"MONITORING SESSION: {sess_id}", out)
        self.assertIn("STATUS", out.upper())

        # 5. Inspect session JSON
        code, out, _ = self.run_cli(["inspect-monitor-session", sess_id, "--format", "json", "--db", self.db_path])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["session_id"], sess_id)
        self.assertEqual(parsed["status"], "COMPLETED")

    def test_cli_phase6_investigation_and_candidates_workflow(self):
        from statebisect.storage import Storage
        from statebisect.collector.models import SnapshotDiff, DiffChangeType

        # 1. Create investigation via CLI
        code, out, _ = self.run_cli([
            "create-investigation",
            "--symptom", "Web service 503 error",
            "--target", "WebServer",
            "--onset", "2026-03-30 12:00:00",
            "--timezone", "UTC",
            "--notes", "Production failure test",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn("INVESTIGATION CREATED", out)

        # Retrieve investigation ID
        storage = Storage(self.db_path)
        invs = storage.list_investigations()
        self.assertEqual(len(invs), 1)
        inv_id = invs[0].id

        # 2. Seed snapshot collection runs and a diff in storage for this test
        from statebisect.collector.models import CollectionRun

        run_a = CollectionRun(
            run_id="run-a",
            created_at="2026-03-30T11:00:00Z",
            finished_at="2026-03-30T11:00:05Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="mach-1",
            status="SUCCESS",
            duration_ms=5000.0,
            records=[],
        )
        run_b = CollectionRun(
            run_id="run-b",
            created_at="2026-03-30T12:00:00Z",
            finished_at="2026-03-30T12:00:05Z",
            collector_version="1.0.0",
            os_platform="Windows-11",
            machine_id="mach-1",
            status="SUCCESS",
            duration_ms=5000.0,
            records=[],
        )
        storage.save_collection_run(run_a)
        storage.save_collection_run(run_b)

        diff = SnapshotDiff(
            diff_id="diff-cli-1",
            before_run_id="run-a",
            after_run_id="run-b",
            category="registry",
            object_key="HKLM\\Software\\WebServer\\Port",
            change_type=DiffChangeType.MODIFIED,
            before_data=8080,
            after_data=80,
            notes="Port changed",
        )
        storage.save_snapshot_diffs([diff])
        storage.close()

        # 3. Generate candidates
        code, out, _ = self.run_cli([
            "generate-candidates",
            inv_id,
            "--before-run", "run-a",
            "--after-run", "run-b",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn(f"CANDIDATE CHANGES GENERATED FOR: {inv_id}", out)
        self.assertIn("cand-diff-diff-cli-1", out)
        self.assertIn("Caution: Candidate scores indicate correlation", out)

        # 4. List candidates (text and json)
        code, out, _ = self.run_cli(["list-candidates", inv_id, "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn("cand-diff-diff-cli-1", out)
        self.assertIn("ACTIVE", out)

        code, out, _ = self.run_cli(["list-candidates", inv_id, "--format", "json", "--db", self.db_path])
        self.assertEqual(code, 0)
        parsed_cands = json.loads(out)
        self.assertEqual(len(parsed_cands), 1)
        cand_id = parsed_cands[0]["candidate_id"]

        # 5. Inspect candidate
        code, out, _ = self.run_cli(["inspect-candidate", cand_id, "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn(f"CANDIDATE CHANGE INSPECTION: {cand_id}", out)
        self.assertIn("SCORE BREAKDOWN (DETERMINISTIC):", out)

        code, out, _ = self.run_cli(["inspect-candidate", cand_id, "--format", "json", "--db", self.db_path])
        self.assertEqual(code, 0)
        cand_detail = json.loads(out)
        self.assertEqual(cand_detail["candidate_id"], cand_id)
        self.assertIn("score_breakdown", cand_detail)

        # 6. Review candidate: Exclude with audited reason
        code, out, _ = self.run_cli([
            "review-candidate",
            cand_id,
            "--exclude",
            "--reason", "Background configuration update unrelated to web server port",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn(f"CANDIDATE REVIEW RECORDED: {cand_id}", out)
        self.assertIn("New Status      : EXCLUDED (Active: False)", out)

        # Check active-only list is now empty
        code, out, _ = self.run_cli(["list-candidates", inv_id, "--active-only", "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn("No candidates found", out)

        # Check full list still includes candidate marked EXCLUDED
        code, out, _ = self.run_cli(["list-candidates", inv_id, "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn("EXCLUDED", out)

        # 7. Review candidate: Re-include with reason
        code, out, _ = self.run_cli([
            "review-candidate",
            cand_id,
            "--include",
            "--reason", "Further review suggests port change could cause bind failure",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn("New Status      : INCLUDED (Active: True)", out)

    def test_cli_phase7_isolated_adapters_and_trials_workflow(self):
        # 1. list-adapters
        code, out, _ = self.run_cli(["list-adapters"])
        self.assertEqual(code, 0)
        self.assertIn("STATEBISECT ISOLATED ENVIRONMENT ADAPTERS", out)
        self.assertIn("simulation", out)
        self.assertIn("[DEFAULT]", out)
        self.assertIn("hyperv", out)

        code, out, _ = self.run_cli(["list-adapters", "--format", "json"])
        self.assertEqual(code, 0)
        adp_data = json.loads(out)
        self.assertTrue(any(a["name"] == "simulation" for a in adp_data))

        # 2. sandbox-evaluation
        code, out, _ = self.run_cli(["sandbox-evaluation"])
        self.assertEqual(code, 0)
        self.assertIn("WINDOWS SANDBOX TECHNICAL EVALUATION", out)
        self.assertIn("DEFERRED", out)

        code, out, _ = self.run_cli(["sandbox-evaluation", "--format", "json"])
        self.assertEqual(code, 0)
        wsb_data = json.loads(out)
        self.assertEqual(wsb_data["status"], "DEFERRED")

        # 3. register-environment: host refusal
        code, out, err = self.run_cli([
            "register-environment",
            "--id", "env-host",
            "--name", "Host System",
            "--is-host",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 1)
        self.assertIn("Refusing to register host machine", err)

        # 4. register-environment: valid disposable simulation environment
        code, out, _ = self.run_cli([
            "register-environment",
            "--id", "sim-custom-1",
            "--name", "Custom Simulation Environment",
            "--is-disposable",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn("ENVIRONMENT REGISTERED: sim-custom-1", out)

        # 5. register-baseline
        code, out, _ = self.run_cli([
            "register-baseline",
            "--environment", "sim-custom-1",
            "--baseline-id", "base-clean-1",
            "--name", "Initial Clean State",
            "--checkpoint-ref", "sim-chk-001",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn("BASELINE REGISTERED: base-clean-1", out)

        # 6. inspect-environment
        code, out, _ = self.run_cli(["inspect-environment", "sim-custom-1", "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn("ENVIRONMENT INSPECTION: sim-custom-1", out)
        self.assertIn("base-clean-1", out)

        # 7. list-environments
        code, out, _ = self.run_cli(["list-environments", "--db", self.db_path])
        self.assertEqual(code, 0)
        self.assertIn("sim-custom-1", out)

        # 8. verify-baseline
        code, out, _ = self.run_cli([
            "verify-baseline",
            "--environment", "sim-env-default",
            "--baseline", "baseline-001",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn("BASELINE VERIFICATION PASSED", out)

        # 9. run-trial: baseline check (no changes) -> passes
        code, out, _ = self.run_cli([
            "run-trial",
            "--environment", "sim-env-default",
            "--baseline", "baseline-001",
            "--db", self.db_path,
        ])
        self.assertEqual(code, 0)
        self.assertIn("STARTING ISOLATED TRIAL", out)
        self.assertIn("Outcome          : PASS", out)
        self.assertIn("Lifecycle State  : COMPLETED", out)

        # 10. run-minimization with candidate changes
        from statebisect.storage import Storage
        from statebisect.models import Investigation, InvestigationStatus
        from statebisect.correlation.models import CandidateChange, CandidateEvidenceType, CandidateReversibility

        storage = Storage(self.db_path)
        try:
            inv = Investigation(
                id="inv-cli-min",
                symptom="Service failure on test config",
                target_system="TestService",
                status=InvestigationStatus.NEW,
                baseline_id="baseline-001",
            )
            storage.save_investigation(inv)

            cands = [
                CandidateChange(
                    candidate_id="cand-good-1",
                    investigation_id="inv-cli-min",
                    evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
                    category="config",
                    object_key="timeout",
                    display_name="Timeout config",
                    description="Timeout change",
                    first_observed_utc="2026-03-30T12:00:00Z",
                    last_observed_utc="2026-03-30T12:00:00Z",
                    after_value="30",
                    score=5.0,
                    reversibility=CandidateReversibility.SIMULATABLE,
                ),
                CandidateChange(
                    candidate_id="cand-bad-1",
                    investigation_id="inv-cli-min",
                    evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
                    category="config",
                    object_key="status",
                    display_name="Status config",
                    description="Status failure change",
                    first_observed_utc="2026-03-30T12:00:00Z",
                    last_observed_utc="2026-03-30T12:00:00Z",
                    after_value="failed",
                    score=10.0,
                    reversibility=CandidateReversibility.SIMULATABLE,
                ),
            ]
            storage.save_candidates("inv-cli-min", cands)
        finally:
            storage.close()

        code, out, err = self.run_cli([
            "run-minimization",
            "--investigation", "inv-cli-min",
            "--db", self.db_path,
        ])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("STATEBISECT ISOLATED DELTA DEBUGGING MINIMIZATION", out)
        self.assertIn("MINIMIZATION COMPLETED", out)
        self.assertIn("Result Verdict   : SUCCESS", out)
        self.assertIn("cand-bad-1", out)


if __name__ == "__main__":
    unittest.main()
