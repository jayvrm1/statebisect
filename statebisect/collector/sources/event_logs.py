"""Selected Windows Event Logs collection source via wevtutil XML queries."""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any

from ..base import BaseCollectionSource
from ..models import CollectionResult, CoverageGap, SnapshotRecord, SourceStatus

MAX_EVENT_TEXT_LEN = 512


class EventLogsSource(BaseCollectionSource):
    """Collects recent time-bounded events from Application and System logs."""

    def __init__(self, channels: tuple[str, ...] = ("Application", "System")) -> None:
        super().__init__(
            source_id="event_logs",
            category="event_log",
            description="Recent warning and error events from Application and System logs",
            version="1.0.0",
            permission_requirement="Standard User",
        )
        self.channels = channels

    def _collect_internal(self, run_id: str, lookback_sec: int, max_records: int) -> CollectionResult:
        if sys.platform != "win32":
            gap = CoverageGap(
                gap_id=f"gap-{self.source_id}-nonwin",
                run_id=run_id,
                source_id=self.source_id,
                category=self.category,
                reason="Non-Windows platform; Windows Event Log service is unavailable.",
                impact="Windows Event Log collection skipped.",
            )
            return CollectionResult(
                source_id=self.source_id,
                category=self.category,
                status=SourceStatus.UNAVAILABLE,
                coverage_gaps=[gap],
                notes="Skipped on non-Windows.",
            )

        records: list[SnapshotRecord] = []
        gaps: list[CoverageGap] = []
        per_channel_limit = max(1, max_records // len(self.channels))

        for ch in self.channels:
            if ch.lower() == "security":
                # Explicitly exclude Security log to avoid permission escalation
                gaps.append(
                    CoverageGap(
                        gap_id=f"gap-{self.source_id}-security",
                        run_id=run_id,
                        source_id=self.source_id,
                        category=self.category,
                        reason="Security log is excluded by default to avoid privileged access requirements.",
                        impact="Security audit events are not included in snapshot.",
                    )
                )
                continue

            # Query wevtutil for newest events formatted as XML
            # Filter for Errors and Warnings: Level 1 (Critical), Level 2 (Error), Level 3 (Warning)
            cmd = [
                "wevtutil.exe",
                "qe",
                ch,
                f"/c:{per_channel_limit}",
                "/rd:true",
                "/f:xml",
                "/q:*[System[(Level=1 or Level=2 or Level=3)]]",
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15.0,
                    shell=False,
                    check=False,
                )
                if proc.returncode != 0:
                    err_msg = proc.stderr.strip()
                    gaps.append(
                        CoverageGap(
                            gap_id=f"gap-{self.source_id}-{ch}",
                            run_id=run_id,
                            source_id=self.source_id,
                            category=self.category,
                            reason=f"wevtutil failed for channel '{ch}' with code {proc.returncode}: {err_msg}",
                            impact=f"Events from {ch} could not be retrieved.",
                        )
                    )
                    continue

                raw_xml = proc.stdout.strip()
                if not raw_xml:
                    continue

                # wevtutil outputs multiple root <Event> elements concatenated without a single root
                wrapped_xml = f"<Events>{raw_xml}</Events>"
                root = ET.fromstring(wrapped_xml)
                ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

                for ev in root.findall("e:Event", ns):
                    sys_elem = ev.find("e:System", ns)
                    if sys_elem is None:
                        continue

                    provider = ""
                    prov_elem = sys_elem.find("e:Provider", ns)
                    if prov_elem is not None:
                        provider = prov_elem.get("Name", "")

                    event_id = ""
                    evid_elem = sys_elem.find("e:EventID", ns)
                    if evid_elem is not None:
                        event_id = evid_elem.text or ""

                    rec_id = ""
                    rec_elem = sys_elem.find("e:EventRecordID", ns)
                    if rec_elem is not None:
                        rec_id = rec_elem.text or ""

                    time_created = ""
                    time_elem = sys_elem.find("e:TimeCreated", ns)
                    if time_elem is not None:
                        time_created = time_elem.get("SystemTime", "")

                    level = ""
                    lvl_elem = sys_elem.find("e:Level", ns)
                    if lvl_elem is not None:
                        level = lvl_elem.text or ""

                    # Extract message data
                    data_items = []
                    ev_data = ev.find("e:EventData", ns)
                    if ev_data is not None:
                        for d in ev_data.findall("e:Data", ns):
                            if d.text:
                                data_items.append(d.text.strip())
                    msg_text = " ".join(data_items)
                    is_trunc = False
                    if len(msg_text) > MAX_EVENT_TEXT_LEN:
                        msg_text = msg_text[:MAX_EVENT_TEXT_LEN] + "..."
                        is_trunc = True

                    obj_key = f"event:{ch}:{provider}:{event_id}:{rec_id}"
                    records.append(
                        SnapshotRecord(
                            record_id=f"{run_id}-evt-{len(records)}",
                            run_id=run_id,
                            category="event_log",
                            object_key=obj_key,
                            data={
                                "channel": ch,
                                "provider": provider,
                                "event_id": event_id,
                                "record_id": rec_id,
                                "level": level,
                                "time_created": time_created,
                                "message": msg_text,
                            },
                            timestamp=time_created or "now",
                            source_id=self.source_id,
                            is_redacted=False,
                            is_truncated=is_trunc,
                        )
                    )
            except Exception as err:
                gaps.append(
                    CoverageGap(
                        gap_id=f"gap-{self.source_id}-{ch}",
                        run_id=run_id,
                        source_id=self.source_id,
                        category=self.category,
                        reason=f"Exception reading channel '{ch}': {err}",
                        impact=f"Channel '{ch}' omitted from snapshot.",
                    )
                )

        status = SourceStatus.SUCCESS
        if gaps and not records:
            status = SourceStatus.UNAVAILABLE
        elif gaps:
            status = SourceStatus.PARTIAL

        return CollectionResult(
            source_id=self.source_id,
            category=self.category,
            status=status,
            records=records,
            coverage_gaps=gaps,
            notes=f"Collected {len(records)} events from {list(self.channels)}.",
        )
