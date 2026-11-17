"""Evaluation and documentation of Windows Sandbox suitability for StateBisect Delta Debugging experiments."""

from __future__ import annotations

from typing import Any

from .models import AdapterType


class WindowsSandboxEvaluation:
    """Evaluates Microsoft Windows Sandbox against StateBisect's trial requirements.

    Reference:
        Microsoft Windows Sandbox Configuration Guide:
        https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file

    Evaluation Findings:
        1. Ephemeral Container Architecture:
           Windows Sandbox runs as an ephemeral desktop environment. Whenever the Sandbox window
           closes, all software, files, and state are permanently discarded.
        2. Lack of Checkpoint / Snapshot Restores:
           Windows Sandbox does not have an API or mechanism to capture an in-memory or on-disk
           checkpoint baseline and restore back to that specific baseline between Delta Debugging trials.
        3. Reboot Overhead:
           Each new trial in Windows Sandbox requires a cold start from a base image (taking 10-30s
           per launch) and sequential re-execution of all configuration setup scripts via <LogonCommand>.
           In Delta Debugging with N=10..30 candidates requiring 15-40 trials, this would mean 10-20
           minutes of cold reboots with high risk of non-deterministic setup errors.
        4. Security & Isolation:
           Windows Sandbox provides excellent isolation, but mapping host folders or clipboard
           creates security risks unless strictly read-only.

    Conclusion:
        Windows Sandbox is DEFERRED for multi-trial Delta Debugging investigations.
        Hyper-V standard checkpoints provide the required microsecond-to-second state restore semantics.
    """

    @classmethod
    def get_evaluation_report(cls) -> dict[str, Any]:
        return {
            "adapter_type": AdapterType.WINDOWS_SANDBOX.value,
            "status": "DEFERRED",
            "decision_date": "2026-11-17",
            "primary_source": "https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/",
            "reasons_deferred": [
                "Ephemeral lifecycle: state is discarded when closed; no persistent named checkpoints exist.",
                "Absence of programmatic snapshot rollback API (unlike Hyper-V Restore-VMSnapshot).",
                "High cold-boot latency per trial (15-30s) compared to checkpoint resets (<2s).",
            ],
            "recommendation": (
                "Use SimulationAdapter for deterministic test runs and HyperVAdapter with "
                "standard checkpoints for isolated Windows experiments."
            ),
        }
