"""Domain models, classifications, and audit records for reproducible benchmarking."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from statebisect.adapters.models import AdapterType
from statebisect.models import utc_now_iso


class BenchmarkCategory(str, Enum):
    """Categories of benchmark scenarios covering failure modes and diagnostic challenges."""

    SINGLE_CAUSE = "single_cause"
    SINGLE_CAUSE_WITH_BYSTANDERS = "single_cause_with_bystanders"
    TWO_CHANGE_INTERACTION = "two_change_interaction"
    THREE_CHANGE_INTERACTION = "three_change_interaction"
    BYSTANDER_WITH_DEPENDENCY = "bystander_with_dependency"
    DEPENDENCY_CONSTRAINED = "dependency_constrained"
    MULTIPLE_MINIMAL_SETS = "multiple_minimal_sets"
    INTERMITTENT_HEALTH_CHECK = "intermittent_health_check"
    UNREPRODUCIBLE_FAILURE = "unreproducible_failure"
    MISSING_TELEMETRY_GAPS = "missing_telemetry_gaps"
    FAILED_BASELINE_RESTORE = "failed_baseline_restore"
    CLEANUP_FAILURE_CORRUPTION = "cleanup_failure_corruption"
    DIFFERENTIAL_BASELINE = "differential_baseline"
    FALSE_TEMPORAL_CORRELATION = "false_temporal_correlation"
    POST_ONSET_CONSEQUENCE = "post_onset_consequence"


class ExpectedVerdict(str, Enum):
    """Expected diagnostic or verification classification for a scenario."""

    VERIFIED_SET = "VERIFIED_SET"
    INCONCLUSIVE = "INCONCLUSIVE"
    INCONCLUSIVE_SUBSET = "INCONCLUSIVE_SUBSET"
    NO_REPRO = "NO_REPRO"
    FLAKY_ORACLE = "FLAKY_ORACLE"
    RESTORATION_FAILED = "RESTORATION_FAILED"
    CORRUPTED = "CORRUPTED"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class CandidateSpec:
    """Specification for a candidate change in a benchmark scenario."""

    candidate_id: str
    category: str
    object_key: str
    after_value: Any
    before_value: Any = None
    dependencies: list[str] = field(default_factory=list)
    relative_time_seconds: float = -60.0  # seconds relative to onset
    is_post_onset: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateSpec:
        return cls(
            candidate_id=data["candidate_id"],
            category=data.get("category", "registry"),
            object_key=data.get("object_key", ""),
            after_value=data.get("after_value", ""),
            before_value=data.get("before_value", None),
            dependencies=list(data.get("dependencies", [])),
            relative_time_seconds=float(data.get("relative_time_seconds", -60.0)),
            is_post_onset=bool(data.get("is_post_onset", False)),
            description=data.get("description", ""),
        )


@dataclass
class HealthCheckSpec:
    """Specification for health check behavior under different change sets."""

    check_type: str = "simulated_state"
    version: str = "1.0.0"
    failure_conditions: list[dict[str, Any]] = field(default_factory=list)
    is_intermittent: bool = False
    intermittent_rate: float = 0.5  # probability of fail when condition met
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HealthCheckSpec:
        return cls(
            check_type=data.get("check_type", "simulated_state"),
            version=data.get("version", "1.0.0"),
            failure_conditions=list(data.get("failure_conditions", [])),
            is_intermittent=bool(data.get("is_intermittent", False)),
            intermittent_rate=float(data.get("intermittent_rate", 0.5)),
            parameters=dict(data.get("parameters", {})),
        )


@dataclass
class BenchmarkScenario:
    """Versioned benchmark case with ground-truth causal attribution."""

    scenario_id: str
    version: str
    category: BenchmarkCategory
    title: str
    description: str
    adapter_type: AdapterType
    ground_truth_causes: list[str]  # candidate IDs forming the true minimal failure cause
    all_candidate_ids: list[str]
    candidates: list[CandidateSpec]
    health_check: HealthCheckSpec
    expected_classification: ExpectedVerdict
    baseline_id: str = "baseline-001"
    differential_baseline_id: str | None = None
    fault_injection: dict[str, Any] = field(default_factory=dict)
    random_seed: int | None = 42
    expected_limitations: list[str] = field(default_factory=list)
    owner_notes: str = ""
    creation_source: str = "StateBisect Engineering Ground-Truth Testbed"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["adapter_type"] = self.adapter_type.value
        data["expected_classification"] = self.expected_classification.value
        data["candidates"] = [c.to_dict() for c in self.candidates]
        data["health_check"] = self.health_check.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkScenario:
        candidates = [CandidateSpec.from_dict(c) for c in data.get("candidates", [])]
        health_check = HealthCheckSpec.from_dict(data.get("health_check", {}))
        return cls(
            scenario_id=data["scenario_id"],
            version=data.get("version", "1.0.0"),
            category=BenchmarkCategory(data["category"]),
            title=data.get("title", ""),
            description=data.get("description", ""),
            adapter_type=AdapterType(data.get("adapter_type", AdapterType.SIMULATION.value)),
            ground_truth_causes=list(data.get("ground_truth_causes", [])),
            all_candidate_ids=list(data.get("all_candidate_ids", [c.candidate_id for c in candidates])),
            candidates=candidates,
            health_check=health_check,
            expected_classification=ExpectedVerdict(data.get("expected_classification", ExpectedVerdict.VERIFIED_SET.value)),
            baseline_id=data.get("baseline_id", "baseline-001"),
            differential_baseline_id=data.get("differential_baseline_id"),
            fault_injection=dict(data.get("fault_injection", {})),
            random_seed=data.get("random_seed", 42),
            expected_limitations=list(data.get("expected_limitations", [])),
            owner_notes=data.get("owner_notes", ""),
            creation_source=data.get("creation_source", "StateBisect Engineering Ground-Truth Testbed"),
        )


@dataclass
class ScenarioExecutionResult:
    """Execution outcome for a single benchmark scenario."""

    scenario_id: str
    category: BenchmarkCategory
    adapter_type: str
    verdict_observed: str
    expected_classification: str
    ground_truth_causes: list[str]
    reported_causes: list[str]
    exact_match: bool
    precision: float
    recall: float
    is_1_minimal: bool
    bystanders_rejected: bool
    trials_count: int
    duration_ms: float
    baseline_restored_cleanly: bool
    cleanup_succeeded: bool
    flaky_detected: bool
    inconclusive_reason: str | None = None
    notes: str = ""
    raw_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenarioExecutionResult:
        return cls(
            scenario_id=data["scenario_id"],
            category=BenchmarkCategory(data["category"]),
            adapter_type=data.get("adapter_type", "simulation"),
            verdict_observed=data["verdict_observed"],
            expected_classification=data.get("expected_classification", ""),
            ground_truth_causes=list(data.get("ground_truth_causes", [])),
            reported_causes=list(data.get("reported_causes", [])),
            exact_match=bool(data.get("exact_match", False)),
            precision=float(data.get("precision", 0.0)),
            recall=float(data.get("recall", 0.0)),
            is_1_minimal=bool(data.get("is_1_minimal", False)),
            bystanders_rejected=bool(data.get("bystanders_rejected", False)),
            trials_count=int(data.get("trials_count", 0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            baseline_restored_cleanly=bool(data.get("baseline_restored_cleanly", True)),
            cleanup_succeeded=bool(data.get("cleanup_succeeded", True)),
            flaky_detected=bool(data.get("flaky_detected", False)),
            inconclusive_reason=data.get("inconclusive_reason"),
            notes=data.get("notes", ""),
            raw_result=dict(data.get("raw_result", {})),
        )


@dataclass
class MetricFraction:
    """Precise raw count and percentage representation."""

    numerator: int
    denominator: int
    percentage: float
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkMetricsSummary:
    """Aggregated empirical benchmark metrics across a benchmark run."""

    total_scenarios: int
    exact_set_accuracy: MetricFraction
    element_precision: MetricFraction
    element_recall: MetricFraction
    one_minimality_rate: MetricFraction
    interaction_recognition_rate: MetricFraction
    bystander_rejection_rate: MetricFraction
    false_attribution_rate: MetricFraction
    inconclusive_correctness_rate: MetricFraction
    baseline_integrity_rate: MetricFraction
    cleanup_success_rate: MetricFraction
    total_trials: int
    total_duration_ms: float
    average_trials_per_scenario: float
    average_duration_ms_per_scenario: float
    diagnostic_accuracy: MetricFraction | None = None
    diagnostic_coverage: MetricFraction | None = None
    normal_restore_success_rate: MetricFraction | None = None
    normal_cleanup_success_rate: MetricFraction | None = None
    fault_containment_rate: MetricFraction | None = None
    ai_valid_citation_rate: MetricFraction | None = None
    ai_unsupported_claim_rate: MetricFraction | None = None
    ai_injection_resistance_rate: MetricFraction | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "total_scenarios": self.total_scenarios,
            "exact_set_accuracy": self.exact_set_accuracy.to_dict(),
            "element_precision": self.element_precision.to_dict(),
            "element_recall": self.element_recall.to_dict(),
            "one_minimality_rate": self.one_minimality_rate.to_dict(),
            "interaction_recognition_rate": self.interaction_recognition_rate.to_dict(),
            "bystander_rejection_rate": self.bystander_rejection_rate.to_dict(),
            "false_attribution_rate": self.false_attribution_rate.to_dict(),
            "inconclusive_correctness_rate": self.inconclusive_correctness_rate.to_dict(),
            "baseline_integrity_rate": self.baseline_integrity_rate.to_dict(),
            "cleanup_success_rate": self.cleanup_success_rate.to_dict(),
            "total_trials": self.total_trials,
            "total_duration_ms": self.total_duration_ms,
            "average_trials_per_scenario": self.average_trials_per_scenario,
            "average_duration_ms_per_scenario": self.average_duration_ms_per_scenario,
        }
        if self.diagnostic_accuracy is not None:
            data["diagnostic_accuracy"] = self.diagnostic_accuracy.to_dict()
        if self.diagnostic_coverage is not None:
            data["diagnostic_coverage"] = self.diagnostic_coverage.to_dict()
        if self.normal_restore_success_rate is not None:
            data["normal_restore_success_rate"] = self.normal_restore_success_rate.to_dict()
        if self.normal_cleanup_success_rate is not None:
            data["normal_cleanup_success_rate"] = self.normal_cleanup_success_rate.to_dict()
        if self.fault_containment_rate is not None:
            data["fault_containment_rate"] = self.fault_containment_rate.to_dict()
        if self.ai_valid_citation_rate is not None:
            data["ai_valid_citation_rate"] = self.ai_valid_citation_rate.to_dict()
        if self.ai_unsupported_claim_rate is not None:
            data["ai_unsupported_claim_rate"] = self.ai_unsupported_claim_rate.to_dict()
        if self.ai_injection_resistance_rate is not None:
            data["ai_injection_resistance_rate"] = self.ai_injection_resistance_rate.to_dict()
        return data


@dataclass
class BenchmarkRunRecord:
    """Complete, persistent record of a benchmark execution."""

    run_id: str
    suite_id: str
    suite_version: str
    suite_hash: str
    engine_version: str
    adapter_type: str
    status: str
    is_disposable_vm: bool
    summary: BenchmarkMetricsSummary
    scenario_results: list[ScenarioExecutionResult]
    configuration: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "suite_hash": self.suite_hash,
            "engine_version": self.engine_version,
            "adapter_type": self.adapter_type,
            "status": self.status,
            "is_disposable_vm": self.is_disposable_vm,
            "summary": self.summary.to_dict(),
            "scenario_results": [r.to_dict() for r in self.scenario_results],
            "configuration": self.configuration,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }
