"""Abstract base class for Phase 5 temporary monitoring sources."""

from __future__ import annotations

import abc
from typing import Any

from .models import MonitoringCoverageGap, MonitoringEvent


class BaseMonitorSource(abc.ABC):
    """Abstract contract for all opt-in, read-only Windows monitoring sources."""

    def __init__(
        self,
        source_id: str,
        category: str,
        description: str,
        version: str = "1.0.0",
        permission_requirement: str = "Standard User",
    ) -> None:
        self.source_id = source_id
        self.category = category
        self.description = description
        self.version = version
        self.permission_requirement = permission_requirement
        self.is_read_only = True
        self.session_id: str = ""
        self.start_time_utc: str = ""

    def preview(self) -> dict[str, Any]:
        """Return preview metadata without activating any collection."""
        supported, reason = self.is_supported()
        return {
            "source_id": self.source_id,
            "category": self.category,
            "description": self.description,
            "version": self.version,
            "read_only": self.is_read_only,
            "permission_requirement": self.permission_requirement,
            "supported": supported,
            "support_note": reason,
        }

    @abc.abstractmethod
    def is_supported(self) -> tuple[bool, str]:
        """Check if source is available and supported on the current host.

        Returns:
            Tuple of (is_supported: bool, diagnostic_message: str)
        """
        raise NotImplementedError

    @abc.abstractmethod
    def start(self, session_id: str, start_time_utc: str) -> list[MonitoringCoverageGap]:
        """Initialize and arm the monitoring source.

        Returns any initial coverage gaps encountered during startup (e.g. partial access).
        """
        raise NotImplementedError

    @abc.abstractmethod
    def poll(self) -> list[MonitoringEvent]:
        """Fetch newly observed events since the previous poll or start.

        Must be non-blocking and safe to call periodically.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def stop(self) -> list[MonitoringCoverageGap]:
        """Disarm the source, close any active handles, and flush final state.

        Returns any coverage gaps or loss notifications observed during the session.
        """
        raise NotImplementedError

    def get_loss_indicators(self) -> dict[str, Any]:
        """Return statistics on buffer drops, truncation, or rate limiting."""
        return {
            "events_dropped": 0,
            "buffer_overflows": 0,
            "truncations": 0,
        }
