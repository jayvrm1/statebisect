"""Windows Collector engine orchestrating read-only evidence collection."""

from __future__ import annotations

import platform
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .base import BaseCollectionSource
from .models import CollectionResult, CollectionRun, CoverageGap, SnapshotRecord, SourceStatus
from .sources import (
    DriversSource,
    EventLogsSource,
    InstalledSoftwareSource,
    NetworkSource,
    OsInfoSource,
    ServicesSource,
    WindowsUpdatesSource,
)

COLLECTOR_VERSION = "1.0.0"


class PlatformNotSupportedError(RuntimeError):
    """Raised when collector execution is attempted on a non-Windows platform."""


class WindowsCollector:
    """Orchestrates read-only collection of system telemetry and inventory snapshots."""

    def __init__(
        self,
        sources: list[BaseCollectionSource] | None = None,
        allow_non_windows: bool = False,
    ) -> None:
        self.version = COLLECTOR_VERSION
        self.read_only_declaration = True
        self.allow_non_windows = allow_non_windows

        if sys.platform != "win32" and not allow_non_windows:
            raise PlatformNotSupportedError(
                f"StateBisect WindowsCollector requires Microsoft Windows. Current platform: '{sys.platform}'."
            )

        self.sources = sources if sources is not None else [
            OsInfoSource(),
            InstalledSoftwareSource(),
            WindowsUpdatesSource(),
            ServicesSource(),
            DriversSource(),
            NetworkSource(),
            EventLogsSource(),
        ]

    def preview(self, lookback_sec: int = 3600, max_records: int = 100) -> dict[str, Any]:
        """Generate a preview of intended sources without executing collection."""
        return {
            "collector_version": COLLECTOR_VERSION,
            "os_platform": platform.platform(),
            "sources_count": len(self.sources),
            "sources": [s.preview(lookback_sec=lookback_sec, max_records=max_records) for s in self.sources],
        }

    def collect(
        self,
        lookback_sec: int = 3600,
        max_records_per_source: int = 100,
        allow_non_windows: bool = False,
    ) -> CollectionRun:
        """Execute read-only snapshot collection across all configured sources.

        Args:
            lookback_sec: Lookback window in seconds for time-bounded sources like Event Logs.
            max_records_per_source: Upper limit on records collected per individual source.
            allow_non_windows: Test flag allowing execution on non-Windows platforms.
        """
        if sys.platform != "win32" and not (allow_non_windows or self.allow_non_windows):
            raise PlatformNotSupportedError(
                f"StateBisect WindowsCollector requires Microsoft Windows. Current platform: '{sys.platform}'."
            )

        run_id = f"coll-{uuid.uuid4().hex[:10]}"
        created_at = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()

        all_records: list[SnapshotRecord] = []
        all_gaps: list[CoverageGap] = []
        source_summaries: dict[str, Any] = {}

        machine_id = "unknown"

        for src in self.sources:
            res: CollectionResult = src.collect(
                run_id=run_id,
                lookback_sec=lookback_sec,
                max_records=max_records_per_source,
            )
            all_records.extend(res.records)
            all_gaps.extend(res.coverage_gaps)
            source_summaries[src.source_id] = {
                "category": src.category,
                "status": res.status.value,
                "record_count": len(res.records),
                "gap_count": len(res.coverage_gaps),
                "duration_ms": res.duration_ms,
                "notes": res.notes,
            }
            if src.source_id == "os_info" and res.records:
                machine_id = res.records[0].data.get("machine_id", machine_id)

        duration_ms = (time.perf_counter() - t0) * 1000.0
        finished_at = datetime.now(timezone.utc).isoformat()

        # Determine overall run status
        has_error = any(s["status"] == SourceStatus.ERROR.value for s in source_summaries.values())
        has_partial = any(s["status"] in (SourceStatus.PARTIAL.value, SourceStatus.UNAVAILABLE.value) for s in source_summaries.values())

        if has_error:
            overall_status = "PARTIAL"
        elif has_partial:
            overall_status = "PARTIAL"
        else:
            overall_status = "SUCCESS"

        summary = {
            "sources": source_summaries,
            "total_records": len(all_records),
            "total_gaps": len(all_gaps),
            "lookback_sec": lookback_sec,
            "record_limit_per_source": max_records_per_source,
        }

        return CollectionRun(
            run_id=run_id,
            created_at=created_at,
            finished_at=finished_at,
            collector_version=COLLECTOR_VERSION,
            os_platform=platform.platform(),
            machine_id=machine_id,
            status=overall_status,
            duration_ms=round(duration_ms, 2),
            summary=summary,
            records=all_records,
            coverage_gaps=all_gaps,
        )
