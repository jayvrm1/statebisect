"""Safe, loopback-restricted HTTP GET health check."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..models import HealthCheckAttempt, HealthCheckOutcome, utc_now_iso
from ..simulation import SimulatedWorkspace
from .base import BaseHealthCheck, sanitize_and_bound_evidence

LOOPBACK_HOSTS = frozenset(["localhost", "127.0.0.1", "::1", "[::1]"])
MAX_RESPONSE_BYTES = 16384  # 16 KB max response payload


class LoopbackSecurityViolation(Exception):
    """Raised when an HTTP request or redirect violates loopback restrictions."""


class SafeLoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTP redirect handler that strictly prevents redirection to non-loopback destinations."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        hostname = (parsed.hostname or "").lower()
        if hostname not in LOOPBACK_HOSTS:
            raise LoopbackSecurityViolation(
                f"Redirect to non-loopback host '{hostname}' rejected for security."
            )
        # Force GET method on redirects
        m = req.get_method()
        if (
            code in (301, 302, 303, 307, 308)
            and m in ("GET", "HEAD")
            or code in (301, 302, 303)
            and m == "POST"
        ):
            new_headers = dict(
                (k, v)
                for k, v in req.headers.items()
                if k.lower() not in ("content-length", "content-type", "cookie", "authorization")
            )
            return urllib.request.Request(
                newurl,
                headers=new_headers,
                origin_req_host=req.origin_req_host,
                unverifiable=True,
                method="GET",
            )
        return None


class LoopbackHttpCheck(BaseHealthCheck):
    """Executes a strictly bounded GET request against a local loopback endpoint."""

    def __init__(
        self,
        check_type: str = "loopback_http",
        check_version: str = "1.0.0",
        name: str = "Loopback HTTP Check",
        description: str = "Sends a read-only GET request to a local loopback test endpoint",
        timeout_ms: int = 5000,
        repeat_count: int = 1,
        required_consistency: str = "ALL_MATCH",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            check_type=check_type,
            check_version=check_version,
            name=name,
            description=description,
            timeout_ms=timeout_ms,
            repeat_count=repeat_count,
            required_consistency=required_consistency,
            parameters=parameters,
        )

    def execute_attempt(self, ws: SimulatedWorkspace, attempt_number: int) -> HealthCheckAttempt:
        start_time = utc_now_iso()
        t0 = time.perf_counter()

        url = self.parameters.get("url", "")
        if not url:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason="Missing 'url' parameter.",
                bounded_evidence="No URL provided.",
            )

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason=f"Unsupported URL scheme '{parsed.scheme}'. Only HTTP/HTTPS is permitted.",
                bounded_evidence=f"Invalid scheme: {parsed.scheme}",
            )

        hostname = (parsed.hostname or "").lower()
        if hostname not in LOOPBACK_HOSTS:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            msg = f"Security violation: Host '{hostname}' is not a loopback address. Only localhost/127.0.0.1/[::1] permitted."
            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                error_reason=msg,
                bounded_evidence=msg,
            )

        expected_status = self.parameters.get("expected_status_code", 200)
        failing_status = self.parameters.get("failing_status_code")
        expected_body = self.parameters.get("expected_body_substring")
        timeout_sec = self.timeout_ms / 1000.0

        # Build custom opener with SafeLoopbackRedirectHandler and NO cookies/auth
        opener = urllib.request.build_opener(SafeLoopbackRedirectHandler())
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "StateBisect-HealthCheck/1.0", "Accept": "*/*"},
            method="GET",
        )

        status_code = None
        response_body = ""
        error_msg = ""

        try:
            with opener.open(req, timeout=timeout_sec) as resp:
                status_code = resp.status
                raw_bytes = resp.read(MAX_RESPONSE_BYTES)
                response_body = raw_bytes.decode("utf-8", errors="replace")
        except LoopbackSecurityViolation as exc:
            error_msg = f"Security violation: {exc}"
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            try:
                response_body = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
            except Exception:
                response_body = str(exc)
        except urllib.error.URLError as exc:
            error_msg = f"Network connection error: {exc.reason}"
        except Exception as exc:
            error_msg = f"HTTP request exception: {type(exc).__name__}: {exc}"

        duration_ms = (time.perf_counter() - t0) * 1000.0

        if error_msg:
            # Network connect failure / connection refused
            # If failing_on_connection_error is set in parameters, treat as symptom reproduced
            if self.parameters.get("fail_on_connection_error") is True:
                outcome = HealthCheckOutcome.FAIL
                bounded_ev, was_trunc, was_red = sanitize_and_bound_evidence(error_msg)
            else:
                outcome = HealthCheckOutcome.INCONCLUSIVE
                bounded_ev, was_trunc, was_red = sanitize_and_bound_evidence(error_msg)

            return HealthCheckAttempt(
                attempt_number=attempt_number,
                outcome=outcome,
                start_time=start_time,
                finish_time=utc_now_iso(),
                duration_ms=round(duration_ms, 2),
                exit_code=status_code,
                expected_value=f"status={expected_status}",
                observed_value=f"error: {error_msg}",
                bounded_evidence=bounded_ev,
                error_reason=error_msg,
                is_truncated=was_trunc,
                is_redacted=was_red,
            )

        evidence_summary = f"HTTP Status: {status_code}\nBody: {response_body[:200]}"
        bounded_ev, was_trunc, was_red = sanitize_and_bound_evidence(evidence_summary)

        # Outcome determination
        if failing_status is not None and status_code == failing_status:
            outcome = HealthCheckOutcome.FAIL
        elif status_code == expected_status:
            if expected_body and expected_body not in response_body:
                outcome = HealthCheckOutcome.FAIL
            else:
                outcome = HealthCheckOutcome.PASS
        else:
            outcome = HealthCheckOutcome.FAIL

        return HealthCheckAttempt(
            attempt_number=attempt_number,
            outcome=outcome,
            start_time=start_time,
            finish_time=utc_now_iso(),
            duration_ms=round(duration_ms, 2),
            exit_code=status_code,
            expected_value=f"status={expected_status}",
            observed_value=f"status={status_code}",
            bounded_evidence=bounded_ev,
            is_truncated=was_trunc,
            is_redacted=was_red,
        )
