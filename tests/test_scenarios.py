"""Tests for declarative scenario validation, parsing, and safety enforcement."""

import json
import tempfile
import unittest
from pathlib import Path

from statebisect.scenarios import (
    CURRENT_SCHEMA_VERSION,
    DeclarativeScenario,
    ScenarioValidationError,
    load_scenario_file,
    validate_and_parse_scenario,
)


class TestScenarioValidation(unittest.TestCase):
    def get_valid_scenario_dict(self) -> dict:
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "id": "scen-test",
            "name": "Test Scenario",
            "description": "A valid test scenario",
            "simulation": True,
            "baseline": {
                "baseline_id": "base-01",
                "initial_state": {"flag": False, "port": 80},
            },
            "changes": [
                {
                    "id": "c1",
                    "category": "net",
                    "description": "Change port",
                    "dependencies": [],
                    "operation": "set_key",
                    "parameters": {"key": "port", "value": 8080},
                },
                {
                    "id": "c2",
                    "category": "logic",
                    "description": "Enable flag",
                    "dependencies": ["c1"],
                    "operation": "set_key",
                    "parameters": {"key": "flag", "value": True},
                },
            ],
            "health_check": {
                "type": "key_value_match",
                "parameters": {"key": "flag", "expected_value": True},
                "timeout_ms": 3000,
            },
        }

    def test_valid_scenario_parses_successfully(self):
        data = self.get_valid_scenario_dict()
        sc = validate_and_parse_scenario(data)
        self.assertIsInstance(sc, DeclarativeScenario)
        self.assertEqual(sc.id, "scen-test")
        self.assertEqual(len(sc.changes), 2)
        self.assertEqual(sc.baseline_id, "base-01")
        self.assertTrue(sc.simulation)
        self.assertTrue(sc.content_hash)

    def test_unknown_schema_version_rejected(self):
        data = self.get_valid_scenario_dict()
        data["schema_version"] = "99.0.0"
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_and_parse_scenario(data)
        self.assertIn("Unsupported schema_version", str(ctx.exception))

    def test_missing_schema_version_rejected(self):
        data = self.get_valid_scenario_dict()
        del data["schema_version"]
        with self.assertRaises(ScenarioValidationError):
            validate_and_parse_scenario(data)

    def test_simulation_false_rejected(self):
        data = self.get_valid_scenario_dict()
        data["simulation"] = False
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_and_parse_scenario(data)
        self.assertIn("simulation", str(ctx.exception))

    def test_duplicate_change_ids_rejected(self):
        data = self.get_valid_scenario_dict()
        data["changes"].append(
            {
                "id": "c1",  # Duplicate ID
                "category": "dup",
                "description": "Dup",
                "dependencies": [],
                "operation": "set_key",
                "parameters": {"key": "x", "value": 1},
            }
        )
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_and_parse_scenario(data)
        self.assertIn("Duplicate change id 'c1'", str(ctx.exception))

    def test_missing_dependency_rejected(self):
        data = self.get_valid_scenario_dict()
        data["changes"][0]["dependencies"] = ["nonexistent_dep"]
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_and_parse_scenario(data)
        self.assertIn("depends on 'nonexistent_dep', which does not exist", str(ctx.exception))

    def test_dependency_cycle_rejected(self):
        data = self.get_valid_scenario_dict()
        data["changes"][0]["dependencies"] = ["c2"]  # c1 -> c2 and c2 -> c1
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_and_parse_scenario(data)
        self.assertIn("Cyclic dependency detected", str(ctx.exception))

    def test_unknown_operation_rejected(self):
        data = self.get_valid_scenario_dict()
        data["changes"][0]["operation"] = "arbitrary_mutate"
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_and_parse_scenario(data)
        self.assertIn("invalid operation 'arbitrary_mutate'", str(ctx.exception))

    def test_unknown_health_check_type_rejected(self):
        data = self.get_valid_scenario_dict()
        data["health_check"]["type"] = "run_bash_script"
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_and_parse_scenario(data)
        self.assertIn("Invalid health check type", str(ctx.exception))

    def test_forbidden_command_key_rejected(self):
        for forbidden in ["command", "cmd", "script", "eval", "powershell", "shell"]:
            data = self.get_valid_scenario_dict()
            data["changes"][0][forbidden] = "rm -rf /"
            with self.assertRaises(ScenarioValidationError) as ctx:
                validate_and_parse_scenario(data)
            self.assertIn(f"Forbidden executable or script property '{forbidden}'", str(ctx.exception))

    def test_path_traversal_in_file_loading_rejected(self):
        with self.assertRaises(ScenarioValidationError) as ctx:
            load_scenario_file("../outside.json")
        self.assertIn("Path traversal", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
