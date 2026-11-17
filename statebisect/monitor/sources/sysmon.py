"""Sysmon detection and read-only telemetry monitoring source."""

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

SYSMON_CHANNEL = "Microsoft-Windows-Sysmon/Operational"


class SysmonMonitorSource(BaseMonitorSource):
    """Monitors Microsoft Sysinternals Sysmon events if pre-installed on the host.

    Safety Boundary:
        This source NEVER installs, updates, configures, or creates Sysmon files.
        It strictly reads existing events from the operational channel if available.
    """

    def __init__(self) -> None:
        super().__init__(
            source_id="sysmon",
            category="telemetry",
            description="Read-only query of pre-installed Sysinternals Sysmon operational channel",
            version="1.0.0",
            permission_requirement="Standard User (Read-Only)",
        )
        self.high_water_mark: str = ""
        self.seen_event_ids: set[str] = set()
        self.dropped_events: int = 0

    def is_supported(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "Requires Microsoft Windows host environment"

        # Check if Sysmon channel exists and is readable
        cmd = ["wevtutil.exe", "gl", SYSMON_CHANNEL]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3.0,
                shell=False,
                check=False,
            )
            if res.returncode == 0 and "enabled: true" in res.stdout.lower():
                return True, "Sysmon operational channel is active and readable"
            elif res.returncode == 0:
                return False, "Sysmon channel exists but is disabled"
            else:
                return False, "Sysmon is not installed on this host"
        except Exception as err:
            return False, f"Error inspecting Sysmon availability: {err}"

    def start(self, session_id: str, start_time_utc: str) -> list[MonitoringCoverageGap]:
        self.session_id = session_id
        self.start_time_utc = start_time_utc
        self.high_water_mark = start_time_utc
        self.seen_event_ids.clear()
        self.dropped_events = 0

        supported, reason = self.is_supported()
        if not supported:
            return [
                MonitoringCoverageGap(
                    gap_id=f"gap-{self.source_id}-not-installed",
                    session_id=session_id,
                    source_id=self.source_id,
                    category=self.category,
                    reason=f"Sysmon source unavailable: {reason}",
                    impact="Process-creation and network telemetry from Sysmon cannot be captured.",
                )
            ]
        return []

    def poll(self) -> list[MonitoringEvent]:
        supported, _ = self.is_supported()
        if not supported:
            return []

        xpath_query = f"*[System[TimeCreated[@SystemTime>='{self.high_water_mark}']]]"
        cmd = [
            "wevtutil.exe",
            "qe",
            SYSMON_CHANNEL,
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
            if proc.returncode != 0 or not proc.stdout.strip():
                return []

            wrapped_xml = f"<Events>{proc.stdout.strip()}</Events>"
            root = ET.fromstring(wrapped_xml)
            results: list[MonitoringEvent] = []
            ns = {"ns": "http://schemas.microsoft.com/win/2004/08/events/event"}

            for elem in root.findall("ns:Event", ns):
                ev = self._parse_sysmon_event(elem, ns)
                if ev and ev.raw_ref not in self.seen_event_ids:
                    self.seen_event_ids.add(ev.raw_ref)
                    results.append(ev)
                    if ev.source_timestamp > self.high_water_mark:
                        self.high_water_mark = ev.source_timestamp
            return results

        except subprocess.TimeoutExpired:
            self.dropped_events += 1
            return []
        except Exception:
            return []

    def _parse_sysmon_event(self, elem: ET.Element, ns: dict[str, str]) -> MonitoringEvent | None:
        sys_node = elem.find("ns:System", ns)
        if sys_node is None:
            return None

        event_id_elem = sys_node.find("ns:EventID", ns)
        event_code = event_id_elem.text.strip() if (event_id_elem is not None and event_id_elem.text) else "0"

        time_elem = sys_node.find("ns:TimeCreated", ns)
        source_ts = time_elem.attrib.get("SystemTime", "") if time_elem is not None else ""

        record_id_elem = sys_node.find("ns:EventRecordID", ns)
        record_id = record_id_elem.text.strip() if (record_id_elem is not None and record_id_elem.text) else ""

        # Sysmon records rich fields in EventData
        payload: dict[str, Any] = {"channel": SYSMON_CHANNEL, "event_record_id": record_id}
        process_id: int | None = None
        process_name: str | None = None
        process_provenance = ProcessProvenance.UNAVAILABLE

        event_data_elem = elem.find("ns:EventData", ns)
        if event_data_elem is not None:
            for data_elem in event_data_elem.findall("ns:Data", ns):
                name = data_elem.attrib.get("Name", "")
                val = data_elem.text or ""
                clean_val, _ = redact_sensitive_text(val)
                bounded_val, _ = bound_evidence(clean_val, max_length=512)

                if name == "ProcessId" and bounded_val.isdigit():
                    process_id = int(bounded_val)
                    process_provenance = ProcessProvenance.EVENT_PAYLOAD
                elif name in ("Image", "ProcessName") and bounded_val:
                    process_name = bounded_val
                    process_provenance = ProcessProvenance.EVENT_PAYLOAD

                payload[name] = bounded_val

        now_utc = datetime.now(timezone.utc).isoformat()
        unique_sig = f"sysmon-{record_id}-{event_code}-{source_ts}"

        return MonitoringEvent(
            event_id=f"evt-{uuid.uuid4().hex[:10]}",
            session_id=self.session_id,
            source_id=self.source_id,
            source_version=self.version,
            category=self.category,
            timestamp_utc=now_utc,
            source_timestamp=source_ts or now_utc,
            channel_or_path=SYSMON_CHANNEL,
            event_code=f"Sysmon-{event_code}",
            process_id=process_id,
            process_name=process_name,
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
            "truncations": 0,
        }
