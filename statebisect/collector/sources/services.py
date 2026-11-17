"""Windows Services collection source via read-only registry enumeration."""

from __future__ import annotations

import sys
from typing import Any

from ..base import BaseCollectionSource
from ..models import CollectionResult, CoverageGap, SnapshotRecord, SourceStatus


START_TYPE_MAP = {
    0: "Boot",
    1: "System",
    2: "Automatic",
    3: "Manual",
    4: "Disabled",
}


class ServicesSource(BaseCollectionSource):
    """Collects installed Windows Services configuration via read-only registry keys."""

    def __init__(self) -> None:
        super().__init__(
            source_id="services",
            category="service",
            description="Windows service configuration, startup modes, and display names",
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
                reason="Non-Windows platform; Windows service registry is unavailable.",
                impact="Services inventory skipped.",
            )
            return CollectionResult(
                source_id=self.source_id,
                category=self.category,
                status=SourceStatus.UNAVAILABLE,
                coverage_gaps=[gap],
                notes="Skipped on non-Windows.",
            )

        import winreg

        records: list[SnapshotRecord] = []
        gaps: list[CoverageGap] = []
        truncated = False

        services_path = r"SYSTEM\CurrentControlSet\Services"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, services_path, 0, winreg.KEY_READ) as root_key:
                num_subkeys = winreg.QueryInfoKey(root_key)[0]
                for idx in range(num_subkeys):
                    if len(records) >= max_records:
                        truncated = True
                        break
                    try:
                        svc_name = winreg.EnumKey(root_key, idx)
                        with winreg.OpenKey(root_key, svc_name, 0, winreg.KEY_READ) as svc_key:
                            # Filter for Win32 service types (0x10 Win32_Own_Process, 0x20 Win32_Share_Process)
                            try:
                                svc_type = winreg.QueryValueEx(svc_key, "Type")[0]
                                if svc_type not in (16, 32, 272, 288):
                                    continue
                            except OSError:
                                continue

                            display_name = svc_name
                            try:
                                display_name = winreg.QueryValueEx(svc_key, "DisplayName")[0]
                            except OSError:
                                pass

                            start_val = None
                            try:
                                start_val = winreg.QueryValueEx(svc_key, "Start")[0]
                            except OSError:
                                pass
                            start_str = START_TYPE_MAP.get(start_val, f"Unknown ({start_val})")

                            records.append(
                                SnapshotRecord(
                                    record_id=f"{run_id}-svc-{len(records)}",
                                    run_id=run_id,
                                    category="service",
                                    object_key=f"service:{svc_name}",
                                    data={
                                        "name": svc_name,
                                        "display_name": str(display_name),
                                        "start_type": start_str,
                                    },
                                    timestamp="now",
                                    source_id=self.source_id,
                                    is_redacted=False,
                                    is_truncated=False,
                                )
                            )
                    except OSError:
                        continue
        except PermissionError as err:
            gaps.append(
                CoverageGap(
                    gap_id=f"gap-{self.source_id}",
                    run_id=run_id,
                    source_id=self.source_id,
                    category=self.category,
                    reason=f"Access denied reading {services_path}: {err}",
                    impact="Services could not be enumerated.",
                )
            )

        status = SourceStatus.SUCCESS if not gaps else SourceStatus.PARTIAL
        notes = f"Collected {len(records)} Win32 service records."
        if truncated:
            notes += f" (truncated at limit {max_records})"

        return CollectionResult(
            source_id=self.source_id,
            category=self.category,
            status=status,
            records=records,
            coverage_gaps=gaps,
            notes=notes,
        )
