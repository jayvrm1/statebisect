"""Comprehensive unit and integration tests for Phase 3 health checks and evidence model."""

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from statebisect.health_checks import (
    BaseHealthCheck,
    HealthCheckConfigurationError,
    LoopbackHttpCheck,
    ProcessRegistry,
    RegisteredProcessCheck,
    SafeLoopbackRedirectHandler,
    SimulatedStateCheck,
    bound_evidence,
    create_health_check,
    redact_sensitive_text,
    sanitize_and_bound_evidence,
)
from statebisect.models import (
    HealthCheckAttempt,
    HealthCheckOutcome,
    HealthCheckResult,
    Investigation,
    InvestigationStatus,
)
from statebisect.runner import ExperimentRunner
from statebisect.simulation import SimulatedWorkspace
from statebisect.storage import Storage


class TestRedactionAndBounding(unittest.TestCase):
    def test_secret_redaction(self):
        raw = "User password=SuperSecretPassword123 with api_key=xyz987654 and bearer eyJhbGciOi..."
        redacted, was_red = redact_sensitive_text(raw)
        self.assertTrue(was_red)
        self.assertNotIn("SuperSecretPassword123", redacted)
        self.assertNotIn("xyz987654", redacted)
        self.assertIn("password=[REDACTED]", redacted)
        self.assertIn("api_key=[REDACTED]", redacted)
        self.assertIn("bearer [REDACTED]", redacted)

    def test_evidence_length_bounding(self):
        long_text = "A" * 10000
        bounded, was_trunc = bound_evidence(long_text, max_length=200)
        self.assertTrue(was_trunc)
        self.assertLess(len(bounded), 500)
        self.assertIn("TRUNCATED", bounded)


class TestSimulatedStateCheck(unittest.TestCase):
    def setUp(self):
        self.ws = SimulatedWorkspace(baseline_state={"port": 80, "mode": "prod"})

    def test_key_value_match_pass_and_fail(self):
        check = SimulatedStateCheck(
            parameters={"key": "port", "expected_value": 8080}
        )
        # Port is 80 (not 8080) -> PASS
        res = check.run(self.ws)
        self.assertEqual(res.outcome, HealthCheckOutcome.PASS)

        # Mutate port to 8080 in workspace -> FAIL
        self.ws._current_state["port"] = 8080
        res = check.run(self.ws)
        self.assertEqual(res.outcome, HealthCheckOutcome.FAIL)
        self.assertEqual(len(res.attempts), 1)

    def test_compound_condition(self):
        check = SimulatedStateCheck(
            parameters={
                "conditions": [
                    {"key": "port", "op": "eq", "value": 8080},
                    {"key": "mode", "op": "eq", "value": "dev"},
                ]
            }
        )
        res = check.run(self.ws)
        self.assertEqual(res.outcome, HealthCheckOutcome.PASS)

        self.ws._current_state["port"] = 8080
        self.ws._current_state["mode"] = "dev"
        res = check.run(self.ws)
        self.assertEqual(res.outcome, HealthCheckOutcome.FAIL)


class TestRepetitionAndFlakiness(unittest.TestCase):
    def test_consistent_repeated_check(self):
        class FlawlessCheck(BaseHealthCheck):
            def execute_attempt(self, ws, attempt_number):
                return HealthCheckAttempt(
                    attempt_number=attempt_number,
                    outcome=HealthCheckOutcome.PASS,
                    start_time="now",
                    finish_time="now",
                    duration_ms=1.0,
                )

        check = FlawlessCheck(check_type="simulated_state", repeat_count=3)
        res = check.run(SimulatedWorkspace())
        self.assertEqual(res.outcome, HealthCheckOutcome.PASS)
        self.assertEqual(len(res.attempts), 3)

    def test_intermittent_check_yields_inconclusive(self):
        class IntermittentCheck(BaseHealthCheck):
            def __init__(self):
                super().__init__(check_type="simulated_state", repeat_count=3)
                self.calls = 0

            def execute_attempt(self, ws, attempt_number):
                self.calls += 1
                outcome = HealthCheckOutcome.PASS if self.calls % 2 != 0 else HealthCheckOutcome.FAIL
                return HealthCheckAttempt(
                    attempt_number=attempt_number,
                    outcome=outcome,
                    start_time="now",
                    finish_time="now",
                    duration_ms=1.0,
                )

        check = IntermittentCheck()
        res = check.run(SimulatedWorkspace())
        # Intermittent pass and fail across attempts must return INCONCLUSIVE
        self.assertEqual(res.outcome, HealthCheckOutcome.INCONCLUSIVE)
        self.assertIn("Intermittent / inconsistent result", res.reason)
        self.assertEqual(len(res.attempts), 3)


