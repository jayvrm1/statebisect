"""Verification and differential validation engine for StateBisect Phase 8."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

from statebisect.adapters import (
    AdapterType,
    BaseEnvironmentAdapter,
    EnvironmentStatus,
    IsolatedTrialRecord,
    IsolatedTrialRunner,
    TrialLifecycleState,
)
from statebisect.models import CancellationToken, Change, HealthCheckOutcome, utc_now_iso

from .models import (
    TrialRole,
    VerificationNecessityDetail,
    VerificationProtocolConfig,
    VerificationRunRecord,
    VerificationTrialRecord,
    VerificationVerdict,
)

logger = logging.getLogger(__name__)


class VerificationEngine:
    """Orchestrates deterministic verification, reproduction, necessity testing,

    and differential validation of candidate change sets against verified baselines.
    """

    def __init__(
        self,
        runner: IsolatedTrialRunner,
        candidate_changes: list[Change],
        config: VerificationProtocolConfig | None = None,
        cancellation_token: CancellationToken | None = None,
        reversal_action_fn: Callable[[BaseEnvironmentAdapter, str], bool] | None = None,
    ) -> None:
        self.runner = runner
        self.candidate_changes = candidate_changes
        self.config = config or VerificationProtocolConfig()
        self.cancellation_token = cancellation_token or runner.cancellation_token or CancellationToken()
        self.reversal_action_fn = reversal_action_fn

    def preview(self) -> dict[str, Any]:
        """Generate a dry-run preview of the planned verification protocol without executing trials."""
        k = len(self.candidate_changes)
        r = self.config.repeat_count

        control_trials = r
        repro_trials = r
        necessity_trials = k * r
        reversal_trials = 1 if self.config.enable_reversal else 0
        diff_trials = len(self.config.differential_baseline_ids) * (r + r)

        total_trials_estimate = control_trials + repro_trials + necessity_trials + reversal_trials + diff_trials

        return {
            "protocol_version": self.config.protocol_version,
            "environment_id": self.runner.environment_id,
            "primary_baseline_id": self.runner.baseline_id,
            "differential_baseline_ids": self.config.differential_baseline_ids,
            "candidate_change_ids": [c.id for c in self.candidate_changes],
            "candidate_count": k,
            "repeat_count": r,
            "enable_reversal": self.config.enable_reversal,
            "estimated_trials": {
                "baseline_control": control_trials,
                "candidate_reproduction": repro_trials,
                "necessity_checks": necessity_trials,
                "reversal_checks": reversal_trials,
                "differential_baselines": diff_trials,
                "total_estimated": total_trials_estimate,
            },
            "safety_declarations": {
                "zero_host_modifications": True,
                "environment_disposable_required": True,
                "pre_and_post_trial_baseline_restore": True,
            },
        }

    def verify(self) -> VerificationRunRecord:
        """Execute the full verification protocol and return an evidence-backed record."""
        verification_id = f"verif-{uuid.uuid4().hex[:10]}"
        t_start = utc_now_iso()

        executed_trials: list[VerificationTrialRecord] = []
        necessity_details: list[VerificationNecessityDetail] = []
        differential_results: dict[str, Any] = {}
        limitations: list[str] = [
            f"Findings apply strictly to tested environment '{self.runner.environment_id}' "
            f"and baseline '{self.runner.baseline_id}'.",
            "Observed reproducible correlation is not a claim of universal causality.",
        ]

        candidate_ids = [c.id for c in self.candidate_changes]

        # 1. Check cancellation before starting
        if self.cancellation_token.is_cancelled:
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.CANCELLED,
                created_at=t_start,
                candidate_ids=candidate_ids,
                rationale=f"Verification cancelled before launch: {self.cancellation_token.reason}",
                trials=[],
                limitations=limitations,
            )

        # 2. Check environment safety prerequisites
        env = self.runner.adapter.inspect_environment(self.runner.environment_id)
        if not env or not env.is_disposable or env.is_host:
            limitations.append("Environment failed safety validation (must be disposable and non-host).")
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.INCONCLUSIVE,
                created_at=t_start,
                candidate_ids=candidate_ids,
                rationale="Environment is not disposable or attempted host targeting.",
                trials=[],
                limitations=limitations,
            )

        if env.status == EnvironmentStatus.CORRUPTED:
            limitations.append("Environment is marked CORRUPTED from previous trial failures.")
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.ENVIRONMENT_CORRUPTED,
                created_at=t_start,
                candidate_ids=candidate_ids,
                rationale="Environment is corrupted; refusing to run verification trials.",
                trials=[],
                limitations=limitations,
            )

        # ----------------------------------------------------------------------
        # STAGE 1: Baseline Control (Ensure baseline is healthy before changes)
        # ----------------------------------------------------------------------
        baseline_control_passed = False
        control_outcomes: list[HealthCheckOutcome] = []

        for attempt in range(1, self.config.repeat_count + 1):
            if self.cancellation_token.is_cancelled:
                return self._finalize_cancelled(verification_id, t_start, candidate_ids, executed_trials, limitations)

            trial_rec = self.runner.run_isolated_trial(
                selected_changes=[],
                notes=f"Verification baseline control (attempt {attempt}/{self.config.repeat_count})",
            )
            v_trial = self._to_verification_trial(trial_rec, TrialRole.BASELINE_CONTROL, [], None, attempt)
            executed_trials.append(v_trial)

            if trial_rec.lifecycle_state == TrialLifecycleState.ENVIRONMENT_CORRUPTED:
                return self._finalize_corrupted(verification_id, t_start, candidate_ids, executed_trials, limitations)

            control_outcomes.append(trial_rec.outcome)

        # Evaluate baseline control consistency
        if any(o == HealthCheckOutcome.FAIL for o in control_outcomes):
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.INVALID_BASELINE,
                created_at=t_start,
                candidate_ids=candidate_ids,
                baseline_control_passed=False,
                rationale="Baseline control trial failed. The baseline is already in a failed state prior to applying changes.",
                trials=executed_trials,
                limitations=limitations,
            )

        if any(o == HealthCheckOutcome.INCONCLUSIVE for o in control_outcomes):
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.INCONCLUSIVE,
                created_at=t_start,
                candidate_ids=candidate_ids,
                baseline_control_passed=False,
                rationale="Baseline control trial produced an inconclusive outcome (timeout, error, or unstable environment).",
                trials=executed_trials,
                limitations=limitations,
            )

        baseline_control_passed = True

        # If candidate set is empty, nothing to verify
        if not self.candidate_changes:
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.NOT_REPRODUCED,
                created_at=t_start,
                candidate_ids=[],
                baseline_control_passed=True,
                rationale="Candidate change set is empty; cannot verify failure reproduction.",
                trials=executed_trials,
                limitations=limitations,
            )

        # ----------------------------------------------------------------------
        # STAGE 2: Candidate Set Reproduction (Apply S to baseline)
        # ----------------------------------------------------------------------
        repro_outcomes: list[HealthCheckOutcome] = []

        for attempt in range(1, self.config.repeat_count + 1):
            if self.cancellation_token.is_cancelled:
                return self._finalize_cancelled(verification_id, t_start, candidate_ids, executed_trials, limitations)

            trial_rec = self.runner.run_isolated_trial(
                selected_changes=self.candidate_changes,
                notes=f"Verification candidate set reproduction (attempt {attempt}/{self.config.repeat_count})",
            )
            v_trial = self._to_verification_trial(trial_rec, TrialRole.CANDIDATE_SET, candidate_ids, None, attempt)
            executed_trials.append(v_trial)

            if trial_rec.lifecycle_state == TrialLifecycleState.ENVIRONMENT_CORRUPTED:
                return self._finalize_corrupted(verification_id, t_start, candidate_ids, executed_trials, limitations)

            repro_outcomes.append(trial_rec.outcome)

        # Check reproducibility consistency
        if len(set(repro_outcomes)) > 1:
            limitations.append("Candidate set reproduction yielded inconsistent results across repeated trials (intermittency).")
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.INCONCLUSIVE,
                created_at=t_start,
                candidate_ids=candidate_ids,
                baseline_control_passed=True,
                candidate_set_reproduced=False,
                rationale=f"Flaky/intermittent symptom reproduction across {self.config.repeat_count} attempts: {repro_outcomes}",
                trials=executed_trials,
                limitations=limitations,
            )

        aggregate_repro = repro_outcomes[0]
        if aggregate_repro == HealthCheckOutcome.PASS:
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.NOT_REPRODUCED,
                created_at=t_start,
                candidate_ids=candidate_ids,
                baseline_control_passed=True,
                candidate_set_reproduced=False,
                rationale="Applying the full candidate set did not reproduce the defined symptom (health check passed).",
                trials=executed_trials,
                limitations=limitations,
            )

        if aggregate_repro == HealthCheckOutcome.INCONCLUSIVE:
            return self._build_record(
                verification_id=verification_id,
                verdict=VerificationVerdict.INCONCLUSIVE,
                created_at=t_start,
                candidate_ids=candidate_ids,
                baseline_control_passed=True,
                candidate_set_reproduced=False,
                rationale="Applying candidate set resulted in an inconclusive health check (timeout or telemetry gap).",
                trials=executed_trials,
                limitations=limitations,
            )

        # At this point, aggregate_repro == FAIL -> Candidate set is SUFFICIENT in tested environment
        candidate_set_reproduced = True

        # ----------------------------------------------------------------------
        # STAGE 3: Necessity Checks (For each c in S, test S \ {c})
        # ----------------------------------------------------------------------
        is_1_minimal = True
        all_necessary = True
        any_necessary = False

        for change in self.candidate_changes:
            if self.cancellation_token.is_cancelled:
                return self._finalize_cancelled(verification_id, t_start, candidate_ids, executed_trials, limitations)

            subset = [c for c in self.candidate_changes if c.id != change.id]
            subset_ids = [c.id for c in subset]

            subset_outcomes: list[HealthCheckOutcome] = []
            for attempt in range(1, self.config.repeat_count + 1):
                trial_rec = self.runner.run_isolated_trial(
                    selected_changes=subset,
                    notes=f"Necessity check omitting '{change.id}' (attempt {attempt}/{self.config.repeat_count})",
                )
                v_trial = self._to_verification_trial(
                    trial_rec, TrialRole.NECESSITY_TEST, subset_ids, change.id, attempt
                )
                executed_trials.append(v_trial)

                if trial_rec.lifecycle_state == TrialLifecycleState.ENVIRONMENT_CORRUPTED:
                    return self._finalize_corrupted(verification_id, t_start, candidate_ids, executed_trials, limitations)

                subset_outcomes.append(trial_rec.outcome)

            # Evaluate necessity for this change
            if len(set(subset_outcomes)) > 1:
                # Disagreement across repeats
                necessity_details.append(
                    VerificationNecessityDetail(
                        change_id=change.id,
                        is_necessary=False,
                        outcome_without_change=HealthCheckOutcome.INCONCLUSIVE,
                        explanation=f"Repetitions disagreed when omitting '{change.id}' ({subset_outcomes}). Marked inconclusive.",
                    )
                )
                all_necessary = False
                is_1_minimal = False
            elif subset_outcomes[0] == HealthCheckOutcome.PASS:
                # Omitting change restored health -> change is NECESSARY
                necessity_details.append(
                    VerificationNecessityDetail(
                        change_id=change.id,
                        is_necessary=True,
                        outcome_without_change=HealthCheckOutcome.PASS,
                        explanation=f"Omitting '{change.id}' restored healthy baseline outcome (PASS).",
                    )
                )
                any_necessary = True
            elif subset_outcomes[0] == HealthCheckOutcome.FAIL:
                # Failure persisted even without this change -> change is NOT necessary
                necessity_details.append(
                    VerificationNecessityDetail(
                        change_id=change.id,
                        is_necessary=False,
                        outcome_without_change=HealthCheckOutcome.FAIL,
                        explanation=f"Failure persisted when omitting '{change.id}' (FAIL). Member is not strictly necessary.",
                    )
                )
                all_necessary = False
                is_1_minimal = False
            else:
                necessity_details.append(
                    VerificationNecessityDetail(
                        change_id=change.id,
                        is_necessary=False,
                        outcome_without_change=HealthCheckOutcome.INCONCLUSIVE,
                        explanation=f"Trial omitting '{change.id}' was inconclusive.",
                    )
                )
                all_necessary = False
                is_1_minimal = False

        # ----------------------------------------------------------------------
        # STAGE 4: Reversal Check (Optional, safe test action only)
        # ----------------------------------------------------------------------
        reversal_tested = False
        reversal_passed: bool | None = None

        if self.config.enable_reversal and self.reversal_action_fn is not None:
            reversal_tested = True
            try:
                # 1. Restore baseline and apply candidate changes
                self.runner.adapter.restore_baseline(self.runner.environment_id, self.runner.baseline_id)
                for chg in self.candidate_changes:
                    self.runner.adapter.apply_registered_change(
                        self.runner.environment_id, chg.id, chg.payload
                    )
                # 2. Apply registered reversal function
                reversal_ok = self.reversal_action_fn(self.runner.adapter, self.runner.environment_id)
                if reversal_ok:
                    # 3. Execute health check on the reversed state
                    hc_res = self.runner.adapter.run_health_check(
                        self.runner.environment_id, "default", {}
                    )
                    reversal_passed = (hc_res.outcome == HealthCheckOutcome.PASS)
                    v_trial = VerificationTrialRecord(
                        trial_id=f"rev-{uuid.uuid4().hex[:8]}",
                        verification_id=verification_id,
                        trial_role=TrialRole.REVERSAL_CHECK,
                        target_change_ids=candidate_ids,
                        omitted_change_id=None,
                        attempt_number=1,
                        environment_id=self.runner.environment_id,
                        baseline_id=self.runner.baseline_id,
                        outcome=hc_res.outcome,
                        lifecycle_state=TrialLifecycleState.COMPLETED.value,
                        restore_status="SUCCESS",
                        cleanup_status="SUCCESS",
                        output_bounded=hc_res.output,
                        duration_ms=hc_res.duration_ms,
                    )
                    executed_trials.append(v_trial)
                else:
                    reversal_passed = False
            except Exception as err:
                logger.warning("Reversal check encountered error: %s", err)
                reversal_passed = False
            finally:
                # 4. Mandatory cleanup and baseline restore after reversal trial
                self.runner.adapter.cleanup_trial(self.runner.environment_id)
                self.runner.adapter.restore_baseline(self.runner.environment_id, self.runner.baseline_id)

        # ----------------------------------------------------------------------
        # STAGE 5: Differential Baseline Validation (Optional)
        # ----------------------------------------------------------------------
        if self.config.differential_baseline_ids:
            for diff_base_id in self.config.differential_baseline_ids:
                diff_runner = IsolatedTrialRunner(
                    adapter=self.runner.adapter,
                    environment_id=self.runner.environment_id,
                    baseline_id=diff_base_id,
                    storage=self.runner.storage,
                    investigation_id=self.runner.investigation_id,
                    cancellation_token=self.cancellation_token,
                )

                # Control on differential baseline
                ctrl_trial = diff_runner.run_isolated_trial(
                    selected_changes=[],
                    notes=f"Differential baseline control on '{diff_base_id}'",
                )
                executed_trials.append(
                    self._to_verification_trial(ctrl_trial, TrialRole.DIFFERENTIAL_BASELINE, [], None, 1)
                )

                if ctrl_trial.outcome != HealthCheckOutcome.PASS:
                    differential_results[diff_base_id] = {
                        "status": "INCOMPATIBLE_BASELINE",
                        "control_outcome": ctrl_trial.outcome.value,
                        "reproduced": False,
                        "notes": f"Differential baseline '{diff_base_id}' failed control check.",
                    }
                    continue

                # Apply candidate set on differential baseline
                set_trial = diff_runner.run_isolated_trial(
                    selected_changes=self.candidate_changes,
                    notes=f"Differential candidate set trial on '{diff_base_id}'",
                )
                executed_trials.append(
                    self._to_verification_trial(set_trial, TrialRole.DIFFERENTIAL_BASELINE, candidate_ids, None, 1)
                )

                diff_reproduced = (set_trial.outcome == HealthCheckOutcome.FAIL)
                differential_results[diff_base_id] = {
                    "status": "TESTED",
                    "control_outcome": ctrl_trial.outcome.value,
                    "candidate_outcome": set_trial.outcome.value,
                    "reproduced": diff_reproduced,
                    "notes": "Reproduced on differential baseline" if diff_reproduced else "Did not reproduce on differential baseline",
                }

                if not diff_reproduced:
                    limitations.append(
                        f"Differential finding: Candidate set reproduced on '{self.runner.baseline_id}' "
                        f"but failed to reproduce on '{diff_base_id}'. Finding is baseline-dependent."
                    )

        # ----------------------------------------------------------------------
        # STAGE 6: Synthesize Verdict and Rationale
        # ----------------------------------------------------------------------
        is_interaction = False
        verdict = VerificationVerdict.PARTIALLY_SUPPORTED

        if all_necessary:
            is_1_minimal = True
            if len(self.candidate_changes) == 1:
                verdict = VerificationVerdict.VERIFIED_SET
                rationale = (
                    f"Candidate change '{self.candidate_changes[0].id}' is verified: applying it to "
                    f"clean baseline '{self.runner.baseline_id}' reproduces the symptom, and it is 1-minimal."
                )
            else:
                verdict = VerificationVerdict.VERIFIED_INTERACTION
                is_interaction = True
                rationale = (
                    f"Verified multi-change interaction across {len(self.candidate_changes)} changes: "
                    f"all members {candidate_ids} are strictly necessary. Omitting any member resolves the symptom."
                )
        elif any_necessary:
            verdict = VerificationVerdict.PARTIALLY_SUPPORTED
            rationale = (
                f"Candidate set {candidate_ids} reproduces the failure, but only a subset of changes "
                "was shown to be necessary. The set contains bystander or redundant changes."
            )
        else:
            verdict = VerificationVerdict.PARTIALLY_SUPPORTED
            rationale = (
                f"Candidate set {candidate_ids} reproduces the failure, but no individual change was strictly "
                "necessary (alternative causes or disjunctive failure pathways exist)."
            )

        record = self._build_record(
            verification_id=verification_id,
            verdict=verdict,
            created_at=t_start,
            candidate_ids=candidate_ids,
            is_1_minimal=is_1_minimal,
            is_interaction=is_interaction,
            baseline_control_passed=baseline_control_passed,
            candidate_set_reproduced=candidate_set_reproduced,
            reversal_tested=reversal_tested,
            reversal_passed=reversal_passed,
            necessity_details=necessity_details,
            differential_results=differential_results,
            trials=executed_trials,
            rationale=rationale,
            limitations=limitations,
        )

        # Persist verification run record
        try:
            self.runner.storage.save_verification_run(record)
        except Exception as err:
            logger.error("Failed to persist verification run record: %s", err)

        return record

    def _to_verification_trial(
        self,
        trial_rec: IsolatedTrialRecord,
        role: TrialRole,
        target_ids: list[str],
        omitted_id: str | None,
        attempt: int,
    ) -> VerificationTrialRecord:
        return VerificationTrialRecord(
            trial_id=trial_rec.trial_id,
            verification_id="",  # Bound during finalization
            trial_role=role,
            target_change_ids=target_ids,
            omitted_change_id=omitted_id,
            attempt_number=attempt,
            environment_id=trial_rec.environment_id,
            baseline_id=trial_rec.baseline_id,
            outcome=trial_rec.outcome,
            lifecycle_state=trial_rec.lifecycle_state.value,
            restore_status=trial_rec.restore_status,
            cleanup_status=trial_rec.cleanup_status,
            output_bounded=trial_rec.output_bounded,
            duration_ms=trial_rec.duration_ms,
            timestamp=trial_rec.finish_time,
        )

    def _build_record(
        self,
        verification_id: str,
        verdict: VerificationVerdict,
        created_at: str,
        candidate_ids: list[str],
        rationale: str,
        trials: list[VerificationTrialRecord],
        limitations: list[str],
        is_1_minimal: bool = False,
        is_interaction: bool = False,
        baseline_control_passed: bool = False,
        candidate_set_reproduced: bool = False,
        reversal_tested: bool = False,
        reversal_passed: bool | None = None,
        necessity_details: list[VerificationNecessityDetail] | None = None,
        differential_results: dict[str, Any] | None = None,
    ) -> VerificationRunRecord:
        for t in trials:
            t.verification_id = verification_id

        return VerificationRunRecord(
            verification_id=verification_id,
            investigation_id=self.runner.investigation_id,
            environment_id=self.runner.environment_id,
            baseline_id=self.runner.baseline_id,
            verdict=verdict,
            protocol_version=self.config.protocol_version,
            created_at=created_at,
            finished_at=utc_now_iso(),
            candidate_ids=candidate_ids,
            is_1_minimal=is_1_minimal,
            is_interaction=is_interaction,
            baseline_control_passed=baseline_control_passed,
            candidate_set_reproduced=candidate_set_reproduced,
            reversal_tested=reversal_tested,
            reversal_passed=reversal_passed,
            necessity_details=necessity_details or [],
            differential_results=differential_results or {},
            trials=trials,
            confidence_rationale=rationale,
            limitations=limitations,
        )

    def _finalize_cancelled(
        self,
        verification_id: str,
        t_start: str,
        candidate_ids: list[str],
        trials: list[VerificationTrialRecord],
        limitations: list[str],
    ) -> VerificationRunRecord:
        record = self._build_record(
            verification_id=verification_id,
            verdict=VerificationVerdict.CANCELLED,
            created_at=t_start,
            candidate_ids=candidate_ids,
            rationale=f"Verification protocol cancelled during execution: {self.cancellation_token.reason}",
            trials=trials,
            limitations=limitations,
        )
        try:
            self.runner.storage.save_verification_run(record)
        except Exception:
            pass
        return record

    def _finalize_corrupted(
        self,
        verification_id: str,
        t_start: str,
        candidate_ids: list[str],
        trials: list[VerificationTrialRecord],
        limitations: list[str],
    ) -> VerificationRunRecord:
        limitations.append("Environment corruption occurred during trial execution. Execution aborted immediately.")
        record = self._build_record(
            verification_id=verification_id,
            verdict=VerificationVerdict.ENVIRONMENT_CORRUPTED,
            created_at=t_start,
            candidate_ids=candidate_ids,
            rationale="Environment restore or cleanup failed; environment marked CORRUPTED.",
            trials=trials,
            limitations=limitations,
        )
        try:
            self.runner.storage.save_verification_run(record)
        except Exception:
            pass
        return record
