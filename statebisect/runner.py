"""Experiment runner for orchestrating safe, repeatable trials with cancellation support."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .models import (
    CancellationToken,
    Change,
    ExperimentTrial,
    HealthCheckOutcome,
    HealthCheckResult,
    utc_now_iso,
)
from .simulation import (
    BaselineIntegrityError,
    DependencyResolutionError,
    HealthCheckFn,
    SimulatedWorkspace,
)
from .storage import Storage


class ExperimentRunner:
    """Orchestrates individual experiment trials with baseline reset and cleanup."""

    def __init__(
        self,
        workspace: SimulatedWorkspace,
        storage: Storage,
        investigation_id: str,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        self.workspace = workspace
        self.storage = storage
        self.investigation_id = investigation_id
        self.cancellation_token = cancellation_token or CancellationToken()

    def run_trial(
        self,
        selected_changes: list[Change],
        health_check: HealthCheckFn,
        notes: str = "",
    ) -> ExperimentTrial:
        """Execute a single trial with safety cleanup.

        Steps:
            1. Check cancellation token.
            2. Reset to baseline & verify.
            3. Apply selected changes in dependency-safe order.
            4. Execute repeatable health check.
            5. Record output, duration, and outcome.
            6. Reset to baseline in finally block.
            7. Store trial in SQLite and record audit log.
        """
        trial_id = f"trial-{uuid.uuid4().hex[:8]}"
        change_ids = [c.id for c in selected_changes]
        start_iso = utc_now_iso()
        t0 = time.perf_counter()

        outcome = HealthCheckOutcome.INCONCLUSIVE
        health_output = ""
        reset_status = "UNKNOWN"
        trial_notes = notes

        # 1. Check if cancellation requested before launching trial
        if self.cancellation_token.is_cancelled:
            trial_notes = f"Trial skipped: {self.cancellation_token.reason}".strip()
            self.storage.log_audit(
                self.investigation_id,
                "TRIAL_SKIPPED_CANCELLED",
                f"Trial {trial_id} skipped due to cancellation: {self.cancellation_token.reason}",
                level="WARNING",
            )
            trial = ExperimentTrial(
                trial_id=trial_id,
                investigation_id=self.investigation_id,
                selected_change_ids=change_ids,
                start_time=start_iso,
                finish_time=utc_now_iso(),
                outcome=HealthCheckOutcome.INCONCLUSIVE,
                health_output=f"Cancelled: {self.cancellation_token.reason}",
                reset_status="SUCCESS",
                duration_ms=0.0,
                notes=trial_notes,
            )
            self.storage.save_trial(trial)
            return trial

        try:
            # 2. Ensure clean baseline
            self.workspace.reset_to_baseline()

            # 3. Apply selected changes
            applied_ids = self.workspace.apply_changes(selected_changes)

            # 4. Execute health check
            hc_result: HealthCheckResult = health_check(self.workspace)
            outcome = hc_result.outcome
            health_output = hc_result.output

        except DependencyResolutionError as e:
            outcome = HealthCheckOutcome.INCONCLUSIVE
            health_output = f"Dependency error: {e}"
            trial_notes = f"{trial_notes} | {health_output}".strip(" |")
            self.storage.log_audit(
                self.investigation_id, "TRIAL_DEPENDENCY_ERROR", str(e), level="WARNING"
            )
        except BaselineIntegrityError as e:
            outcome = HealthCheckOutcome.INCONCLUSIVE
            health_output = f"Baseline integrity error: {e}"
            trial_notes = f"{trial_notes} | {health_output}".strip(" |")
            self.storage.log_audit(
                self.investigation_id, "TRIAL_BASELINE_ERROR", str(e), level="ERROR"
            )
        except Exception as e:
            outcome = HealthCheckOutcome.INCONCLUSIVE
            health_output = f"Execution exception: {type(e).__name__}: {e}"
            trial_notes = f"{trial_notes} | {health_output}".strip(" |")
            self.storage.log_audit(
                self.investigation_id, "TRIAL_EXCEPTION", str(e), level="ERROR"
            )
        finally:
            # 6. Clean up unconditionally
            try:
                self.workspace.reset_to_baseline()
                reset_status = "SUCCESS"
            except Exception as reset_err:
                reset_status = f"FAILED: {reset_err}"
                self.storage.log_audit(
                    self.investigation_id,
                    "CLEANUP_FAILURE",
                    f"Baseline restoration failed in trial {trial_id}: {reset_err}",
                    level="CRITICAL",
                )

        duration_ms = (time.perf_counter() - t0) * 1000.0
        finish_iso = utc_now_iso()

        trial = ExperimentTrial(
            trial_id=trial_id,
            investigation_id=self.investigation_id,
            selected_change_ids=change_ids,
            start_time=start_iso,
            finish_time=finish_iso,
            outcome=outcome,
            health_output=health_output,
            reset_status=reset_status,
            duration_ms=round(duration_ms, 2),
            notes=trial_notes,
        )

        self.storage.save_trial(trial)
        self.storage.log_audit(
            self.investigation_id,
            "TRIAL_COMPLETED",
            f"Trial {trial_id} completed with outcome {outcome.value}",
            details={
                "outcome": outcome.value,
                "changes": change_ids,
                "duration_ms": trial.duration_ms,
                "reset_status": reset_status,
            },
        )
        return trial
