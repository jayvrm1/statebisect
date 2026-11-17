"""Base class and common contract for read-only collection sources."""

from __future__ import annotations

import abc
import time
from typing import Any

from .models import CollectionResult, CoverageGap, SourceStatus


class BaseCollectionSource(abc.ABC):
    """Abstract base class for all read-only Windows collection sources."""

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

    def preview(self, lookback_sec: int = 3600, max_records: int = 100) -> dict[str, Any]:
        """Return preview metadata without performing any collection."""
        return {
            "source_id": self.source_id,
            "category": self.category,
            "description": self.description,
            "version": self.version,
            "read_only": self.is_read_only,
            "permission_requirement": self.permission_requirement,
            "intended_lookback_sec": lookback_sec,
            "record_limit": max_records,
        }

    def collect(self, run_id: str = "", lookback_sec: int = 3600, max_records: int = 100) -> CollectionResult:
        """Execute read-only collection with error boundaries."""
        t0 = time.perf_counter()
        try:
            result = self._collect_internal(run_id, lookback_sec, max_records)
            result.duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return result
        except PermissionError as err:
            duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            gap = CoverageGap(
                gap_id=f"gap-{self.source_id}",
                run_id=run_id,
                source_id=self.source_id,
                category=self.category,
                reason=f"Access denied: {err}",
                impact=f"Telemetry from {self.source_id} is omitted from this snapshot.",
            )
            return CollectionResult(
                source_id=self.source_id,
                category=self.category,
                status=SourceStatus.UNAVAILABLE,
                coverage_gaps=[gap],
                duration_ms=duration_ms,
                notes=f"Skipped due to permissions: {err}",
            )
        except Exception as err:
            duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            gap = CoverageGap(
                gap_id=f"gap-{self.source_id}",
                run_id=run_id,
                source_id=self.source_id,
                category=self.category,
                reason=f"Collection error: {type(err).__name__}: {err}",
                impact=f"Collection failed for source {self.source_id}.",
            )
            return CollectionResult(
                source_id=self.source_id,
                category=self.category,
                status=SourceStatus.ERROR,
                coverage_gaps=[gap],
                duration_ms=duration_ms,
                notes=f"Failed with exception: {err}",
            )

    @abc.abstractmethod
    def _collect_internal(self, run_id: str, lookback_sec: int, max_records: int) -> CollectionResult:
        """Internal collection implementation."""
        raise NotImplementedError
