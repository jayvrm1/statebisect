"""Comprehensive test suite for StateBisect Phase 9 local dashboard and API."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from statebisect.adapters.models import AdapterType, BaselineRecord, EnvironmentRecord, EnvironmentStatus, IsolatedTrialRecord, TrialLifecycleState
from statebisect.collector.models import CollectionRun, SourceStatus
from statebisect.correlation.models import (
    CandidateChange,
    CandidateEvidenceType,
    CandidateReversibility,
    CandidateReviewStatus,
    ProcessProvenance,
    ScoreBreakdown,
)
from statebisect.dashboard.handlers import DashboardApiRouter, validate_id
from statebisect.dashboard.server import DashboardServer, MAX_BODY_BYTES
from statebisect.models import HealthCheckOutcome, Investigation, InvestigationStatus, utc_now_iso
from statebisect.monitor.models import EventConfidence, MonitoringEvent, MonitoringSession, MonitoringSessionStatus
from statebisect.storage import Storage
from statebisect.verification.models import (
    TrialRole,
    VerificationNecessityDetail,
    VerificationRunRecord,
    VerificationTrialRecord,
    VerificationVerdict,
)


class TestDashboardServerAndApi(unittest.TestCase):
    """Tests for dashboard HTTP server, security boundaries, and REST API endpoints."""

    def setUp(self) -> None:
        self.storage = Storage(":memory:")
        # Seed test environment & baseline
        self.storage.register_environment(
            EnvironmentRecord(
                environment_id="sim-env-test",
                adapter_type=AdapterType.SIMULATION,
                display_name="Test Simulation",
                is_disposable=True,
                is_host=False,
                status=EnvironmentStatus.VERIFIED,
                created_at=utc_now_iso(),
            )
        )
        self.storage.register_baseline(
            BaselineRecord(
                baseline_id="baseline-test-01",
                environment_id="sim-env-test",
                display_name="Clean Baseline",
                is_verified=True,
                checksum_or_state="snap_v1",
                created_at=utc_now_iso(),
            )
        )

        # Seed an investigation
        self.inv = Investigation(
            id="inv-test-001",
            symptom="TLS Handshake timeout with upstream service",
            target_system="PaymentService",
            baseline_id="baseline-test-01",
            onset_time_utc="2026-03-30T14:00:00+00:00",
            status=InvestigationStatus.NEW,
        )
        self.storage.save_investigation(self.inv)

        # Seed candidate change
        self.cand = CandidateChange(
            candidate_id="cand-001",
            investigation_id=self.inv.id,
            evidence_type=CandidateEvidenceType.SNAPSHOT_DIFF,
            category="registry",
            object_key="HKLM\\System\\CurrentControlSet\\Control\\SecurityProviders\\SCHANNEL\\Protocols\\TLS 1.0\\Client\\Enabled",
            display_name="TLS Protocol",
            description="TLS 1.0 Enabled switched from 1 to 0",
            first_observed_utc="2026-03-30T13:58:00+00:00",
            last_observed_utc="2026-03-30T13:58:00+00:00",
            process_id=644,
            process_name="services.exe",
            process_provenance=ProcessProvenance.EVENT_PAYLOAD,
            reversibility=CandidateReversibility.SIMULATABLE,
            review_status=CandidateReviewStatus.INCLUDED,
            is_active=True,
            score=82.5,
            rank=1,
            score_breakdown=ScoreBreakdown(
                evidence_type_score=30.0,
                proximity_score=25.0,
                target_relevance_score=20.0,
                provenance_score=10.0,
                corroboration_score=0.0,
                gap_penalty=0.0,
                total_score=85.0,
                rationale="Observed close to onset with verified process provenance.",
            ),
            before_value="1",
            after_value="0",
        )
        self.storage.save_candidates(self.inv.id, [self.cand])

        # Seed an isolated trial
        self.trial = IsolatedTrialRecord(
            trial_id="trial-test-001",
            investigation_id=self.inv.id,
            environment_id="sim-env-test",
            baseline_id="baseline-test-01",
            adapter_type=AdapterType.SIMULATION,
            lifecycle_state=TrialLifecycleState.COMPLETED,
            is_simulation=True,
            selected_change_ids=["cand-001"],
            start_time="2026-03-30T14:05:00+00:00",
            finish_time="2026-03-30T14:05:01+00:00",
            outcome=HealthCheckOutcome.FAIL,
            output_bounded="TLSHandshakeException: Client aborted connection",
            duration_ms=45.2,
            restore_status="VERIFIED",
            cleanup_status="VERIFIED",
        )
        self.storage.save_isolated_trial(self.trial)

        # Seed a verification run
        self.verif = VerificationRunRecord(
            verification_id="verif-test-001",
            investigation_id=self.inv.id,
            environment_id="sim-env-test",
            baseline_id="baseline-test-01",
            verdict=VerificationVerdict.VERIFIED_SET,
            candidate_ids=["cand-001"],
            is_1_minimal=True,
            confidence_rationale="Candidate set ['cand-001'] reproduces failure and is strictly 1-minimal.",
            created_at="2026-03-30T14:10:00+00:00",
            finished_at="2026-03-30T14:10:02+00:00",
            trials=[
                VerificationTrialRecord(
                    trial_id="vtrial-01",
                    verification_id="verif-test-001",
                    trial_role=TrialRole.BASELINE_CONTROL,
                    target_change_ids=[],
                    baseline_id="baseline-test-01",
                    outcome=HealthCheckOutcome.PASS,
                    duration_ms=12.0,
                ),
                VerificationTrialRecord(
                    trial_id="vtrial-02",
                    verification_id="verif-test-001",
                    trial_role=TrialRole.CANDIDATE_SET,
                    target_change_ids=["cand-001"],
                    baseline_id="baseline-test-01",
                    outcome=HealthCheckOutcome.FAIL,
                    duration_ms=15.0,
                ),
            ],
            necessity_details=[
                VerificationNecessityDetail(
                    change_id="cand-001",
                    is_necessary=True,
                    outcome_without_change=HealthCheckOutcome.PASS,
                    explanation="Singleton candidate is 1-minimal.",
                )
            ],
            reversal_tested=True,
            reversal_passed=True,
        )
        self.storage.save_verification_run(self.verif)

        # Start live test server on ephemeral loopback port
        self.server = DashboardServer(storage=self.storage, host="127.0.0.1", port=0)
        self.server.start()
        self.base_url = self.server.url

    def tearDown(self) -> None:
        self.server.stop()
        self.storage.close()

    # --- 1. Loopback & Host Security Boundary Tests ---

    def test_remote_host_binding_rejected(self) -> None:
        """DashboardServer strictly rejects binding to non-loopback hosts."""
        with self.assertRaises(ValueError) as ctx:
            DashboardServer(storage=self.storage, host="0.0.0.0", port=8080)
        self.assertIn("Security violation", str(ctx.exception))
        self.assertIn("loopback", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx2:
            DashboardServer(storage=self.storage, host="192.168.1.100", port=8080)
        self.assertIn("Security violation", str(ctx2.exception))

    def test_invalid_host_header_rejected(self) -> None:
        """Requests with non-loopback Host headers are rejected with 400 Bad Request (DNS rebinding defense)."""
        req = urllib.request.Request(f"{self.base_url}/api/overview", headers={"Host": "evil-attacker.com"})
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTPError 400 for invalid Host header")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            err_data = json.loads(e.read().decode())
            self.assertEqual(err_data["code"], "INVALID_HOST")

    def test_untrusted_origin_rejected(self) -> None:
        """Requests with untrusted cross-origin Origin headers are rejected with 403 Forbidden."""
        req = urllib.request.Request(
            f"{self.base_url}/api/overview",
            headers={"Host": f"127.0.0.1:{self.server.port}", "Origin": "http://evil-site.com"},
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTPError 403 for cross-origin Origin header")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)
            err_data = json.loads(e.read().decode())
            self.assertEqual(err_data["code"], "CROSS_ORIGIN_FORBIDDEN")

    def test_post_without_csrf_header_rejected(self) -> None:
        """State-changing POST requests without anti-CSRF custom header are rejected with 403."""
        payload = json.dumps({"symptom": "crash", "target_system": "svc"}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/investigations",
            data=payload,
            method="POST",
            headers={"Host": f"127.0.0.1:{self.server.port}", "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTPError 403 for missing anti-CSRF header")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)
            err_data = json.loads(e.read().decode())
            self.assertEqual(err_data["code"], "CSRF_REJECTED")

    def test_security_headers_present(self) -> None:
        """API and static responses include defensive security headers."""
        req = urllib.request.Request(f"{self.base_url}/api/overview")
        with urllib.request.urlopen(req) as resp:
            headers = resp.headers
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(headers.get("X-Frame-Options"), "DENY")
            self.assertEqual(headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
            self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))
            self.assertIn("no-store", headers.get("Cache-Control", ""))

    def test_payload_too_large_rejected(self) -> None:
        """Payloads exceeding MAX_BODY_BYTES are rejected with 413 Payload Too Large."""
        oversized = b"x" * (MAX_BODY_BYTES + 1024)
        req = urllib.request.Request(
            f"{self.base_url}/api/investigations",
            data=oversized,
            method="POST",
            headers={
                "Host": f"127.0.0.1:{self.server.port}",
                "Content-Type": "application/json",
                "X-StateBisect-Request": "1",
            },
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTPError 413 for oversized payload")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 413)

    def test_path_traversal_guarded(self) -> None:
        """Path traversal attempts in static file requests are forbidden."""
        for path in ("/../../statebisect/storage.py", "/..\\..\\storage.py", "/etc/passwd"):
            req = urllib.request.Request(f"{self.base_url}{path}")
            try:
                urllib.request.urlopen(req)
                self.fail(f"Expected HTTPError 403 or 404 for traversal path {path}")
            except urllib.error.HTTPError as e:
                self.assertIn(e.code, (403, 404))

    # --- 2. API Functional Endpoint Tests ---

    def test_get_overview(self) -> None:
        """GET /api/overview returns system status, counts, transparency badges, and disclaimer."""
        req = urllib.request.Request(f"{self.base_url}/api/overview")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "ONLINE")
            self.assertIn("Absence of recent failure observations does NOT imply system health", data["health_notice"])
            self.assertGreaterEqual(data["counts"]["investigations"], 1)
            self.assertTrue(data["environment_transparency"]["has_simulation"])
            self.assertEqual(len(data["recent_investigations"]), 1)
            self.assertEqual(data["recent_investigations"][0]["id"], self.inv.id)

    def test_get_environments(self) -> None:
        """GET /api/environments returns registered environments and baselines."""
        req = urllib.request.Request(f"{self.base_url}/api/environments")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            envs = data["environments"]
            self.assertGreaterEqual(len(envs), 1)
            e = next(x for x in envs if x["environment_id"] == "sim-env-test")
            self.assertTrue(e["is_simulation"])
            self.assertEqual(len(e["baselines"]), 1)
            self.assertEqual(e["baselines"][0]["baseline_id"], "baseline-test-01")

    def test_create_and_list_investigations(self) -> None:
        """POST /api/investigations creates an investigation and GET /api/investigations lists it."""
        payload = json.dumps({
            "target_system": "AuthGateway",
            "symptom": "JWT token signature validation failure",
            "onset_time": "2026-03-30T15:00:00+00:00",
            "baseline_id": "baseline-test-01",
        }).encode("utf-8")

        # 1. Create with anti-CSRF header
        req = urllib.request.Request(
            f"{self.base_url}/api/investigations",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "X-StateBisect-Request": "1"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 201)
            created = json.loads(resp.read().decode())
            self.assertEqual(created["target_system"], "AuthGateway")
            new_id = created["id"]

        # 2. List with search filter
        req2 = urllib.request.Request(f"{self.base_url}/api/investigations?search=JWT")
        with urllib.request.urlopen(req2) as resp2:
            self.assertEqual(resp2.status, 200)
            data2 = json.loads(resp2.read().decode())
            self.assertEqual(data2["total"], 1)
            self.assertEqual(data2["investigations"][0]["id"], new_id)

    def test_create_investigation_validation(self) -> None:
        """POST /api/investigations validates required fields."""
        payload = json.dumps({"target_system": "", "symptom": ""}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/investigations",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "X-StateBisect-Request": "1"},
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected 400 for empty symptom")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            data = json.loads(e.read().decode())
            self.assertIn("required", data["error"])

    def test_get_investigation_detail(self) -> None:
        """GET /api/investigations/{id} returns investigation detail."""
        req = urllib.request.Request(f"{self.base_url}/api/investigations/{self.inv.id}")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["id"], self.inv.id)
            self.assertEqual(data["target_system"], "PaymentService")

    def test_get_investigation_timeline(self) -> None:
        """GET /api/investigations/{id}/timeline returns unified chronological timeline with post-onset markers."""
        req = urllib.request.Request(f"{self.base_url}/api/investigations/{self.inv.id}/timeline")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertGreaterEqual(data["total"], 3)
            items = data["items"]

            # Symptom onset item should exist
            symptom_item = next(x for x in items if x["event_type"] == "SYMPTOM_ONSET")
            self.assertFalse(symptom_item["is_post_onset"])

            # Trial item was at 14:05 (post-onset: onset was 14:00)
            trial_item = next(x for x in items if x["event_type"] == "EXPERIMENT_TRIAL")
            self.assertTrue(trial_item["is_post_onset"])
            self.assertEqual(trial_item["outcome"], "FAIL")

            # Verification item was at 14:10 (post-onset)
            verif_item = next(x for x in items if x["event_type"] == "VERIFICATION_RUN")
            self.assertTrue(verif_item["is_post_onset"])

    def test_get_candidates_and_review(self) -> None:
        """GET /api/investigations/{id}/candidates returns score breakdown and review audit works."""
        # 1. Fetch candidates
        req = urllib.request.Request(f"{self.base_url}/api/investigations/{self.inv.id}/candidates")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertIn("Ranking reflects experimental priority", data["disclaimer"])
            self.assertEqual(len(data["candidates"]), 1)
            cand = data["candidates"][0]
            self.assertEqual(cand["id"], "cand-001")
            self.assertEqual(cand["rank"], 1)
            self.assertIn("score_breakdown", cand)
            self.assertEqual(cand["score_breakdown"]["temporal_proximity_score"], 25.0)

        # 2. Exclude candidate without reason -> rejected with 400
        req_bad = urllib.request.Request(
            f"{self.base_url}/api/candidates/cand-001/review",
            data=json.dumps({"action": "exclude", "reason": ""}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-StateBisect-Request": "1"},
        )
        try:
            urllib.request.urlopen(req_bad)
            self.fail("Expected 400 for excluding candidate without reason")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            self.assertIn("audit reason", json.loads(e.read().decode())["error"])

        # 3. Exclude candidate with valid audit reason -> success
        req_good = urllib.request.Request(
            f"{self.base_url}/api/candidates/cand-001/review",
            data=json.dumps({"action": "exclude", "reason": "Verified benign scheduled policy change"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-StateBisect-Request": "1"},
        )
        with urllib.request.urlopen(req_good) as resp_good:
            self.assertEqual(resp_good.status, 200)
            updated = json.loads(resp_good.read().decode())
            self.assertEqual(updated["review_status"], "EXCLUDED")
            self.assertFalse(updated["is_active"])

    def test_get_trials_and_verifications(self) -> None:
        """GET trials and verification runs return full audit details."""
        # Trials
        req_t = urllib.request.Request(f"{self.base_url}/api/investigations/{self.inv.id}/trials")
        with urllib.request.urlopen(req_t) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["trials"][0]["health_outcome"], "FAIL")
            self.assertTrue(data["trials"][0]["is_baseline_verified"])

        # Verifications
        req_v = urllib.request.Request(f"{self.base_url}/api/investigations/{self.inv.id}/verifications")
        with urllib.request.urlopen(req_v) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["total"], 1)
            v = data["verification_runs"][0]
            self.assertEqual(v["verdict"], "VERIFIED_SET")
            self.assertEqual(len(v["trials"]), 2)
            self.assertEqual(len(v["necessity_details"]), 1)
            self.assertTrue(v["necessity_details"][0]["is_necessary"])

    def test_report_preview_and_download(self) -> None:
        """GET /report and /report/download return properly formatted text and JSON."""
        # Text Report
        req_txt = urllib.request.Request(f"{self.base_url}/api/investigations/{self.inv.id}/report?format=text")
        with urllib.request.urlopen(req_txt) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["format"], "text")
            self.assertIn("STATEBISECT EVIDENCE-BACKED VERIFICATION REPORT", data["content"])

        # JSON Download Attachment
        req_dl = urllib.request.Request(f"{self.base_url}/api/investigations/{self.inv.id}/report/download?format=json")
        with urllib.request.urlopen(req_dl) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
            self.assertIn(".json", resp.headers.get("Content-Disposition", ""))
            raw = json.loads(resp.read().decode())
            self.assertEqual(raw["verification_id"], "verif-test-001")

    def test_static_index_html_serving(self) -> None:
        """Static index.html and style.css are served with correct MIME types."""
        req = urllib.request.Request(f"{self.base_url}/")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            content = resp.read().decode()
            self.assertIn("StateBisect — Local Investigation Dashboard", content)

        req_css = urllib.request.Request(f"{self.base_url}/style.css")
        with urllib.request.urlopen(req_css) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/css", resp.headers.get("Content-Type", ""))


class TestDashboardExceptionHandling(unittest.TestCase):
    """Verify that exception handling rules are strictly adhered to."""

    def test_identifier_validation_rejects_injections(self) -> None:
        """validate_id strictly rejects directory traversal and command characters."""
        valid_ids = ["inv-123", "test_run_01", "cand-001.ext", "A1B2C3"]
        for vid in valid_ids:
            self.assertEqual(validate_id(vid), vid)

        invalid_ids = ["../etc/passwd", "inv;rm -rf", "test<script>", "inv space", ""]
        for iid in invalid_ids:
            with self.assertRaises(ValueError):
                validate_id(iid)


if __name__ == "__main__":
    unittest.main()
