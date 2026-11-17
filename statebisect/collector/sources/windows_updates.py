"""Windows Update / Hotfix collection source via CIM."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..base import BaseCollectionSource
from ..models import CollectionResult, CoverageGap, SnapshotRecord, SourceStatus


class WindowsUpdatesSource(BaseCollectionSource):
    """Collects installed Windows Updates and Hotfixes using CIM Win32_QuickFixEngineering."""

    def __init__(self) -> None:
        super().__init__(
            source_id="windows_updates",
            category="update",
            description="Installed Windows updates and hotfixes",
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
                reason="Non-Windows platform; Win32_QuickFixEngineering is unavailable.",
                impact="Windows update inventory skipped.",
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
            "Get-CimInstance -ClassName Win32_QuickFixEngineering | "
            "Select-Object -Property HotFixID, Description, InstalledOn | "
            "ConvertTo-Json -Compress",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30.0,
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
                    notes="No hotfixes returned by CIM query.",
                )

            parsed = json.loads(raw_out)
            items = parsed if isinstance(parsed, list) else [parsed]
        except Exception as err:
            gap = CoverageGap(
                gap_id=f"gap-{self.source_id}",
                run_id=run_id,
                source_id=self.source_id,
                category=self.category,
                reason=f"Failed querying Win32_QuickFixEngineering: {err}",
                impact="Windows updates could not be enumerated.",
            )
            return CollectionResult(
                source_id=self.source_id,
                category=self.category,
                status=SourceStatus.PARTIAL,
                coverage_gaps=[gap],
                notes=f"Error executing CIM query: {err}",
            )

        records: list[SnapshotRecord] = []
        for idx, item in enumerate(items[:max_records]):
            hotfix_id = str(item.get("HotFixID") or f"KB-unknown-{idx}")
            desc = str(item.get("Description") or "")
            installed_on_raw = item.get("InstalledOn")
            installed_date = ""
            if isinstance(installed_on_raw, dict):
                installed_date = str(installed_on_raw.get("DateTime") or "")
            else:
                installed_date = str(installed_on_raw or "")

            records.append(
                SnapshotRecord(
                    record_id=f"{run_id}-upd-{idx}",
                    run_id=run_id,
                    category="update",
                    object_key=f"update:{hotfix_id}",
                    data={
                        "hotfix_id": hotfix_id,
                        "description": desc,
                        "installed_on": installed_date,
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
            notes=f"Collected {len(records)} installed Windows update records.",
        )
