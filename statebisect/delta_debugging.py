"""Generalized Delta Debugging (ddmin) engine with cancellation and integrity handling."""

from __future__ import annotations

from typing import Callable

from .models import (
    Change,
    ExperimentTrial,
    HealthCheckOutcome,
    MinimizationResult,
)
from .runner import ExperimentRunner
from .simulation import HealthCheckFn


def partition(items: list[str], n: int) -> list[list[str]]:
    """Partition a list of items into n approximately equal slices."""
    if n <= 0:
        return [items]
    k, m = divmod(len(items), n)
    subsets: list[list[str]] = []
    start = 0
    for i in range(n):
        end = start + k + (1 if i < m else 0)
        if start < end:
            subsets.append(items[start:end])
        start = end
    return subsets


class DeltaDebugger:
    """Implements generalized ddmin with support for interactions, inconclusive handling, and cancellation."""

    def __init__(
        self,
        runner: ExperimentRunner,
        all_changes: list[Change],
        health_check: HealthCheckFn,
    ) -> None:
        self.runner = runner
        self.all_changes = {c.id: c for c in all_changes}
        self.health_check = health_check
        self.trial_cache: dict[tuple[str, ...], ExperimentTrial] = {}
        self.inconclusive_count = 0

    def _test(self, change_ids: list[str], notes: str = "") -> ExperimentTrial:
        key = tuple(sorted(change_ids))
        if key in self.trial_cache:
            return self.trial_cache[key]

        selected = [self.all_changes[cid] for cid in sorted(change_ids)]
        trial = self.runner.run_trial(
            selected_changes=selected,
            health_check=self.health_check,
            notes=notes,
        )
        self.trial_cache[key] = trial
        if trial.outcome == HealthCheckOutcome.INCONCLUSIVE:
            self.inconclusive_count += 1
        return trial

    def minimize(self, candidate_ids: list[str]) -> MinimizationResult:
        """Run generalized ddmin on candidate changes.

        Returns:
            MinimizationResult detailing the minimal failure-inducing set.
        """
        # Check cancellation before starting
        if self.runner.cancellation_token.is_cancelled:
            return MinimizationResult(
                investigation_id=self.runner.investigation_id,
                suspected_set=[],
                is_1_minimal=False,
                total_trials=len(self.trial_cache),
                verdict="CANCELLED",
                rationale=f"Investigation cancelled before starting: {self.runner.cancellation_token.reason}",
                trials=list(self.trial_cache.values()),
            )

        # 1. Verify baseline test([]) -> PASS
        baseline_trial = self._test([], notes="Baseline pre-check")
        if baseline_trial.reset_status.startswith("FAILED"):
            return MinimizationResult(
                investigation_id=self.runner.investigation_id,
                suspected_set=[],
                is_1_minimal=False,
                total_trials=len(self.trial_cache),
                verdict="SIMULATION_CORRUPTED",
                rationale=f"Baseline cleanup failed: {baseline_trial.reset_status}",
                trials=list(self.trial_cache.values()),
            )

        if baseline_trial.outcome != HealthCheckOutcome.PASS:
            return MinimizationResult(
                investigation_id=self.runner.investigation_id,
                suspected_set=[],
                is_1_minimal=False,
                total_trials=len(self.trial_cache),
                verdict="BASELINE_FAIL",
                rationale=(
                    f"Baseline pre-check did not pass: returned {baseline_trial.outcome.value} "
                    f"({baseline_trial.health_output})."
                ),
                trials=list(self.trial_cache.values()),
            )

        # Check cancellation
        if self.runner.cancellation_token.is_cancelled:
            return self._cancellation_result()

        # 2. Verify full candidate set test(candidate_ids) -> FAIL
        full_trial = self._test(candidate_ids, notes="Full candidate set pre-check")
        if full_trial.reset_status.startswith("FAILED"):
            return MinimizationResult(
                investigation_id=self.runner.investigation_id,
                suspected_set=[],
                is_1_minimal=False,
                total_trials=len(self.trial_cache),
                verdict="SIMULATION_CORRUPTED",
                rationale=f"Cleanup failed during full candidate test: {full_trial.reset_status}",
                trials=list(self.trial_cache.values()),
            )

        if full_trial.outcome == HealthCheckOutcome.PASS:
            return MinimizationResult(
                investigation_id=self.runner.investigation_id,
                suspected_set=[],
                is_1_minimal=False,
                total_trials=len(self.trial_cache),
                verdict="NOT_REPRODUCIBLE",
                rationale="Full candidate set passed health check; failure could not be reproduced.",
                trials=list(self.trial_cache.values()),
            )
        elif full_trial.outcome == HealthCheckOutcome.INCONCLUSIVE:
            return MinimizationResult(
                investigation_id=self.runner.investigation_id,
                suspected_set=[],
                is_1_minimal=False,
                total_trials=len(self.trial_cache),
                verdict="INCONCLUSIVE",
                rationale=(
                    f"Testing full candidate set yielded INCONCLUSIVE ({full_trial.health_output}). "
                    "Cannot safely commence delta debugging."
                ),
                trials=list(self.trial_cache.values()),
            )

        # 3. Generalized ddmin loop
        c = list(candidate_ids)
        n = 2

        while len(c) >= 2:
            if self.runner.cancellation_token.is_cancelled:
                return self._cancellation_result(suspected_subset=c)

            subsets = partition(c, n)
            reduced = False

            # Test individual subsets
            for delta in subsets:
                if self.runner.cancellation_token.is_cancelled:
                    return self._cancellation_result(suspected_subset=c)

                trial = self._test(delta, notes=f"Testing subset of size {len(delta)} (granularity {n})")
                if trial.reset_status.startswith("FAILED"):
                    return MinimizationResult(
                        investigation_id=self.runner.investigation_id,
                        suspected_set=[],
                        is_1_minimal=False,
                        total_trials=len(self.trial_cache),
                        verdict="SIMULATION_CORRUPTED",
                        rationale=f"Cleanup failed during subset trial: {trial.reset_status}",
                        trials=list(self.trial_cache.values()),
                    )

                if trial.outcome == HealthCheckOutcome.FAIL:
                    c = delta
                    n = max(n - 1, 2)
                    reduced = True
                    break

            if reduced:
                continue

            # Test complements (c \ delta)
            for delta in subsets:
                if self.runner.cancellation_token.is_cancelled:
                    return self._cancellation_result(suspected_subset=c)

                complement = [item for item in c if item not in delta]
                if not complement:
                    continue
                trial = self._test(
                    complement,
                    notes=f"Testing complement of size {len(complement)} (granularity {n})",
                )
                if trial.reset_status.startswith("FAILED"):
                    return MinimizationResult(
                        investigation_id=self.runner.investigation_id,
                        suspected_set=[],
                        is_1_minimal=False,
                        total_trials=len(self.trial_cache),
                        verdict="SIMULATION_CORRUPTED",
                        rationale=f"Cleanup failed during complement trial: {trial.reset_status}",
                        trials=list(self.trial_cache.values()),
                    )

                if trial.outcome == HealthCheckOutcome.FAIL:
                    c = complement
                    n = max(n - 1, 2)
                    reduced = True
                    break

            if reduced:
                continue

            # If granularity < |c|, increase granularity
            if n < len(c):
                n = min(2 * n, len(c))
            else:
                # Finished: no subset and no complement failed
                break

        if self.runner.cancellation_token.is_cancelled:
            return self._cancellation_result(suspected_subset=c)

        # Verification: A set is 1-minimal if removing any single element causes health check != FAIL
        is_1_minimal = False
        verdict = "SUCCESS"
        rationale = ""

        # Test each complement of size |c|-1 if not already verified
        all_single_removals_pass = True
        had_inconclusive_removal = False

        if len(c) == 1:
            is_1_minimal = True
            rationale = (
                f"Isolated 1-minimal failure-inducing set: {c}. "
                f"Removing {c[0]} returns to baseline which passed health check."
            )
        else:
            for item in c:
                if self.runner.cancellation_token.is_cancelled:
                    return self._cancellation_result(suspected_subset=c)

                without_item = [x for x in c if x != item]
                sub_trial = self._test(
                    without_item, notes=f"1-minimality verification removing {item}"
                )
                if sub_trial.reset_status.startswith("FAILED"):
                    return MinimizationResult(
                        investigation_id=self.runner.investigation_id,
                        suspected_set=[],
                        is_1_minimal=False,
                        total_trials=len(self.trial_cache),
                        verdict="SIMULATION_CORRUPTED",
                        rationale=f"Cleanup failed during verification: {sub_trial.reset_status}",
                        trials=list(self.trial_cache.values()),
                    )

                if sub_trial.outcome == HealthCheckOutcome.FAIL:
                    all_single_removals_pass = False
                elif sub_trial.outcome == HealthCheckOutcome.INCONCLUSIVE:
                    had_inconclusive_removal = True

            if all_single_removals_pass and not had_inconclusive_removal:
                is_1_minimal = True
                rationale = (
                    f"Isolated 1-minimal failure-inducing interaction set: {c}. "
                    "Removing any single element from this set resolves the failure."
                )
            elif had_inconclusive_removal:
                verdict = "INCONCLUSIVE_SUBSET"
                is_1_minimal = False
                rationale = (
                    f"Candidate set {c} produces failure, but 1-minimality could not be strictly "
                    "verified due to inconclusive sub-trial outcomes."
                )
            else:
                is_1_minimal = False
                rationale = f"Candidate set {c} produces failure, but is not strictly 1-minimal."

        return MinimizationResult(
            investigation_id=self.runner.investigation_id,
            suspected_set=c,
            is_1_minimal=is_1_minimal,
            total_trials=len(self.trial_cache),
            verdict=verdict,
            rationale=rationale,
            trials=list(self.trial_cache.values()),
        )

    def _cancellation_result(self, suspected_subset: list[str] | None = None) -> MinimizationResult:
        return MinimizationResult(
            investigation_id=self.runner.investigation_id,
            suspected_set=suspected_subset or [],
            is_1_minimal=False,
            total_trials=len(self.trial_cache),
            verdict="CANCELLED",
            rationale=f"Investigation cancelled by user: {self.runner.cancellation_token.reason}. No further trials executed.",
            trials=list(self.trial_cache.values()),
        )
