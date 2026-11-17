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


if __name__ == "__main__":
    unittest.main()
