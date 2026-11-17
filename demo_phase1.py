"""Demonstration script for StateBisect Phase 1.

Demonstrates:
1. Single-cause configuration failure isolation across 8 candidate changes.
2. Two-change interaction isolation where a combination of changes causes failure.
3. Explicit inconclusive handling during health checks.
4. SQLite persistence and structured audit reporting.
"""

from __future__ import annotations

from statebisect.delta_debugging import DeltaDebugger
from statebisect.models import (
    Change,
    HealthCheckOutcome,
    HealthCheckResult,
    Investigation,
    InvestigationStatus,
)
from statebisect.reporting import generate_text_report
from statebisect.runner import ExperimentRunner
from statebisect.simulation import SimulatedWorkspace
from statebisect.storage import Storage


def run_single_cause_demo() -> None:
    print("\n" + "=" * 80)
    print("DEMO SCENARIO 1: SINGLE-CHANGE FAILURE ISOLATION (8 Candidates)")
    print("=" * 80)

    storage = Storage(":memory:")
    inv = Investigation(
        id="INV-DEMO-001",
        symptom="Payment service handshake fails with SSL/TLS error",
        target_system="PaymentGatewayService",
        status=InvestigationStatus.RUNNING,
    )
    storage.save_investigation(inv)

    baseline_state = {
        "tls_version": "TLSv1.3",
        "cipher_suites": ["ECDHE-RSA-AES128-GCM-SHA256", "ECDHE-RSA-AES256-GCM-SHA384"],
        "max_connections": 1000,
        "keepalive_sec": 60,
        "http2_enabled": True,
        "legacy_fallback": True,
        "log_level": "INFO",
        "auth_timeout_ms": 3000,
    }

    workspace = SimulatedWorkspace(baseline_state)
    runner = ExperimentRunner(workspace, storage, inv.id)

    # 8 candidate changes
    changes = [
        Change("C1", "network", "Increase auth timeout to 5000ms", payload={"auth_timeout_ms": 5000}),
        Change("C2", "logging", "Enable verbose trace logging", payload={"log_level": "TRACE"}),
        Change("C3", "network", "Bump keepalive to 120s", payload={"keepalive_sec": 120}),
        Change("C4", "crypto", "Drop legacy cipher suites", payload={"cipher_suites": ["CHACHA20-POLY1305"]}),  # ROOT CAUSE
        Change("C5", "performance", "Increase max connections to 2000", payload={"max_connections": 2000}),
        Change("C6", "network", "Set TLS min version to TLSv1.3", payload={"tls_version": "TLSv1.3"}),
        Change("C7", "network", "Disable HTTP2 push", payload={"http2_push": False}),
        Change("C8", "diagnostics", "Enable ETW provider trace", payload={"etw_trace": True}),
    ]

    def health_check(ws: SimulatedWorkspace) -> HealthCheckResult:
        # C4 alone triggers SSL handshake failure with downstream gateway
        ciphers = ws.current_state.get("cipher_suites", [])
        if "CHACHA20-POLY1305" in ciphers and len(ciphers) == 1:
            return HealthCheckResult(
                outcome=HealthCheckOutcome.FAIL,
                output="SSLHandshakeException: No matching cipher suite supported by remote endpoint",
            )
        return HealthCheckResult(
            outcome=HealthCheckOutcome.PASS,
            output="HTTP/1.1 200 OK: Downstream gateway health check passed",
        )

    debugger = DeltaDebugger(runner, changes, health_check)
    result = debugger.minimize([c.id for c in changes])

    inv.status = InvestigationStatus.VERIFIED if result.is_1_minimal else InvestigationStatus.FAILED
    storage.save_investigation(inv)

    report = generate_text_report(inv, result)
    print(report)


