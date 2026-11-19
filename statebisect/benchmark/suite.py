"""Benchmark suite registry, loader, and cryptographic verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

from .models import BenchmarkCategory, BenchmarkScenario, ExpectedVerdict


class BenchmarkSuite:
    """Manages a versioned collection of ground-truth benchmark scenarios."""

    DEFAULT_SUITE_ID = "statebisect-standard-v1"
    DEFAULT_VERSION = "1.0.0"

    def __init__(
        self,
        suite_id: str = DEFAULT_SUITE_ID,
        version: str = DEFAULT_VERSION,
        scenarios: Sequence[BenchmarkScenario] | None = None,
    ) -> None:
        self.suite_id = suite_id
        self.version = version
        self._scenarios: dict[str, BenchmarkScenario] = {}
        if scenarios:
            for s in scenarios:
                self._scenarios[s.scenario_id] = s

    @property
    def scenarios(self) -> list[BenchmarkScenario]:
        return list(self._scenarios.values())

    def get_scenario(self, scenario_id: str) -> BenchmarkScenario | None:
        return self._scenarios.get(scenario_id)

    def compute_suite_hash(self) -> str:
        """Compute deterministic SHA-256 fingerprint of all scenarios in the suite."""
        hasher = hashlib.sha256()
        for sid in sorted(self._scenarios.keys()):
            sc = self._scenarios[sid]
            encoded = json.dumps(sc.to_dict(), sort_keys=True).encode("utf-8")
            hasher.update(encoded)
        return hasher.hexdigest()

    def validate(self) -> list[str]:
        """Validate scenario definitions, constraints, and dependencies.
        
        Returns a list of validation error messages (empty if valid).
        """
        errors: list[str] = []
        if not self._scenarios:
            errors.append("Suite contains zero scenarios.")
            return errors

        for s in self._scenarios.values():
            if not s.scenario_id or not s.scenario_id.strip():
                errors.append(f"Scenario missing scenario_id: {s}")
                continue

            # Verify category
            if not isinstance(s.category, BenchmarkCategory):
                errors.append(f"Scenario {s.scenario_id}: invalid category {s.category}")

            # Verify classification
            if not isinstance(s.expected_classification, ExpectedVerdict):
                errors.append(f"Scenario {s.scenario_id}: invalid expected_classification {s.expected_classification}")

            # Verify candidate IDs uniqueness and presence
            candidate_ids = [c.candidate_id for c in s.candidates]
            if len(candidate_ids) != len(set(candidate_ids)):
                errors.append(f"Scenario {s.scenario_id}: duplicate candidate_ids found")

            # Verify ground truth is subset of candidates (unless expected empty)
            for c_id in s.ground_truth_causes:
                if c_id not in candidate_ids:
                    errors.append(f"Scenario {s.scenario_id}: ground truth cause '{c_id}' not found in candidate list")

            # Verify candidate dependency references
            for c in s.candidates:
                for dep in c.dependencies:
                    if dep not in candidate_ids:
                        errors.append(f"Scenario {s.scenario_id}: candidate '{c.candidate_id}' references unknown dependency '{dep}'")
                    if dep == c.candidate_id:
                        errors.append(f"Scenario {s.scenario_id}: candidate '{c.candidate_id}' cannot depend on itself")

        return errors

    @classmethod
    def load_from_directory(cls, directory_path: str | Path | None = None) -> BenchmarkSuite:
        """Load benchmark suite from JSON files in a directory."""
        if directory_path is None:
            directory_path = Path(__file__).parent / "scenarios"
        else:
            directory_path = Path(directory_path)

        if not directory_path.exists() or not directory_path.is_dir():
            raise FileNotFoundError(f"Benchmark scenarios directory not found: {directory_path}")

        scenarios: list[BenchmarkScenario] = []
        for json_file in sorted(directory_path.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                scenarios.append(BenchmarkScenario.from_dict(data))
            except Exception as err:
                raise ValueError(f"Failed to load scenario file '{json_file.name}': {err}") from err

        return cls(scenarios=scenarios)

    @classmethod
    def get_default_suite(cls) -> BenchmarkSuite:
        """Load default built-in benchmark suite."""
        return cls.load_from_directory()
