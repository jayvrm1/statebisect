"""Experiment runner for orchestrating safe, repeatable trials."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .models import (
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
    ) -> None:
        self.workspace = workspace
        self.storage = storage
        self.investigation_id = investigation_id

    def run_trial(
        self,
        selected_changes: list[Change],
        health_check: HealthCheckFn,
        notes: str = "",
    ) -> ExperimentTrial:
        """Execute a single trial with safety cleanup.

        Steps:
            1. Reset to baseline & verify.
            2. Apply selected changes in dependency-safe order.
            3. Execute repeatable health check.
            4. Record output, duration, and outcome.
            5. Reset to baseline in finally block.
            6. Store trial in SQLite.
        """
        trial_id = f"trial-{uuid.uuid4().hex[:8]}"
        change_ids = [c.id for c in selected_changes]
        start_iso = utc_now_iso()
        t0 = time.perf_counter()

        outcome = HealthCheckOutcome.INCONCLUSIVE
        health_output = ""
        reset_status = "UNKNOWN"
        trial_notes = notes

        try:
            # 1. Ensure clean baseline
            self.workspace.reset_to_baseline()

            # 2. Apply selected changes
            applied_ids = self.workspace.apply_changes(selected_changes)

            # 3. Execute health check
            hc_result: HealthCheckResult = health_check(self.workspace)
            outcome = hc_result.outcome
            health_output = hc_result.output

        except DependencyResolutionError as e:
            outcome = HealthCheckOutcome.INCONCLUSIVE
            health_output = f"Dependency error: {e}"
            trial_notes = f"{trial_notes} | {health_output}".strip(" |")
        except BaselineIntegrityError as e:
            outcome = HealthCheckOutcome.INCONCLUSIVE
            health_output = f"Baseline integrity error: {e}"
            trial_notes = f"{trial_notes} | {health_output}".strip(" |")
        except Exception as e:
            outcome = HealthCheckOutcome.INCONCLUSIVE
            health_output = f"Execution exception: {type(e).__name__}: {e}"
            trial_notes = f"{trial_notes} | {health_output}".strip(" |")
        finally:
            # 5. Clean up unconditionally
            try:
                self.workspace.reset_to_baseline()
                reset_status = "SUCCESS"
            except Exception as reset_err:
                reset_status = f"FAILED: {reset_err}"

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
        return trial

