"""Data models for Phase 5 targeted, opt-in Windows change monitoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MonitoringSessionStatus(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class EventConfidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    INCONCLUSIVE = "INCONCLUSIVE"


class ProcessProvenance(str, Enum):
    EVENT_PAYLOAD = "EVENT_PAYLOAD"      # Extracted directly from OS event record fields (PID, ImageName)
    PROCESS_LOOKUP = "PROCESS_LOOKUP"    # Resolved via active process table at event time
    UNAVAILABLE = "UNAVAILABLE"          # Process identity not present in source telemetry


@dataclass
class MonitoringCoverageGap:
    """Represents a source-specific monitoring limitation or permission denial."""

    gap_id: str
    session_id: str
    source_id: str
    category: str
    reason: str
    impact: str


@dataclass
class MonitoringEvent:
    """Normalized observation record produced during a monitoring session."""

    event_id: str
    session_id: str
    source_id: str
    source_version: str
    category: str
    timestamp_utc: str
    source_timestamp: str
    channel_or_path: str
    event_code: str
    process_id: int | None = None
    process_name: str | None = None
    process_provenance: ProcessProvenance = ProcessProvenance.UNAVAILABLE
    payload: dict[str, Any] = field(default_factory=dict)
    is_redacted: bool = False
    is_truncated: bool = False
    confidence: EventConfidence = EventConfidence.CONFIRMED
    raw_ref: str = ""


@dataclass
class MonitoringSession:
    """Metadata and collected observations of a bounded monitoring session."""

    session_id: str
    created_at: str
    started_at: str
    stopped_at: str
    duration_sec: int
    elapsed_sec: float
    status: MonitoringSessionStatus
    stop_reason: str = ""
    pre_snapshot_run_id: str | None = None
    post_snapshot_run_id: str | None = None
    sources_requested: list[str] = field(default_factory=list)
    sources_active: list[str] = field(default_factory=list)
    total_events: int = 0
    events_by_source: dict[str, int] = field(default_factory=dict)
    coverage_gaps: list[MonitoringCoverageGap] = field(default_factory=list)
    loss_indicators: dict[str, Any] = field(default_factory=dict)
    events: list[MonitoringEvent] = field(default_factory=list)
