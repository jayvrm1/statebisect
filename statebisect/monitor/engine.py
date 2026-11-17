"""Monitoring engine orchestrating bounded, opt-in change observation sessions."""

from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from statebisect.collector.engine import WindowsCollector
from statebisect.models import CancellationToken
from statebisect.storage import Storage

from .base import BaseMonitorSource
from .models import (
    MonitoringCoverageGap,
    MonitoringEvent,
    MonitoringSession,
    MonitoringSessionStatus,
)
from .sources import (
    EtwEvaluationSource,
    EventLogMonitorSource,
    RegistryMonitorSource,
    SysmonMonitorSource,
)

MIN_DURATION_SEC = 1
DEFAULT_DURATION_SEC = 30
MAX_DURATION_SEC = 300
MAX_TOTAL_EVENTS = 2000
POLL_INTERVAL_SEC = 1.0


class MonitoringEngine:
    """Manages foreground, bounded change monitoring sessions."""

    def __init__(
        self,
        sources: list[BaseMonitorSource] | None = None,
        max_duration_sec: int = MAX_DURATION_SEC,
        max_total_events: int = MAX_TOTAL_EVENTS,
        poll_interval_sec: float = POLL_INTERVAL_SEC,
        allow_non_windows: bool = False,
    ) -> None:
        self.max_duration_sec = max_duration_sec
        self.max_total_events = max_total_events
        self.poll_interval_sec = poll_interval_sec
        self.allow_non_windows = allow_non_windows

        if sources is not None:
            self.sources = sources
        else:
            self.sources = [
                EventLogMonitorSource(),
                RegistryMonitorSource(),
                SysmonMonitorSource(),
                EtwEvaluationSource(),
            ]

    def preview(self) -> dict[str, Any]:
        """Generate a preview of available monitoring sources, permissions, and bounds."""
        return {
            "engine_version": "1.0.0",
            "platform": sys.platform,
            "min_duration_sec": MIN_DURATION_SEC,
            "default_duration_sec": DEFAULT_DURATION_SEC,
            "max_duration_sec": self.max_duration_sec,
            "max_total_events": self.max_total_events,
            "poll_interval_sec": self.poll_interval_sec,
            "sources_count": len(self.sources),
            "sources": [s.preview() for s in self.sources],
        }

    def run_session(
        self,
        duration_sec: int = DEFAULT_DURATION_SEC,
        sources_filter: list[str] | None = None,
        with_snapshots: bool = False,
        cancellation_token: CancellationToken | None = None,
        storage: Storage | None = None,
        event_callback: Callable[[MonitoringEvent], None] | None = None,
    ) -> MonitoringSession:
        """Execute a bounded foreground monitoring session.

        Guarantees:
            1. Bounded duration: terminates automatically after `duration_sec` (max bounded).
            2. Cancellation safety: stops immediately on cancellation token / Ctrl+C.
            3. Resource cleanup: all sources disarmed in an unconditional `finally` block.
            4. No background daemon or persistence outside SQLite records.
        """
        if duration_sec < MIN_DURATION_SEC:
            raise ValueError(f"Duration {duration_sec}s is below minimum {MIN_DURATION_SEC}s.")
        if duration_sec > self.max_duration_sec:
            raise ValueError(
                f"Duration {duration_sec}s exceeds safety bound of {self.max_duration_sec}s."
            )

        if sys.platform != "win32" and not self.allow_non_windows:
            raise RuntimeError(
                f"StateBisect monitoring requires Microsoft Windows. Current: '{sys.platform}'."
            )

        token = cancellation_token or CancellationToken()
        session_id = f"mon-{uuid.uuid4().hex[:10]}"
        created_at = datetime.now(timezone.utc).isoformat()

        # Select requested sources
        if sources_filter:
            selected_sources = [s for s in self.sources if s.source_id in sources_filter]
        else:
            selected_sources = list(self.sources)

        sources_requested = [s.source_id for s in selected_sources]
        sources_active: list[str] = []
        coverage_gaps: list[MonitoringCoverageGap] = []
        all_events: list[MonitoringEvent] = []
        loss_indicators: dict[str, Any] = {
            "events_dropped": 0,
            "buffer_overflows": 0,
            "truncations": 0,
            "notes": [],
        }

        pre_snapshot_id: str | None = None
        post_snapshot_id: str | None = None

        # Optional Pre-session Snapshot
        if with_snapshots:
            try:
                collector = WindowsCollector(allow_non_windows=self.allow_non_windows)
                pre_run = collector.collect(max_records_per_source=50)
                if storage:
                    storage.save_collection_run(pre_run)
                pre_snapshot_id = pre_run.run_id
            except Exception as err:
                coverage_gaps.append(
                    MonitoringCoverageGap(
                        gap_id=f"gap-pre-snap-{session_id}",
                        session_id=session_id,
                        source_id="snapshot",
                        category="snapshot",
                        reason=f"Failed capturing pre-session snapshot: {err}",
                        impact="Pre-session baseline snapshot comparison is unavailable.",
                    )
                )

        started_at = datetime.now(timezone.utc).isoformat()
        t_start = time.perf_counter()
        stop_reason = "COMPLETED_DURATION"

        # Arm active sources
        active_source_instances: list[BaseMonitorSource] = []
        for src in selected_sources:
            try:
                init_gaps = src.start(session_id, started_at)
                coverage_gaps.extend(init_gaps)
                supported, _ = src.is_supported()
                if supported:
                    sources_active.append(src.source_id)
                    active_source_instances.append(src)
            except Exception as err:
                coverage_gaps.append(
                    MonitoringCoverageGap(
                        gap_id=f"gap-start-{src.source_id}-{uuid.uuid4().hex[:6]}",
                        session_id=session_id,
                        source_id=src.source_id,
                        category=src.category,
                        reason=f"Exception initializing source {src.source_id}: {err}",
                        impact=f"Source {src.source_id} disabled for this session.",
                    )
                )

        try:
            # Active monitoring loop
            while True:
                elapsed = time.perf_counter() - t_start
                if elapsed >= duration_sec:
                    stop_reason = "COMPLETED_DURATION"
                    break

                if token.is_cancelled:
                    stop_reason = "USER_INTERRUPT"
                    break

                # Poll each active source
                for src in active_source_instances:
                    if token.is_cancelled:
                        break
                    try:
                        new_events = src.poll()
                        for ev in new_events:
                            if len(all_events) >= self.max_total_events:
                                loss_indicators["events_dropped"] += 1
                                loss_indicators["buffer_overflows"] += 1
                                if "Buffer capacity reached" not in loss_indicators["notes"]:
                                    loss_indicators["notes"].append("Buffer capacity reached; dropping excess events.")
                                stop_reason = "LIMIT_EXCEEDED"
                                break

                            all_events.append(ev)
                            if event_callback:
                                event_callback(ev)
                    except Exception as err:
                        coverage_gaps.append(
                            MonitoringCoverageGap(
                                gap_id=f"gap-poll-{src.source_id}-{uuid.uuid4().hex[:6]}",
                                session_id=session_id,
                                source_id=src.source_id,
                                category=src.category,
                                reason=f"Error polling source {src.source_id}: {err}",
                                impact=f"Some telemetry from {src.source_id} may be missing.",
                            )
                        )

                if stop_reason == "LIMIT_EXCEEDED":
                    break

                # Sleep in small slices to remain responsive to cancellation
                sleep_target = time.perf_counter() + self.poll_interval_sec
                while time.perf_counter() < sleep_target:
                    if token.is_cancelled:
                        stop_reason = "USER_INTERRUPT"
                        break
                    time.sleep(0.1)

                if token.is_cancelled:
                    stop_reason = "USER_INTERRUPT"
                    break

        except KeyboardInterrupt:
            stop_reason = "USER_INTERRUPT"
        except Exception as err:
            stop_reason = f"ERROR: {type(err).__name__}: {err}"

        finally:
            # Guaranteed shutdown of all sources
            for src in active_source_instances:
                try:
                    final_gaps = src.stop()
                    coverage_gaps.extend(final_gaps)
                    loss = src.get_loss_indicators()
                    loss_indicators["events_dropped"] += loss.get("events_dropped", 0)
                    loss_indicators["buffer_overflows"] += loss.get("buffer_overflows", 0)
                    loss_indicators["truncations"] += loss.get("truncations", 0)
                except Exception as err:
                    coverage_gaps.append(
                        MonitoringCoverageGap(
                            gap_id=f"gap-stop-{src.source_id}",
                            session_id=session_id,
                            source_id=src.source_id,
                            category=src.category,
                            reason=f"Error shutting down source {src.source_id}: {err}",
                            impact=f"Cleanup error on source {src.source_id}.",
                        )
                    )

            # Optional Post-session Snapshot
            if with_snapshots:
                try:
                    collector = WindowsCollector(allow_non_windows=self.allow_non_windows)
                    post_run = collector.collect(max_records_per_source=50)
                    if storage:
                        storage.save_collection_run(post_run)
                    post_snapshot_id = post_run.run_id
                except Exception as err:
                    coverage_gaps.append(
                        MonitoringCoverageGap(
                            gap_id=f"gap-post-snap-{session_id}",
                            session_id=session_id,
                            source_id="snapshot",
                            category="snapshot",
                            reason=f"Failed capturing post-session snapshot: {err}",
                            impact="Post-session snapshot comparison is unavailable.",
                        )
                    )

            elapsed_final = round(time.perf_counter() - t_start, 2)
            stopped_at = datetime.now(timezone.utc).isoformat()

            # Map status
            if stop_reason == "USER_INTERRUPT":
                status = MonitoringSessionStatus.CANCELLED
            elif "ERROR" in stop_reason:
                status = MonitoringSessionStatus.ERROR
            elif stop_reason == "LIMIT_EXCEEDED":
                status = MonitoringSessionStatus.COMPLETED
            else:
                status = MonitoringSessionStatus.COMPLETED

            # Tally events by source
            events_by_source: dict[str, int] = {}
            for ev in all_events:
                events_by_source[ev.source_id] = events_by_source.get(ev.source_id, 0) + 1

            session = MonitoringSession(
                session_id=session_id,
                created_at=created_at,
                started_at=started_at,
                stopped_at=stopped_at,
                duration_sec=duration_sec,
                elapsed_sec=elapsed_final,
                status=status,
                stop_reason=stop_reason,
                pre_snapshot_run_id=pre_snapshot_id,
                post_snapshot_run_id=post_snapshot_id,
                sources_requested=sources_requested,
                sources_active=sources_active,
                total_events=len(all_events),
                events_by_source=events_by_source,
                coverage_gaps=coverage_gaps,
                loss_indicators=loss_indicators,
                events=all_events,
            )

            if storage:
                storage.save_monitoring_session(session)

            return session