class TestProcessCheckSafety(unittest.TestCase):
    def setUp(self):
        ProcessRegistry.clear()

    def tearDown(self):
        ProcessRegistry.clear()

    def test_disabled_by_default(self):
        check = RegisteredProcessCheck(
            parameters={"executable_id": "python_test", "args": ["-c", "print(1)"]}
        )
        res = check.run(SimulatedWorkspace())
        self.assertEqual(res.outcome, HealthCheckOutcome.INCONCLUSIVE)
        self.assertIn("disabled by safety policy", res.output)

    def test_registered_executable_execution(self):
        # Register python executable
        ProcessRegistry.register_executable("py_helper", sys.executable)
        ProcessRegistry.enable()

        check = RegisteredProcessCheck(
            parameters={
                "executable_id": "py_helper",
                "args": ["-c", "import sys; sys.exit(0)"],
                "expected_exit_code": 0,
            }
        )
        res = check.run(SimulatedWorkspace())
        self.assertEqual(res.outcome, HealthCheckOutcome.PASS)
        self.assertEqual(len(res.attempts), 1)
        self.assertEqual(res.attempts[0].exit_code, 0)

    def test_unregistered_executable_rejected(self):
        ProcessRegistry.enable()
        check = RegisteredProcessCheck(
            parameters={"executable_id": "unregistered_malicious", "args": []}
        )
        res = check.run(SimulatedWorkspace())
        self.assertEqual(res.outcome, HealthCheckOutcome.INCONCLUSIVE)
        self.assertIn("not in the trusted ProcessRegistry", res.output)


class TestLoopbackHttpCheckSafety(unittest.TestCase):
    def test_non_loopback_host_rejected(self):
        check = LoopbackHttpCheck(
            parameters={"url": "http://example.com/api/health"}
        )
        res = check.run(SimulatedWorkspace())
        self.assertEqual(res.outcome, HealthCheckOutcome.INCONCLUSIVE)
        self.assertIn("not a loopback address", res.output)

    def test_non_loopback_redirect_rejected(self):
        class RedirectMockHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                # Attempt redirect to external domain
                self.send_response(302)
                self.send_header("Location", "http://evil.external.com/stolen")
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), RedirectMockHandler)
        port = server.server_port
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        try:
            check = LoopbackHttpCheck(
                parameters={"url": f"http://127.0.0.1:{port}/redirect"}
            )
            res = check.run(SimulatedWorkspace())
            self.assertEqual(res.outcome, HealthCheckOutcome.INCONCLUSIVE)
            self.assertIn("non-loopback host", res.output)
        finally:
            server.server_close()


class TestEvidencePersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "evidence_test.db"
        self.storage = Storage(self.db_path)
        self.inv = Investigation(id="inv-ev", symptom="Symptom", target_system="System")
        self.storage.save_investigation(self.inv)
        self.workspace = SimulatedWorkspace(baseline_state={"port": 80})

    def tearDown(self):
        self.storage.close()
        self.temp_dir.cleanup()

    def test_trials_and_evidence_persisted(self):
        check = SimulatedStateCheck(
            repeat_count=2,
            parameters={"key": "port", "expected_value": 8080},
        )
        runner = ExperimentRunner(self.workspace, self.storage, self.inv.id)
        trial = runner.run_trial([], check)

        self.assertEqual(trial.outcome, HealthCheckOutcome.PASS)
        self.assertEqual(len(trial.attempts), 2)

        # Retrieve trials from storage
        saved_trials = self.storage.get_trials(self.inv.id)
        self.assertEqual(len(saved_trials), 1)
        self.assertEqual(saved_trials[0].trial_id, trial.trial_id)
        self.assertEqual(len(saved_trials[0].attempts), 2)
        self.assertEqual(saved_trials[0].check_type, "simulated_state")
        self.assertTrue(saved_trials[0].parameters_hash)


if __name__ == "__main__":
    unittest.main()
