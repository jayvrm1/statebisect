"""Unit and integration tests for reproducible benchmarking suite and metrics."""

import json
import tempfile
import unittest
from pathlib import Path

from statebisect.adapters.models import AdapterType
from statebisect.benchmark import (
    BenchmarkCategory,
    BenchmarkMetricsSummary,
    BenchmarkRunRecord,
    BenchmarkRunner,
    BenchmarkScenario,
    BenchmarkSuite,
    CandidateSpec,
    ExpectedVerdict,
    HealthCheckSpec,
    ScenarioExecutionResult,
    calculate_benchmark_metrics,
    format_benchmark_report_json,
    format_benchmark_report_text,
)
from statebisect.models import CancellationToken
from statebisect.storage import Storage


class TestBenchmarkSuite(unittest.TestCase):
    """Test suite loading, validation, and cryptographic hash verification."""

    def test_default_suite_loads_all_scenarios(self):
        suite = BenchmarkSuite.get_default_suite()
        self.assertEqual(len(suite.scenarios), 15)
        h = suite.compute_suite_hash()
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)  # SHA-256 length

    def test_default_suite_validation_passes(self):
        suite = BenchmarkSuite.get_default_suite()
        errors = suite.validate()
        self.assertEqual(errors, [])

    def test_validation_detects_duplicate_candidate_ids(self):
        dup_scenario = BenchmarkScenario(
            scenario_id="bench-dup",
            version="1.0.0",
            category=BenchmarkCategory.SINGLE_CAUSE,
            title="Duplicate candidate test",
            description="",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1", "C1"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="K1", after_value="1"),
                CandidateSpec(candidate_id="C1", category="registry", object_key="K2", after_value="2"),
            ],
            health_check=HealthCheckSpec(),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
        )
        suite = BenchmarkSuite(scenarios=[dup_scenario])
        errors = suite.validate()
        self.assertTrue(any("duplicate candidate_ids" in e for e in errors))

    def test_validation_detects_unknown_dependency(self):
        bad_dep = BenchmarkScenario(
            scenario_id="bench-dep",
            version="1.0.0",
            category=BenchmarkCategory.DEPENDENCY_CONSTRAINED,
            title="Bad dependency",
            description="",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1"],
            candidates=[
                CandidateSpec(
                    candidate_id="C1",
                    category="registry",
                    object_key="K1",
                    after_value="1",
                    dependencies=["UNKNOWN_PARENT"],
                )
            ],
            health_check=HealthCheckSpec(),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
        )
        suite = BenchmarkSuite(scenarios=[bad_dep])
        errors = suite.validate()
        self.assertTrue(any("references unknown dependency" in e for e in errors))

    def test_validation_detects_self_dependency(self):
        self_dep = BenchmarkScenario(
            scenario_id="bench-self-dep",
            version="1.0.0",
            category=BenchmarkCategory.DEPENDENCY_CONSTRAINED,
            title="Self dependency",
            description="",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1"],
            candidates=[
                CandidateSpec(
                    candidate_id="C1",
                    category="registry",
                    object_key="K1",
                    after_value="1",
                    dependencies=["C1"],
                )
            ],
            health_check=HealthCheckSpec(),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
        )
        suite = BenchmarkSuite(scenarios=[self_dep])
        errors = suite.validate()
        self.assertTrue(any("cannot depend on itself" in e for e in errors))


class TestBenchmarkExecutionAndMetrics(unittest.TestCase):
    """Test benchmark runner execution, metrics aggregation, and cancellation."""

    def test_run_default_suite_reproducibility(self):
        runner = BenchmarkRunner()
        record = runner.run_suite()
        self.assertEqual(record.status, "COMPLETED")
        self.assertFalse(record.is_disposable_vm)
        self.assertEqual(record.summary.total_scenarios, 15)

        # 100% exact set accuracy across ground-truth suite
        self.assertEqual(record.summary.exact_set_accuracy.numerator, 15)
        self.assertEqual(record.summary.exact_set_accuracy.percentage, 100.0)

        # 100% element precision and recall
        self.assertEqual(record.summary.element_precision.percentage, 100.0)
        self.assertEqual(record.summary.element_recall.percentage, 100.0)

        # 100% bystander rejection
        self.assertEqual(record.summary.bystander_rejection_rate.percentage, 100.0)

        # 100% inconclusive handling
        self.assertEqual(record.summary.inconclusive_correctness_rate.percentage, 100.0)

        # Zero false attribution
        self.assertEqual(record.summary.false_attribution_rate.numerator, 0)
        self.assertEqual(record.summary.false_attribution_rate.percentage, 0.0)

    def test_cancellation_preserves_partial_benchmark_results(self):
        cancel_tok = CancellationToken()
        runner = BenchmarkRunner(cancellation_token=cancel_tok)

        # Pre-cancel before launch
        cancel_tok.cancel(reason="User interrupted benchmark")
        record = runner.run_suite()
        self.assertEqual(record.status, "CANCELLED")
        self.assertEqual(len(record.scenario_results), 0)

    def test_hyperv_refusal_without_explicit_opt_in(self):
        suite = BenchmarkSuite(
            scenarios=[
                BenchmarkScenario(
                    scenario_id="bench-vm-safety",
                    version="1.0.0",
                    category=BenchmarkCategory.SINGLE_CAUSE,
                    title="Real VM safety check",
                    description="",
                    adapter_type=AdapterType.HYPERV,
                    ground_truth_causes=["C1"],
                    all_candidate_ids=["C1"],
                    candidates=[
                        CandidateSpec(candidate_id="C1", category="registry", object_key="K1", after_value="1")
                    ],
                    health_check=HealthCheckSpec(),
                    expected_classification=ExpectedVerdict.VERIFIED_SET,
                )
            ]
        )
        runner = BenchmarkRunner(allow_real_vm=False)
        with self.assertRaises(PermissionError):
            runner.run_suite(suite=suite)

    def test_benchmark_sqlite_persistence_and_retrieval(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "bench_test.db"
            storage = Storage(db_path)
            self.assertEqual(storage.get_current_schema_version(), 10)

            runner = BenchmarkRunner(storage=storage)
            record = runner.run_suite()
            storage.save_benchmark_run(record)

            loaded = storage.get_benchmark_run(record.run_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.run_id, record.run_id)
            self.assertEqual(loaded.summary.total_scenarios, 15)
            self.assertEqual(len(loaded.scenario_results), 15)

            # Test list runs
            runs = storage.list_benchmark_runs()
            self.assertEqual(len(runs), 1)

            # Test text & JSON formatting on loaded record
            txt = format_benchmark_report_text(loaded)
            self.assertIn("STATEBISECT REPRODUCIBLE BENCHMARK REPORT", txt)
            self.assertIn("Exact Set Accuracy", txt)

            js = format_benchmark_report_json(loaded)
            data = json.loads(js)
            self.assertEqual(data["run_id"], record.run_id)

            # Test delete run
            deleted = storage.delete_benchmark_run(record.run_id)
            self.assertTrue(deleted)
            self.assertIsNone(storage.get_benchmark_run(record.run_id))

            storage.close()


if __name__ == "__main__":
    unittest.main()
