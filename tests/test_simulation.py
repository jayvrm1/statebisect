"""Tests for the simulated workspace and dependency management."""

import unittest
from statebisect.models import Change
from statebisect.simulation import (
    BaselineIntegrityError,
    DependencyResolutionError,
    SimulatedWorkspace,
)


class TestSimulatedWorkspace(unittest.TestCase):
    def setUp(self):
        self.baseline = {"service_port": 8080, "auth_enabled": True}
        self.ws = SimulatedWorkspace(baseline_state=self.baseline)

    def test_baseline_initialization_and_verification(self):
        self.assertTrue(self.ws.verify_baseline())
        self.assertEqual(self.ws.current_state, self.baseline)

    def test_apply_changes_and_reset(self):
        c1 = Change(
            id="c1",
            category="config",
            description="Change port",
            payload={"service_port": 9090},
        )
        applied = self.ws.apply_changes([c1])
        self.assertEqual(applied, ["c1"])
        self.assertEqual(self.ws.current_state["service_port"], 9090)
        self.assertFalse(self.ws.verify_baseline())

        # Reset to baseline
        self.ws.reset_to_baseline()
        self.assertTrue(self.ws.verify_baseline())
        self.assertEqual(self.ws.current_state["service_port"], 8080)

    def test_dependency_ordering_applied_correctly(self):
        # c2 depends on c1
        c1 = Change(
            id="c1",
            category="config",
            description="Base setting",
            payload={"feature_x": "base"},
        )
        c2 = Change(
            id="c2",
            category="config",
            description="Derived setting",
            dependencies=("c1",),
            payload={"feature_x": "derived"},
        )
        # Pass in reverse order: [c2, c1]
        applied = self.ws.apply_changes([c2, c1])
        self.assertEqual(applied, ["c1", "c2"])
        self.assertEqual(self.ws.current_state["feature_x"], "derived")

    def test_missing_dependency_rejected(self):
        c2 = Change(
            id="c2",
            category="config",
            description="Needs c1",
            dependencies=("c1",),
            payload={"k": "v"},
        )
        with self.assertRaises(DependencyResolutionError) as ctx:
            self.ws.apply_changes([c2])
        self.assertIn("depends on 'c1', which is not in the selected change set", str(ctx.exception))

    def test_cyclic_dependency_rejected(self):
        c1 = Change(id="c1", category="c", description="1", dependencies=("c2",))
        c2 = Change(id="c2", category="c", description="2", dependencies=("c1",))
        with self.assertRaises(DependencyResolutionError) as ctx:
            self.ws.apply_changes([c1, c2])
        self.assertIn("Cyclic dependency", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
