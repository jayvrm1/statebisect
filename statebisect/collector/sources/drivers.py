"""Driver inventory collection source via read-only registry enumeration."""

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


class DriversSource(BaseCollectionSource):
    """Collects installed kernel and filesystem drivers via read-only registry."""

    def __init__(self) -> None:
        super().__init__(
            source_id="drivers",
            category="driver",
            description="Installed Windows kernel and filesystem driver service configuration",
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
                reason="Non-Windows platform; driver service registry is unavailable.",
                impact="Driver inventory skipped.",
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
                        drv_name = winreg.EnumKey(root_key, idx)
                        with winreg.OpenKey(root_key, drv_name, 0, winreg.KEY_READ) as drv_key:
                            # Filter for Driver types (0x1 Kernel Driver, 0x2 File System Driver)
                            try:
                                drv_type = winreg.QueryValueEx(drv_key, "Type")[0]
                                if drv_type not in (1, 2):
                                    continue
                            except OSError:
                                continue

                            display_name = drv_name
                            try:
                                display_name = winreg.QueryValueEx(drv_key, "DisplayName")[0]
                            except OSError:
                                pass

                            start_val = None
                            try:
                                start_val = winreg.QueryValueEx(drv_key, "Start")[0]
                            except OSError:
                                pass
                            start_str = START_TYPE_MAP.get(start_val, f"Unknown ({start_val})")

                            records.append(
                                SnapshotRecord(
                                    record_id=f"{run_id}-drv-{len(records)}",
                                    run_id=run_id,
                                    category="driver",
                                    object_key=f"driver:{drv_name}",
                                    data={
                                        "name": drv_name,
                                        "display_name": str(display_name),
                                        "start_type": start_str,
                                        "driver_type": "Kernel" if drv_type == 1 else "FileSystem",
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
                    impact="Drivers could not be enumerated.",
                )
            )

        status = SourceStatus.SUCCESS if not gaps else SourceStatus.PARTIAL
        notes = f"Collected {len(records)} driver records."
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
