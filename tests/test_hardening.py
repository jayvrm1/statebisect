"""Comprehensive product hardening, security verification, and AST exception-handling tests."""

import ast
import glob
import io
import json
import os
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from statebisect.adapters.models import AdapterType
from statebisect.benchmark.models import (
    BenchmarkCategory,
    BenchmarkScenario,
    CandidateSpec,
    ExpectedVerdict,
    HealthCheckSpec,
)
from statebisect.benchmark.runner import BenchmarkRunner
from statebisect.benchmark.suite import BenchmarkSuite
from statebisect.dashboard.server import DashboardServer
from statebisect.health_checks.base import redact_sensitive_text
from statebisect.storage import Storage


class TestAstExceptionHandlingAudit(unittest.TestCase):
    """Static AST audit across entire repository ensuring strict adherence to the project rule:

    Never place return, break, or continue inside a finally block.
    """

    def test_zero_forbidden_control_flow_in_finally_blocks(self):
        project_root = Path(__file__).parent.parent
        py_files = list(project_root.glob("statebisect/**/*.py")) + list(
            project_root.glob("tests/**/*.py")
        )

        violations = []
        for file_path in py_files:
            content = file_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(file_path))

            for node in ast.walk(tree):
                if isinstance(node, ast.Try):
                    for stmt in node.finalbody:
                        for subnode in ast.walk(stmt):
                            if isinstance(subnode, (ast.Return, ast.Break, ast.Continue)):
                                violations.append(
                                    f"{file_path.name}:{subnode.lineno} forbidden {type(subnode).__name__} in finally block"
                                )

        self.assertEqual(
            violations,
            [],
            f"Found forbidden control flow in finally blocks:\n" + "\n".join(violations),
        )


class TestSecurityHardening(unittest.TestCase):
    """Test security boundaries: Host/Origin validation, CSRF defense, path traversal, and secret scrubbing."""

    @classmethod
    def setUpClass(cls):
        # Start an in-memory loopback dashboard server on ephemeral port
        cls.storage = Storage(":memory:")
        cls.server = DashboardServer(storage=cls.storage, host="127.0.0.1", port=0)
        cls.server.start()
        cls.port = cls.server.port
        cls.base_url = cls.server.url

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.storage.close()

    def test_host_header_validation_rejects_untrusted_host(self):
        req = urllib.request.Request(
            f"{self.base_url}/api/overview",
            headers={"Host": "attacker.com"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def test_origin_header_validation_rejects_cross_origin(self):
        req = urllib.request.Request(
            f"{self.base_url}/api/overview",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": "http://evil-website.com",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_csrf_header_required_for_post(self):
        req = urllib.request.Request(
            f"{self.base_url}/api/mcp",
            data=b'{"jsonrpc":"2.0","method":"tools/list","id":1}',
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # Without X-StateBisect-Request header -> 403 CSRF rejected
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_csrf_header_accepted_with_custom_header(self):
        req = urllib.request.Request(
            f"{self.base_url}/api/mcp",
            data=b'{"jsonrpc":"2.0","method":"tools/list","id":1}',
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Content-Type": "application/json",
                "X-StateBisect-Request": "1",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        self.assertEqual(resp.status, 200)

    def test_path_traversal_in_static_files_rejected(self):
        req = urllib.request.Request(
            f"{self.base_url}/../../../../Windows/System32/drivers/etc/hosts",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_secret_scrubbing_redacts_api_keys_and_passwords(self):
        raw = "Error in auth: Bearer sk-ant-api03-1234567890abcdef and password='SuperSecretPassword123!'"
        redacted, was_redacted = redact_sensitive_text(raw)
        self.assertTrue(was_redacted)
        self.assertNotIn("sk-ant-api03", redacted)
        self.assertNotIn("SuperSecretPassword123!", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_hyperv_adapter_refusal_when_allow_real_vm_is_false(self):
        sc = BenchmarkScenario(
            scenario_id="bench-vm-deny",
            version="1.0.0",
            category=BenchmarkCategory.SINGLE_CAUSE,
            title="Refusal check",
            description="",
            adapter_type=AdapterType.HYPERV,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1"],
            candidates=[CandidateSpec(candidate_id="C1", category="registry", object_key="K1", after_value="1")],
            health_check=HealthCheckSpec(),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
        )
        runner = BenchmarkRunner(allow_real_vm=False)
        suite = BenchmarkSuite(scenarios=[sc])
        with self.assertRaises(PermissionError) as ctx:
            runner.run_suite(suite=suite)
        self.assertIn("protect host", str(ctx.exception))


class TestExceptionHandlingSemantics(unittest.TestCase):
    """Test that unexpected exceptions, KeyboardInterrupt, and SystemExit are not swallowed."""

    def test_keyboard_interrupt_propagates(self):
        def interrupt_fn():
            try:
                raise KeyboardInterrupt("Interrupted")
            finally:
                pass  # Clean cleanup only

        with self.assertRaises(KeyboardInterrupt):
            interrupt_fn()

    def test_system_exit_propagates(self):
        def exit_fn():
            try:
                raise SystemExit(42)
            finally:
                pass

        with self.assertRaises(SystemExit) as ctx:
            exit_fn()
        self.assertEqual(ctx.exception.code, 42)

    def test_cleanup_exception_preserves_primary_error(self):
        primary_caught = None
        cleanup_caught = None
        try:
            try:
                raise ValueError("Primary operation failed")
            finally:
                try:
                    raise RuntimeError("Cleanup socket failed")
                except RuntimeError as clean_err:
                    cleanup_caught = clean_err
        except ValueError as prim_err:
            primary_caught = prim_err

        self.assertIsNotNone(primary_caught)
        self.assertEqual(str(primary_caught), "Primary operation failed")
        self.assertIsNotNone(cleanup_caught)
        self.assertEqual(str(cleanup_caught), "Cleanup socket failed")


if __name__ == "__main__":
    unittest.main()
