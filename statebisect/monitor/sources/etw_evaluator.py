"""ETW evaluation and safe deferral source."""

from __future__ import annotations

import sys
from typing import Any

from ..base import BaseMonitorSource
from ..models import MonitoringCoverageGap, MonitoringEvent


class EtwEvaluationSource(BaseMonitorSource):
    """Evaluates Event Tracing for Windows (ETW) feasibility and tracks safety limitations.

    Safety Decision (Primary Source: Microsoft Learn ETW Reference):
        Real-time ETW trace sessions require `TRACELOG_CREATE_REALTIME` permissions
        (Administrators or Performance Log Users group), and kernel logging requires
        administrative elevation. Creating persistent trace sessions violates StateBisect's
        core standard-user and bounded foreground session invariants.
        Therefore, ETW kernel tracing is safely deferred and tracked as a documented coverage gap.
    """

    def __init__(self) -> None:
        super().__init__(
            source_id="etw",
            category="etw",
            description="ETW real-time session evaluator (deferred for un-elevated standard user safety)",
            version="1.0.0",
            permission_requirement="Administrator / TRACELOG_CREATE_REALTIME (Deferred)",
        )

    def is_supported(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "Requires Microsoft Windows host environment"
        return False, (
            "Deferred: Real-time ETW sessions require elevated Administrator or "
            "TRACELOG_CREATE_REALTIME privileges. StateBisect operates strictly "
            "as standard user to prevent machine-wide tracing reconfiguration."
        )

    def start(self, session_id: str, start_time_utc: str) -> list[MonitoringCoverageGap]:
        self.session_id = session_id
        self.start_time_utc = start_time_utc
        _, reason = self.is_supported()
        return [
            MonitoringCoverageGap(
                gap_id=f"gap-{self.source_id}-deferred",
                session_id=session_id,
                source_id=self.source_id,
                category=self.category,
                reason=reason,
                impact="Kernel-level process, network, and file system trace events are deferred.",
            )
        ]

    def poll(self) -> list[MonitoringEvent]:
        # Always empty as ETW is safely deferred
        return []

    def stop(self) -> list[MonitoringCoverageGap]:
        return []

    def get_loss_indicators(self) -> dict[str, Any]:
        return {
            "events_dropped": 0,
            "buffer_overflows": 0,
            "truncations": 0,
        }
