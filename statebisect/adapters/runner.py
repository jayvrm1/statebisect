"""Isolated trial runner orchestrating baseline verification, action application, and cleanup."""

from __future__ import annotations

import sys
import time
import uuid
from typing import Any, TYPE_CHECKING

from statebisect.models import (
    CancellationToken,
    Change,
    HealthCheckOutcome,
    HealthCheckResult,
    utc_now_iso,
)

if TYPE_CHECKING:
    from statebisect.storage import Storage

from .base import BaseEnvironmentAdapter
from .models import (
    AdapterOperationResult,
    AdapterType,
    EnvironmentStatus,
    IsolatedTrialRecord,
    TrialLifecycleState,
)
from .registry import RegisteredActionRegistry


class IsolatedTrialRunner:
    """Orchestrates trials against an isolated environment adapter with a strict state machine."""

    def __init__(
        self,
        adapter: BaseEnvironmentAdapter,
        environment_id: str,
        baseline_id: str,
        storage: Storage,
        investigation_id: str,
        cancellation_token: CancellationToken | None = None,
        action_registry: RegisteredActionRegistry | None = None,
    ) -> None:
        self.adapter = adapter
        self.environment_id = environment_id
        self.baseline_id = baseline_id
        self.storage = storage
        self.investigation_id = investigation_id
        self.cancellation_token = cancellation_token or CancellationToken()
        self.action_registry = action_registry or RegisteredActionRegistry()

    def run_isolated_trial(
        self,
        selected_changes: list[Any],
        health_check_id: str = "default",
        health_check_params: dict[str, Any] | None = None,
        notes: str = "",
    ) -> IsolatedTrialRecord:
        """Execute a single trial enforcing baseline restore and verification before and after."""
        trial_id = f"iso-trial-{uuid.uuid4().hex[:8]}"
        start_iso = utc_now_iso()
        t0 = time.perf_counter()

        change_ids = [
            getattr(c, "candidate_id", getattr(c, "id", str(c))) for c in selected_changes
        ]

        lifecycle_state = TrialLifecycleState.VALIDATING
        outcome = HealthCheckOutcome.INCONCLUSIVE
        output_bounded = ""
        restore_status = "UNKNOWN"
        verify_status = "UNKNOWN"
        apply_status = "UNKNOWN"
        test_status = "UNKNOWN"
        cleanup_status = "UNKNOWN"
        error_reason: str | None = None
        evidence_data: dict[str, Any] = {}

        primary_exception: BaseException | None = None
        cleanup_exception: BaseException | None = None

        # 1. Check cancellation before launch
        if self.cancellation_token.is_cancelled:
            finish_iso = utc_now_iso()
            record = IsolatedTrialRecord(
                trial_id=trial_id,
                investigation_id=self.investigation_id,
                environment_id=self.environment_id,
                baseline_id=self.baseline_id,
                adapter_type=self.adapter.get_adapter_type(),
                lifecycle_state=TrialLifecycleState.CANCELLED,
                is_simulation=(self.adapter.get_adapter_type() == AdapterType.SIMULATION),
                selected_change_ids=change_ids,
                start_time=start_iso,
                finish_time=finish_iso,
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                output_bounded=f"Trial skipped: {self.cancellation_token.reason}",
                duration_ms=0.0,
                error_reason=self.cancellation_token.reason,
            )
            self._persist_trial(record)
            return record

        try:
            # 2. VALIDATING Environment & Baseline
            env = self.adapter.inspect_environment(self.environment_id)
            if not env:
                raise ValueError(f"Environment '{self.environment_id}' not found.")
            env.validate_for_execution()

            base = self.adapter.inspect_baseline(self.environment_id, self.baseline_id)
            if not base:
                raise ValueError(
                    f"Baseline '{self.baseline_id}' not found for environment '{self.environment_id}'."
                )

            # 3. RESTORING Baseline before changes
            lifecycle_state = TrialLifecycleState.RESTORING
            restore_res = self.adapter.restore_baseline(self.environment_id, self.baseline_id)
            if not restore_res.success:
                restore_status = f"FAILED: {restore_res.error_reason}"
                lifecycle_state = TrialLifecycleState.ENVIRONMENT_CORRUPTED
                outcome = HealthCheckOutcome.INCONCLUSIVE
                output_bounded = f"Pre-trial baseline restore failed: {restore_res.error_reason}"
                error_reason = restore_res.error_reason
                env.status = EnvironmentStatus.CORRUPTED
                # Halts immediately before applying any change
                return self._finalize_trial_record(
                    trial_id, start_iso, t0, change_ids, lifecycle_state, outcome,
                    output_bounded, restore_status, verify_status, apply_status,
                    test_status, cleanup_status, error_reason, evidence_data
                )
            restore_status = "SUCCESS"

            # 4. BASELINE_VERIFIED before changes
            lifecycle_state = TrialLifecycleState.BASELINE_VERIFIED
            verify_res = self.adapter.verify_baseline(self.environment_id, self.baseline_id)
            if not verify_res.success:
                verify_status = f"FAILED: {verify_res.error_reason}"
                lifecycle_state = TrialLifecycleState.ENVIRONMENT_CORRUPTED
                outcome = HealthCheckOutcome.INCONCLUSIVE
                output_bounded = f"Pre-trial baseline verification failed: {verify_res.error_reason}"
                error_reason = verify_res.error_reason
                env.status = EnvironmentStatus.CORRUPTED
                return self._finalize_trial_record(
                    trial_id, start_iso, t0, change_ids, lifecycle_state, outcome,
                    output_bounded, restore_status, verify_status, apply_status,
                    test_status, cleanup_status, error_reason, evidence_data
                )
            verify_status = "SUCCESS"

            # Check cancellation
            if self.cancellation_token.is_cancelled:
                lifecycle_state = TrialLifecycleState.CANCELLED
                output_bounded = f"Cancelled after verification: {self.cancellation_token.reason}"
                return self._finalize_trial_record(
                    trial_id, start_iso, t0, change_ids, lifecycle_state, outcome,
                    output_bounded, restore_status, verify_status, apply_status,
                    test_status, cleanup_status, error_reason, evidence_data
                )

            # 5. APPLYING Registered Candidate Actions
            lifecycle_state = TrialLifecycleState.APPLYING
            applied_any = False
            for chg in selected_changes:
                cid = getattr(chg, "candidate_id", getattr(chg, "id", str(chg)))
                params = getattr(chg, "parameters", {})
                if not params and getattr(chg, "payload", None):
                    params = dict(chg.payload)
                    if hasattr(chg, "category") and "category" not in params:
                        params["category"] = chg.category
                if not params and hasattr(chg, "category"):
                    params = {"category": chg.category, "key": getattr(chg, "object_key", getattr(chg, "key", cid)), "value": getattr(chg, "after_value", getattr(chg, "value", None))}

                # Validate against action allowlist
                action_id = params.get("action_id", "simulated_state_set")
                self.action_registry.validate_action_invocation(action_id, params)

                apply_res = self.adapter.apply_registered_change(self.environment_id, cid, params)
                if not apply_res.success:
                    apply_status = f"FAILED on {cid}: {apply_res.error_reason}"
                    outcome = HealthCheckOutcome.INCONCLUSIVE
                    output_bounded = f"Applying change '{cid}' failed: {apply_res.error_reason}"
                    error_reason = apply_res.error_reason
                    return self._finalize_trial_record(
                        trial_id, start_iso, t0, change_ids, lifecycle_state, outcome,
                        output_bounded, restore_status, verify_status, apply_status,
                        test_status, cleanup_status, error_reason, evidence_data
                    )
                applied_any = True
            apply_status = "SUCCESS" if applied_any or not selected_changes else "NOOP"

            # 6. TESTING Health Check
            lifecycle_state = TrialLifecycleState.TESTING
            hc_params = health_check_params or {}
            hc_result = self.adapter.run_health_check(self.environment_id, health_check_id, hc_params)
            outcome = hc_result.outcome
            output_bounded = hc_result.output[:4096]
            test_status = "SUCCESS"

            evidence_data = self.adapter.collect_trial_evidence(self.environment_id, trial_id)

            if outcome == HealthCheckOutcome.PASS:
                lifecycle_state = TrialLifecycleState.COMPLETED
            elif outcome == HealthCheckOutcome.FAIL:
                lifecycle_state = TrialLifecycleState.FAILED
            else:
                lifecycle_state = TrialLifecycleState.INCONCLUSIVE

        except (KeyboardInterrupt, SystemExit) as fatal_err:
            primary_exception = fatal_err
            lifecycle_state = TrialLifecycleState.CANCELLED
            outcome = HealthCheckOutcome.INCONCLUSIVE
            output_bounded = f"Trial aborted by user interrupt: {type(fatal_err).__name__}"
            error_reason = str(fatal_err)
        except Exception as err:
            primary_exception = err
            lifecycle_state = TrialLifecycleState.FAILED
            outcome = HealthCheckOutcome.INCONCLUSIVE
            output_bounded = f"Trial execution error: {type(err).__name__}: {err}"
            error_reason = str(err)
        finally:
            # 7. CLEANING_UP: Always attempt cleanup and baseline restore in finally block
            # Mandatory rule: Never use return, break, or continue in finally.
            try:
                cleanup_res = self.adapter.cleanup_trial(self.environment_id, trial_id)
                if cleanup_res and not cleanup_res.success:
                    cleanup_status = f"FAILED: {cleanup_res.error_reason}"
                    lifecycle_state = TrialLifecycleState.ENVIRONMENT_CORRUPTED
                    outcome = HealthCheckOutcome.INCONCLUSIVE
                    env = self.adapter.inspect_environment(self.environment_id)
                    if env:
                        env.status = EnvironmentStatus.CORRUPTED
                else:
                    cleanup_status = "SUCCESS"
            except Exception as clean_err:
                cleanup_exception = clean_err
                cleanup_status = f"EXCEPTION: {clean_err}"
                lifecycle_state = TrialLifecycleState.ENVIRONMENT_CORRUPTED
                outcome = HealthCheckOutcome.INCONCLUSIVE
                env = self.adapter.inspect_environment(self.environment_id)
                if env:
                    env.status = EnvironmentStatus.CORRUPTED

        # 8. Post-cleanup record compilation and propagation
        record = self._finalize_trial_record(
            trial_id, start_iso, t0, change_ids, lifecycle_state, outcome,
            output_bounded, restore_status, verify_status, apply_status,
            test_status, cleanup_status, error_reason, evidence_data
        )

        # Handle exception propagation per mandatory rule:
        if isinstance(primary_exception, (KeyboardInterrupt, SystemExit)):
            # Propagate fatal interruptions immediately after cleanup
            raise primary_exception

        if primary_exception and cleanup_exception:
            # Both failed: preserve both errors
            combined_msg = (
                f"Dual Failure in trial {trial_id}:\n"
                f"  Primary Error: {type(primary_exception).__name__}: {primary_exception}\n"
                f"  Cleanup Error: {type(cleanup_exception).__name__}: {cleanup_exception}"
            )
            record.error_reason = combined_msg
            self._persist_trial(record)
            raise RuntimeError(combined_msg) from primary_exception

        if cleanup_exception:
            raise cleanup_exception

        return record

    def _finalize_trial_record(
        self,
        trial_id: str,
        start_iso: str,
        t0: float,
        change_ids: list[str],
        lifecycle_state: TrialLifecycleState,
        outcome: HealthCheckOutcome,
        output_bounded: str,
        restore_status: str,
        verify_status: str,
        apply_status: str,
        test_status: str,
        cleanup_status: str,
        error_reason: str | None,
        evidence_data: dict[str, Any],
    ) -> IsolatedTrialRecord:
        finish_iso = utc_now_iso()
        duration_ms = (time.perf_counter() - t0) * 1000.0

        record = IsolatedTrialRecord(
            trial_id=trial_id,
            investigation_id=self.investigation_id,
            environment_id=self.environment_id,
            baseline_id=self.baseline_id,
            adapter_type=self.adapter.get_adapter_type(),
            lifecycle_state=lifecycle_state,
            is_simulation=(self.adapter.get_adapter_type() == AdapterType.SIMULATION),
            selected_change_ids=change_ids,
            start_time=start_iso,
            finish_time=finish_iso,
            outcome=outcome,
            output_bounded=output_bounded,
            duration_ms=round(duration_ms, 2),
            restore_status=restore_status,
            verify_status=verify_status,
            apply_status=apply_status,
            test_status=test_status,
            cleanup_status=cleanup_status,
            error_reason=error_reason,
            evidence_data=evidence_data,
        )
        self._persist_trial(record)
        return record

    def _persist_trial(self, record: IsolatedTrialRecord) -> None:
        self.storage.save_isolated_trial(record)
        self.storage.log_audit(
            self.investigation_id,
            f"ISOLATED_TRIAL_{record.lifecycle_state.value}",
            f"Trial {record.trial_id} completed with state {record.lifecycle_state.value} and outcome {record.outcome.value}",
            details={
                "environment_id": record.environment_id,
                "baseline_id": record.baseline_id,
                "adapter": record.adapter_type.value,
                "outcome": record.outcome.value,
                "state": record.lifecycle_state.value,
                "duration_ms": record.duration_ms,
                "cleanup_status": record.cleanup_status,
            },
        )

    def run_trial(
        self,
        selected_changes: list[Any],
        health_check: Any = None,
        notes: str = "",
    ) -> Any:
        """Compatibility method allowing DeltaDebugger to seamlessly run on IsolatedTrialRunner."""
        from statebisect.models import ExperimentTrial

        rec = self.run_isolated_trial(selected_changes=selected_changes, notes=notes)
        return ExperimentTrial(
            trial_id=rec.trial_id,
            investigation_id=rec.investigation_id,
            selected_change_ids=rec.selected_change_ids,
            start_time=rec.start_time,
            finish_time=rec.finish_time,
            outcome=rec.outcome,
            health_output=rec.output_bounded,
            reset_status=rec.cleanup_status,
            duration_ms=rec.duration_ms,
            notes=rec.error_reason or notes,
            check_type=f"isolated_{rec.adapter_type.value.lower()}",
            check_version="1.0.0",
            parameters_hash="",
            attempts=[],
        )

