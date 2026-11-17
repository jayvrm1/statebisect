"""Data models for the read-only Windows collector, snapshots, and diffs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SourceStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class DiffChangeType(str, Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"
    UNKNOWN = "UNKNOWN"


@dataclass
class CoverageGap:
    """Represents an unmonitored area or skipped source due to permissions or unavailability."""

    gap_id: str
    run_id: str
    source_id: str
    category: str
    reason: str
    impact: str


@dataclass
class SnapshotRecord:
    """A single normalized observation record within a collection snapshot."""

    record_id: str
    run_id: str
    category: str
    object_key: str
    data: dict[str, Any]
    timestamp: str
    source_id: str
    is_redacted: bool = False
    is_truncated: bool = False


@dataclass
class CollectionResult:
    """Outcome of running a single collection source."""

    source_id: str
    category: str
    status: SourceStatus
    records: list[SnapshotRecord] = field(default_factory=list)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    duration_ms: float = 0.0
    notes: str = ""


@dataclass
class CollectionRun:
    """Metadata and contents of a complete snapshot collection run."""

    run_id: str
    created_at: str
    finished_at: str
    collector_version: str
    os_platform: str
    machine_id: str
    status: str
    duration_ms: float
    summary: dict[str, Any] = field(default_factory=dict)
    records: list[SnapshotRecord] = field(default_factory=list)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)


@dataclass
class SnapshotDiff:
    """Result of comparing two snapshot records between collection runs."""

    diff_id: str
    before_run_id: str
    after_run_id: str
    category: str
    object_key: str
    change_type: DiffChangeType
    before_data: dict[str, Any] | None = None
    after_data: dict[str, Any] | None = None
    status: str = "CONFIRMED"
    notes: str = ""
