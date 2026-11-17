"""Selected network adapter configuration collection source."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..base import BaseCollectionSource
from ..models import CollectionResult, CoverageGap, SnapshotRecord, SourceStatus


class NetworkSource(BaseCollectionSource):
    """Collects connected network adapters and interface status without collecting traffic or credentials."""

    def __init__(self) -> None:
        super().__init__(
            source_id="network",
            category="network",
            description="Active network adapters and connection status",
            version="1.0.0",
            permission_requirement="Standard User",
        )

    def _collect_internal(self, run_id: str, lookback_sec: int, max_records: int) -> CollectionResult:
        if sys.platform != "win32":
            gap = CoverageGap(
                gap_id=f"gap-{self.source_id}-nonwin",
                run_id=run_id,
                source_id=self.source_id,
                category=self.category,
                reason="Non-Windows platform; Win32_NetworkAdapter is unavailable.",
                impact="Network adapter snapshot skipped.",
            )
            return CollectionResult(
                source_id=self.source_id,
                category=self.category,
                status=SourceStatus.UNAVAILABLE,
                coverage_gaps=[gap],
                notes="Skipped on non-Windows.",
            )

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance -ClassName Win32_NetworkAdapter -Filter 'NetConnectionStatus=2' | "
            "Select-Object -Property Name, AdapterType, NetConnectionID, Speed | "
            "ConvertTo-Json -Compress",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
                shell=False,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"CIM query failed with code {proc.returncode}: {proc.stderr}")

            raw_out = proc.stdout.strip()
            if not raw_out:
                return CollectionResult(
                    source_id=self.source_id,
                    category=self.category,
                    status=SourceStatus.SUCCESS,
                    records=[],
                    coverage_gaps=[],
                    notes="No active network adapters found.",
                )

            parsed = json.loads(raw_out)
            items = parsed if isinstance(parsed, list) else [parsed]
        except Exception as err:
            gap = CoverageGap(
                gap_id=f"gap-{self.source_id}",
                run_id=run_id,
                source_id=self.source_id,
                category=self.category,
                reason=f"Failed querying network adapters: {err}",
                impact="Network configuration could not be enumerated.",
            )
            return CollectionResult(
                source_id=self.source_id,
                category=self.category,
                status=SourceStatus.PARTIAL,
                coverage_gaps=[gap],
                notes=f"Error executing network query: {err}",
            )

        records: list[SnapshotRecord] = []
        for idx, item in enumerate(items[:max_records]):
            adapter_name = str(item.get("Name") or f"Adapter-{idx}")
            conn_id = str(item.get("NetConnectionID") or adapter_name)
            adapter_type = str(item.get("AdapterType") or "Unknown")

            records.append(
                SnapshotRecord(
                    record_id=f"{run_id}-net-{idx}",
                    run_id=run_id,
                    category="network",
                    object_key=f"network:{conn_id}",
                    data={
                        "name": adapter_name,
                        "connection_id": conn_id,
                        "adapter_type": adapter_type,
                    },
                    timestamp="now",
                    source_id=self.source_id,
                    is_redacted=False,
                    is_truncated=False,
                )
            )

        return CollectionResult(
            source_id=self.source_id,
            category=self.category,
            status=SourceStatus.SUCCESS,
            records=records,
            coverage_gaps=[],
            notes=f"Collected {len(records)} active network adapter records.",
        )
