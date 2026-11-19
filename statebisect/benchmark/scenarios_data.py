"""Canonical definitions for the 15 reproducible benchmark scenarios."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from statebisect.adapters.models import AdapterType
from .models import (
    BenchmarkCategory,
    BenchmarkScenario,
    CandidateSpec,
    ExpectedVerdict,
    HealthCheckSpec,
)


def get_all_scenarios() -> list[BenchmarkScenario]:
    """Return the complete suite of 15 ground-truth benchmark scenarios."""
    scenarios: list[BenchmarkScenario] = []

    # 1. Single Cause
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-01-single-cause",
            version="1.0.0",
            category=BenchmarkCategory.SINGLE_CAUSE,
            title="Single Configuration Change Failure",
            description="A single invalid registry value causes service health check to fail immediately.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1"],
            candidates=[
                CandidateSpec(
                    candidate_id="C1",
                    category="registry",
                    object_key="HKLM\\Software\\App\\Timeout",
                    after_value="0",
                    before_value="30",
                    relative_time_seconds=-120.0,
                    description="Service timeout reduced to zero",
                )
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[{"key": "HKLM\\Software\\App\\Timeout", "value": "0"}],
            ),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
            baseline_id="baseline-001",
            owner_notes="Clean 1-change baseline failure.",
        )
    )

    # 2. Single Cause with Bystanders
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-02-single-with-bystanders",
            version="1.0.0",
            category=BenchmarkCategory.SINGLE_CAUSE_WITH_BYSTANDERS,
            title="Single Causal Change with 7 Bystander Changes",
            description="Seven innocent UI/cache changes co-occur with one breaking TLS protocol downgrade.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C4"],
            all_candidate_ids=["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKCU\\Software\\App\\Theme", after_value="Dark", relative_time_seconds=-500),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKCU\\Software\\App\\Font", after_value="Segoe UI", relative_time_seconds=-450),
                CandidateSpec(candidate_id="C3", category="registry", object_key="HKCU\\Software\\App\\WindowLeft", after_value="100", relative_time_seconds=-400),
                CandidateSpec(candidate_id="C4", category="registry", object_key="HKLM\\Software\\App\\SSLProtocol", after_value="SSL3", relative_time_seconds=-350, description="Insecure protocol forced"),
                CandidateSpec(candidate_id="C5", category="registry", object_key="HKCU\\Software\\App\\MRU", after_value="file1.txt", relative_time_seconds=-300),
                CandidateSpec(candidate_id="C6", category="registry", object_key="HKCU\\Software\\App\\Toolbar", after_value="Compact", relative_time_seconds=-250),
                CandidateSpec(candidate_id="C7", category="registry", object_key="HKCU\\Software\\App\\CheckUpdates", after_value="1", relative_time_seconds=-200),
                CandidateSpec(candidate_id="C8", category="registry", object_key="HKCU\\Software\\App\\LogVerbosity", after_value="Debug", relative_time_seconds=-150),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[{"key": "HKLM\\Software\\App\\SSLProtocol", "value": "SSL3"}],
            ),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
            baseline_id="baseline-001",
            owner_notes="Tests bystander rejection across 8 candidates.",
        )
    )

    # 3. Two-Change Interaction
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-03-two-change-interaction",
            version="1.0.0",
            category=BenchmarkCategory.TWO_CHANGE_INTERACTION,
            title="Two-Change Interaction Failure (C3 + C6)",
            description="Neither change causes failure alone. Only the combination of ProxyEnabled=1 and DisableCertCheck=1 breaks the client.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C3", "C6"],
            all_candidate_ids=["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\WorkerThreads", after_value="8", relative_time_seconds=-800),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKLM\\Software\\App\\MaxSockets", after_value="1024", relative_time_seconds=-750),
                CandidateSpec(candidate_id="C3", category="registry", object_key="HKLM\\Software\\App\\ProxyEnabled", after_value="1", relative_time_seconds=-700),
                CandidateSpec(candidate_id="C4", category="registry", object_key="HKLM\\Software\\App\\CacheSizeMB", after_value="256", relative_time_seconds=-650),
                CandidateSpec(candidate_id="C5", category="registry", object_key="HKLM\\Software\\App\\KeepAliveSec", after_value="60", relative_time_seconds=-600),
                CandidateSpec(candidate_id="C6", category="registry", object_key="HKLM\\Software\\App\\DisableCertValidation", after_value="1", relative_time_seconds=-550),
                CandidateSpec(candidate_id="C7", category="registry", object_key="HKLM\\Software\\App\\Compression", after_value="gzip", relative_time_seconds=-500),
                CandidateSpec(candidate_id="C8", category="registry", object_key="HKLM\\Software\\App\\RetryCount", after_value="3", relative_time_seconds=-450),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[
                    {
                        "type": "interaction",
                        "keys": ["HKLM\\Software\\App\\ProxyEnabled", "HKLM\\Software\\App\\DisableCertValidation"],
                        "values": ["1", "1"],
                    }
                ],
            ),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
            baseline_id="baseline-001",
            owner_notes="Requires interaction recognition algorithm in delta debugging.",
        )
    )

    # 4. Three-Change Interaction
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-04-three-change-interaction",
            version="1.0.0",
            category=BenchmarkCategory.THREE_CHANGE_INTERACTION,
            title="Three-Change Interaction Failure (C1 + C4 + C7)",
            description="Requires three disparate feature flags to all be active to reproduce a crash.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1", "C4", "C7"],
            all_candidate_ids=["C1", "C2", "C3", "C4", "C5", "C6", "C7"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\FeatureX", after_value="1", relative_time_seconds=-700),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKLM\\Software\\App\\FeatureA", after_value="0", relative_time_seconds=-650),
                CandidateSpec(candidate_id="C3", category="registry", object_key="HKLM\\Software\\App\\FeatureB", after_value="0", relative_time_seconds=-600),
                CandidateSpec(candidate_id="C4", category="registry", object_key="HKLM\\Software\\App\\FeatureY", after_value="1", relative_time_seconds=-550),
                CandidateSpec(candidate_id="C5", category="registry", object_key="HKLM\\Software\\App\\FeatureC", after_value="0", relative_time_seconds=-500),
                CandidateSpec(candidate_id="C6", category="registry", object_key="HKLM\\Software\\App\\FeatureD", after_value="0", relative_time_seconds=-450),
                CandidateSpec(candidate_id="C7", category="registry", object_key="HKLM\\Software\\App\\FeatureZ", after_value="1", relative_time_seconds=-400),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[
                    {
                        "type": "interaction",
                        "keys": ["HKLM\\Software\\App\\FeatureX", "HKLM\\Software\\App\\FeatureY", "HKLM\\Software\\App\\FeatureZ"],
                        "values": ["1", "1", "1"],
                    }
                ],
            ),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
            baseline_id="baseline-001",
            owner_notes="Multi-way interaction stress test.",
        )
    )

    # 5. Bystander with Dependency
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-05-bystander-with-dependency",
            version="1.0.0",
            category=BenchmarkCategory.BYSTANDER_WITH_DEPENDENCY,
            title="Failure Cause Depends on Prerequisite with Unrelated Bystanders",
            description="Service install (C1) and service start (C2) where C2 depends on C1. C3 and C4 are bystanders.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1", "C2"],
            all_candidate_ids=["C1", "C2", "C3", "C4"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="service", object_key="Services\\BrokenSvc\\Installed", after_value="1", relative_time_seconds=-300),
                CandidateSpec(candidate_id="C2", category="service", object_key="Services\\BrokenSvc\\Running", after_value="1", dependencies=["C1"], relative_time_seconds=-200),
                CandidateSpec(candidate_id="C3", category="registry", object_key="HKCU\\Software\\App\\DesktopPos", after_value="10", relative_time_seconds=-100),
                CandidateSpec(candidate_id="C4", category="registry", object_key="HKCU\\Software\\App\\Volume", after_value="80", relative_time_seconds=-50),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[
                    {
                        "type": "interaction",
                        "keys": ["Services\\BrokenSvc\\Installed", "Services\\BrokenSvc\\Running"],
                        "values": ["1", "1"],
                    }
                ],
            ),
            expected_classification=ExpectedVerdict.INCONCLUSIVE_SUBSET,
            baseline_id="baseline-001",
            owner_notes="Evaluates dependency order resolution and bystander exclusion.",
        )
    )

    # 6. Dependency Constrained
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-06-dependency-constrained",
            version="1.0.0",
            category=BenchmarkCategory.DEPENDENCY_CONSTRAINED,
            title="Ordered Dependency-Constrained Configuration Changes",
            description="Database migration (C1) followed by application schema bump (C2) where C2 depends on C1.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1", "C2"],
            all_candidate_ids=["C1", "C2", "C3"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="file", object_key="AppData\\SchemaVersion", after_value="v2", relative_time_seconds=-400),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKLM\\Software\\App\\RequireV2", after_value="1", dependencies=["C1"], relative_time_seconds=-200),
                CandidateSpec(candidate_id="C3", category="registry", object_key="HKCU\\Software\\App\\IconCache", after_value="reset", relative_time_seconds=-100),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[
                    {
                        "type": "interaction",
                        "keys": ["AppData\\SchemaVersion", "HKLM\\Software\\App\\RequireV2"],
                        "values": ["v2", "1"],
                    }
                ],
            ),
            expected_classification=ExpectedVerdict.INCONCLUSIVE_SUBSET,
            baseline_id="baseline-001",
            owner_notes="Tests topological change sorting during trial execution.",
        )
    )

    # 7. Multiple Minimal Sets
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-07-multiple-minimal-sets",
            version="1.0.0",
            category=BenchmarkCategory.MULTIPLE_MINIMAL_SETS,
            title="Multiple Minimal Sets (C1 alone OR C2+C3)",
            description="Two distinct configuration errors can each independently trigger the failure. Delta debugging isolates one 1-minimal set.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1", "C2", "C3", "C4"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\BadPort", after_value="1", relative_time_seconds=-300),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKLM\\Software\\App\\UseAuth", after_value="1", relative_time_seconds=-200),
                CandidateSpec(candidate_id="C3", category="registry", object_key="HKLM\\Software\\App\\EmptySecret", after_value="1", relative_time_seconds=-100),
                CandidateSpec(candidate_id="C4", category="registry", object_key="HKCU\\Software\\App\\Bystander", after_value="1", relative_time_seconds=-50),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[
                    {"key": "HKLM\\Software\\App\\BadPort", "value": "1"},
                    {
                        "type": "interaction",
                        "keys": ["HKLM\\Software\\App\\UseAuth", "HKLM\\Software\\App\\EmptySecret"],
                        "values": ["1", "1"],
                    },
                ],
            ),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
            baseline_id="baseline-001",
            owner_notes="Verifies 1-minimality when multiple minimal failure sets exist.",
        )
    )

    # 8. Intermittent Health Check
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-08-intermittent-health-check",
            version="1.0.0",
            category=BenchmarkCategory.INTERMITTENT_HEALTH_CHECK,
            title="Intermittent / Flaky Health Check Failure",
            description="The target symptom is non-deterministic (network jitter), causing health check to oscillate between PASS and FAIL.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=[],
            all_candidate_ids=["C1", "C2"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\SettingA", after_value="1", relative_time_seconds=-100),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKLM\\Software\\App\\SettingB", after_value="1", relative_time_seconds=-50),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                is_intermittent=True,
                intermittent_rate=0.5,
                failure_conditions=[{"key": "HKLM\\Software\\App\\SettingA", "value": "1"}],
            ),
            expected_classification=ExpectedVerdict.INCONCLUSIVE,
            baseline_id="baseline-001",
            owner_notes="Must yield INCONCLUSIVE rather than falsely asserting deterministic causation.",
        )
    )

    # 9. Unreproducible Failure
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-09-unreproducible-failure",
            version="1.0.0",
            category=BenchmarkCategory.UNREPRODUCIBLE_FAILURE,
            title="Reported Failure Cannot Be Reproduced (All Trials Pass)",
            description="Applying all candidate changes passes the health check. Failure cannot be reproduced under test oracle.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=[],
            all_candidate_ids=["C1", "C2", "C3"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\Setting1", after_value="A", relative_time_seconds=-300),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKLM\\Software\\App\\Setting2", after_value="B", relative_time_seconds=-200),
                CandidateSpec(candidate_id="C3", category="registry", object_key="HKLM\\Software\\App\\Setting3", after_value="C", relative_time_seconds=-100),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[],  # Never triggers failure
            ),
            expected_classification=ExpectedVerdict.INCONCLUSIVE,
            baseline_id="baseline-001",
            owner_notes="Full candidate set passes oracle; engine aborts without false accusations.",
        )
    )

    # 10. Missing Telemetry Gaps
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-10-missing-telemetry-gaps",
            version="1.0.0",
            category=BenchmarkCategory.MISSING_TELEMETRY_GAPS,
            title="Root Cause in Unmonitored Hive / Coverage Gap",
            description="The real causal change occurred in an unmonitored hive. Recorded candidates cannot produce failure.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=[],
            all_candidate_ids=["C1", "C2"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKCU\\Software\\Monitored\\Key1", after_value="Val1", relative_time_seconds=-100),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKCU\\Software\\Monitored\\Key2", after_value="Val2", relative_time_seconds=-50),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[{"key": "HKLM\\SYSTEM\\UnmonitoredDriver\\Setting", "value": "corrupt"}],
            ),
            expected_classification=ExpectedVerdict.INCONCLUSIVE,
            baseline_id="baseline-001",
            expected_limitations=["True root cause resided in unmonitored system hive."],
            owner_notes="Ensures coverage limitations are reported instead of claiming innocent changes caused the bug.",
        )
    )

    # 11. Failed Baseline Restore
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-11-failed-baseline-restore",
            version="1.0.0",
            category=BenchmarkCategory.FAILED_BASELINE_RESTORE,
            title="Adapter Baseline Restoration Fails Before Trial",
            description="The underlying snapshot or checkpoint restore operation fails. Engine halts safely.",
            adapter_type=AdapterType.FAKE,
            fault_injection={"fail_restore": True},
            ground_truth_causes=[],
            all_candidate_ids=["C1"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\Key", after_value="1", relative_time_seconds=-60)
            ],
            health_check=HealthCheckSpec(check_type="simulated_state"),
            expected_classification=ExpectedVerdict.INCONCLUSIVE,
            baseline_id="fake-base-001",
            owner_notes="Tests safety boundary: adapter restore failure immediately halts trials.",
        )
    )

    # 12. Cleanup Failure Corruption
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-12-cleanup-failure-corruption",
            version="1.0.0",
            category=BenchmarkCategory.CLEANUP_FAILURE_CORRUPTION,
            title="Post-Trial Cleanup Fails Corrupting Environment",
            description="Post-trial cleanup raises an exception. Environment status transitions to CORRUPTED and prevents subsequent runs.",
            adapter_type=AdapterType.FAKE,
            fault_injection={"fail_cleanup": True, "corrupt_on_cleanup_failure": True},
            ground_truth_causes=[],
            all_candidate_ids=["C1", "C2"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\A", after_value="1", relative_time_seconds=-100),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKLM\\Software\\App\\B", after_value="1", relative_time_seconds=-50),
            ],
            health_check=HealthCheckSpec(check_type="simulated_state"),
            expected_classification=ExpectedVerdict.INCONCLUSIVE,
            baseline_id="fake-base-001",
            owner_notes="Verifies environment corruption handling and exception safety.",
        )
    )

    # 13. Differential Baseline
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-13-differential-baseline",
            version="1.0.0",
            category=BenchmarkCategory.DIFFERENTIAL_BASELINE,
            title="Differential Validation Across Compatible Baselines",
            description="Candidate C1 reproduces failure on Windows 11 baseline, but passes cleanly on Windows 10 baseline.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1", "C2"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\Win11ModernStyle", after_value="1", relative_time_seconds=-200),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKCU\\Software\\App\\Bystander", after_value="0", relative_time_seconds=-100),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[{"key": "HKLM\\Software\\App\\Win11ModernStyle", "value": "1"}],
            ),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
            baseline_id="baseline-001",
            differential_baseline_id="baseline-alt-002",
            owner_notes="Differential validation isolates version-specific baseline sensitivity.",
        )
    )

    # 14. False Temporal Correlation
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-14-false-temporal-correlation",
            version="1.0.0",
            category=BenchmarkCategory.FALSE_TEMPORAL_CORRELATION,
            title="Harmless Change at T-1s vs True Cause at T-2h",
            description="A benign UI setting changed 1 second before crash. The actual breaking config change occurred 2 hours earlier.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1", "C2"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\ListenPort", after_value="8081", relative_time_seconds=-7200.0, description="Port changed 2h ago"),
                CandidateSpec(candidate_id="C2", category="registry", object_key="HKCU\\Software\\App\\LastMousePos", after_value="120,450", relative_time_seconds=-1.0, description="Mouse moved 1s ago"),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[{"key": "HKLM\\Software\\App\\ListenPort", "value": "8081"}],
            ),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
            baseline_id="baseline-001",
            owner_notes="Ensures empirical delta debugging overrides temporal proximity bias.",
        )
    )

    # 15. Post-Onset Consequence
    scenarios.append(
        BenchmarkScenario(
            scenario_id="bench-15-post-onset-consequence",
            version="1.0.0",
            category=BenchmarkCategory.POST_ONSET_CONSEQUENCE,
            title="Crash Consequence Created at T+5s After Symptom Onset",
            description="WER crash report and error log entries generated after symptom onset are flagged as consequences rather than root causes.",
            adapter_type=AdapterType.SIMULATION,
            ground_truth_causes=["C1"],
            all_candidate_ids=["C1", "C2"],
            candidates=[
                CandidateSpec(candidate_id="C1", category="registry", object_key="HKLM\\Software\\App\\ConfigCorruption", after_value="true", relative_time_seconds=-100.0, is_post_onset=False),
                CandidateSpec(candidate_id="C2", category="file", object_key="AppData\\CrashDumps\\app.dmp", after_value="present", relative_time_seconds=5.0, is_post_onset=True),
            ],
            health_check=HealthCheckSpec(
                check_type="simulated_state",
                failure_conditions=[{"key": "HKLM\\Software\\App\\ConfigCorruption", "value": "true"}],
            ),
            expected_classification=ExpectedVerdict.VERIFIED_SET,
            baseline_id="baseline-001",
            owner_notes="Verifies post-onset consequence marker filtering.",
        )
    )

    return scenarios


def write_canonical_scenarios_to_disk(target_dir: Path | str) -> list[Path]:
    """Write canonical benchmark scenarios to JSON files on disk."""
    dir_path = Path(target_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for s in get_all_scenarios():
        p = dir_path / f"{s.scenario_id}.json"
        p.write_text(json.dumps(s.to_dict(), indent=2), encoding="utf-8")
        paths.append(p)
    return paths