def run_interaction_demo() -> None:
    print("\n" + "=" * 80)
    print("DEMO SCENARIO 2: TWO-CHANGE INTERACTION FAILURE ISOLATION (8 Candidates)")
    print("=" * 80)

    storage = Storage(":memory:")
    inv = Investigation(
        id="INV-DEMO-002",
        symptom="Microservice reports intermittent connection reset after config refresh",
        target_system="OrderProcessingService",
        status=InvestigationStatus.RUNNING,
    )
    storage.save_investigation(inv)

    baseline_state = {
        "http2_strict": False,
        "legacy_protocol_fallback": True,
        "buffer_size_kb": 64,
        "compression": "gzip",
        "pool_size": 50,
        "tcp_nodelay": True,
        "dns_cache_ttl_sec": 300,
        "retry_limit": 3,
    }

    workspace = SimulatedWorkspace(baseline_state)
    runner = ExperimentRunner(workspace, storage, inv.id)

    # 8 candidate changes
    changes = [
        Change("C1", "perf", "Double connection pool to 100", payload={"pool_size": 100}),
        Change("C2", "perf", "Increase buffer to 128KB", payload={"buffer_size_kb": 128}),
        Change("C3", "protocol", "Enforce strict HTTP/2 protocol", payload={"http2_strict": True}),  # Interaction 1
        Change("C4", "perf", "Switch compression to zstd", payload={"compression": "zstd"}),
        Change("C5", "net", "Disable TCP NoDelay", payload={"tcp_nodelay": False}),
        Change("C6", "protocol", "Disable legacy protocol fallback", payload={"legacy_protocol_fallback": False}),  # Interaction 2
        Change("C7", "dns", "Lower DNS cache TTL to 60s", payload={"dns_cache_ttl_sec": 60}),
        Change("C8", "retry", "Increase retry limit to 5", payload={"retry_limit": 5}),
    ]

    def health_check(ws: SimulatedWorkspace) -> HealthCheckResult:
        state = ws.current_state
        # C3 alone is tolerated, C6 alone is tolerated.
        # But C3 AND C6 combined causes connection reset when legacy clients connect!
        if state.get("http2_strict") is True and state.get("legacy_protocol_fallback") is False:
            return HealthCheckResult(
                outcome=HealthCheckOutcome.FAIL,
                output="ERR_CONNECTION_RESET: Client unable to negotiate HTTP/2 with fallback disabled",
            )
        return HealthCheckResult(
            outcome=HealthCheckOutcome.PASS,
            output="Ready: Client successfully completed ping-pong request",
        )

    debugger = DeltaDebugger(runner, changes, health_check)
    result = debugger.minimize([c.id for c in changes])

    inv.status = InvestigationStatus.VERIFIED if result.is_1_minimal else InvestigationStatus.FAILED
    storage.save_investigation(inv)

    report = generate_text_report(inv, result)
    print(report)


def run_inconclusive_demo() -> None:
    print("\n" + "=" * 80)
    print("DEMO SCENARIO 3: EXPLICIT INCONCLUSIVE HANDLING")
    print("=" * 80)

    storage = Storage(":memory:")
    inv = Investigation(
        id="INV-DEMO-003",
        symptom="Flaky telemetry agent drops communication",
        target_system="TelemetryAgent",
        status=InvestigationStatus.RUNNING,
    )
    storage.save_investigation(inv)

    workspace = SimulatedWorkspace({"agent_mode": "normal"})
    runner = ExperimentRunner(workspace, storage, inv.id)

    changes = [
        Change("C1", "cfg", "Change polling interval", payload={"interval": 10}),
        Change("C2", "cfg", "Enable debug telemetry", payload={"debug": True}),
    ]

    def inconclusive_check(ws: SimulatedWorkspace) -> HealthCheckResult:
        if not ws.applied_change_ids:
            return HealthCheckResult(HealthCheckOutcome.PASS, "Baseline healthy")
        return HealthCheckResult(
            HealthCheckOutcome.INCONCLUSIVE,
            "Timeout waiting for agent RPC response (network jitter detected)",
        )

    debugger = DeltaDebugger(runner, changes, inconclusive_check)
    result = debugger.minimize([c.id for c in changes])

    inv.status = InvestigationStatus.INCONCLUSIVE
    storage.save_investigation(inv)

    report = generate_text_report(inv, result)
    print(report)


if __name__ == "__main__":
    run_single_cause_demo()
    run_interaction_demo()
    run_inconclusive_demo()
