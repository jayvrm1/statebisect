"""Event Log continuous monitoring source via time-bounded XML polling."""

from __future__ import annotations

import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from statebisect.health_checks.base import bound_evidence, redact_sensitive_text

from ..base import BaseMonitorSource
from ..models import (
    EventConfidence,
    MonitoringCoverageGap,
    MonitoringEvent,
    ProcessProvenance,
)


class EventLogMonitorSource(BaseMonitorSource):
    """Monitors Windows Event Log channels (Application, System) for new events during a session."""

    def __init__(self, channels: list[str] | None = None) -> None:
        super().__init__(
            source_id="event_log",
            category="event_log",
            description="Time-bounded monitoring of Windows Application and System event channels",
            version="1.0.0",
            permission_requirement="Standard User",
        )
        self.channels = channels if channels is not None else ["Application", "System"]
        self.high_water_marks: dict[str, str] = {}
        self.seen_event_ids: set[str] = set()
        self.dropped_events: int = 0
        self.truncations: int = 0

    def is_supported(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "Requires Microsoft Windows host environment"
        return True, "Available via standard user wevtutil read access"

    def start(self, session_id: str, start_time_utc: str) -> list[MonitoringCoverageGap]:
        self.session_id = session_id
        self.start_time_utc = start_time_utc
        self.seen_event_ids.clear()
        self.dropped_events = 0
        self.truncations = 0

        gaps: list[MonitoringCoverageGap] = []
        # Initialize high-water mark for each channel to session start time
        for ch in self.channels:
            self.high_water_marks[ch] = start_time_utc

        if "Security" in self.channels:
            gaps.append(
                MonitoringCoverageGap(
                    gap_id=f"gap-{self.source_id}-security",
                    session_id=session_id,
                    source_id=self.source_id,
                    category=self.category,
                    reason="Security log requires elevated SeSecurityPrivilege; omitted from standard monitoring.",
                    impact="Security audit events are not captured in this session.",
                )
            )
        return gaps

    def poll(self) -> list[MonitoringEvent]:
        if sys.platform != "win32":
            return []

        events: list[MonitoringEvent] = []
        for ch in self.channels:
            if ch == "Security":
                continue
            ch_events = self._query_channel(ch)
            events.extend(ch_events)
        return events

    def _query_channel(self, channel: str) -> list[MonitoringEvent]:
        since_time = self.high_water_marks.get(channel, self.start_time_utc)
        xpath_query = f"*[System[TimeCreated[@SystemTime>='{since_time}']]]"

        cmd = [
            "wevtutil.exe",
            "qe",
            channel,
            f"/q:{xpath_query}",
            "/c:50",
            "/rd:false",
            "/f:xml",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5.0,
                shell=False,
                check=False,
            )
            if proc.returncode != 0:
                return []

            raw_xml = proc.stdout.strip()
            if not raw_xml:
                return []

            wrapped_xml = f"<Events>{raw_xml}</Events>"
            try:
                root = ET.fromstring(wrapped_xml)
            except ET.ParseError:
                return []

            results: list[MonitoringEvent] = []
            ns = {"ns": "http://schemas.microsoft.com/win/2004/08/events/event"}

            for elem in root.findall("ns:Event", ns):
                parsed = self._parse_event_element(channel, elem, ns)
                if parsed and parsed.raw_ref not in self.seen_event_ids:
                    self.seen_event_ids.add(parsed.raw_ref)
                    results.append(parsed)
                    # Update high-water mark if newer
                    if parsed.source_timestamp > self.high_water_marks.get(channel, ""):
                        self.high_water_marks[channel] = parsed.source_timestamp

            return results

        except subprocess.TimeoutExpired:
            self.dropped_events += 1
            return []
        except Exception:
            return []

    def _parse_event_element(
        self,
        channel: str,
        elem: ET.Element,
        ns: dict[str, str],
    ) -> MonitoringEvent | None:
        sys_node = elem.find("ns:System", ns)
        if sys_node is None:
            return None

        event_id_elem = sys_node.find("ns:EventID", ns)
        event_code = event_id_elem.text.strip() if (event_id_elem is not None and event_id_elem.text) else "0"

        time_elem = sys_node.find("ns:TimeCreated", ns)
        source_ts = time_elem.attrib.get("SystemTime", "") if time_elem is not None else ""

        provider_elem = sys_node.find("ns:Provider", ns)
        provider_name = provider_elem.attrib.get("Name", "Unknown") if provider_elem is not None else "Unknown"

        record_id_elem = sys_node.find("ns:EventRecordID", ns)
        record_id = record_id_elem.text.strip() if (record_id_elem is not None and record_id_elem.text) else ""

        # Check execution process info
        exec_elem = sys_node.find("ns:Execution", ns)
        process_id: int | None = None
        process_provenance = ProcessProvenance.UNAVAILABLE
        if exec_elem is not None and "ProcessID" in exec_elem.attrib:
            try:
                process_id = int(exec_elem.attrib["ProcessID"])
                process_provenance = ProcessProvenance.EVENT_PAYLOAD
            except (ValueError, TypeError):
                process_id = None

        # Extract EventData or UserData
        payload: dict[str, Any] = {
            "channel": channel,
            "provider": provider_name,
            "event_record_id": record_id,
        }

        event_data_elem = elem.find("ns:EventData", ns)
        if event_data_elem is not None:
            data_items = []
            for data_elem in event_data_elem.findall("ns:Data", ns):
                name = data_elem.attrib.get("Name", "")
                val = data_elem.text or ""
                # Redact sensitive text
                cleaned_val, was_redacted = redact_sensitive_text(val)
                bounded_val, was_truncated = bound_evidence(cleaned_val, max_length=512)
                if was_truncated:
                    self.truncations += 1
                if name:
                    payload[name] = bounded_val
                else:
                    data_items.append(bounded_val)
            if data_items:
                payload["data_items"] = data_items[:10]

        now_utc = datetime.now(timezone.utc).isoformat()
        unique_sig = f"{channel}-{record_id}-{event_code}-{source_ts}"

        return MonitoringEvent(
            event_id=f"evt-{uuid.uuid4().hex[:10]}",
            session_id=self.session_id,
            source_id=self.source_id,
            source_version=self.version,
            category=self.category,
            timestamp_utc=now_utc,
            source_timestamp=source_ts or now_utc,
            channel_or_path=channel,
            event_code=event_code,
            process_id=process_id,
            process_name=None,  # Not present in standard System header
            process_provenance=process_provenance,
            payload=payload,
            is_redacted=False,
            is_truncated=False,
            confidence=EventConfidence.CONFIRMED,
            raw_ref=unique_sig,
        )

    def stop(self) -> list[MonitoringCoverageGap]:
        return []

    def get_loss_indicators(self) -> dict[str, Any]:
        return {
            "events_dropped": self.dropped_events,
            "buffer_overflows": 0,
            "truncations": self.truncations,
        }
