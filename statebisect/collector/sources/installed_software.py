"""Installed software collection source via read-only Windows Registry enumeration."""

from __future__ import annotations

import sys
from typing import Any

from ..base import BaseCollectionSource
from ..models import CollectionResult, CoverageGap, SnapshotRecord, SourceStatus


class InstalledSoftwareSource(BaseCollectionSource):
    """Collects installed applications from standard Windows Uninstall registry hives."""

    def __init__(self) -> None:
        super().__init__(
            source_id="installed_software",
            category="software",
            description="Installed software inventory from standard Windows registry hives",
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
                reason="Non-Windows platform; Windows registry uninstall hives are unavailable.",
                impact="Installed software inventory skipped.",
            )
            return CollectionResult(
                source_id=self.source_id,
                category=self.category,
                status=SourceStatus.UNAVAILABLE,
                coverage_gaps=[gap],
                notes="Skipped on non-Windows.",
            )

        import winreg

        targets = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
        ]

        seen_names: set[str] = set()
        records: list[SnapshotRecord] = []
        gaps: list[CoverageGap] = []
        truncated = False

        for hive, subkey_path, access_flag in targets:
            try:
                with winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_READ | access_flag) as root_key:
                    num_subkeys = winreg.QueryInfoKey(root_key)[0]
                    for idx in range(num_subkeys):
                        if len(records) >= max_records:
                            truncated = True
                            break
                        try:
                            sub_name = winreg.EnumKey(root_key, idx)
                            with winreg.OpenKey(root_key, sub_name, 0, winreg.KEY_READ | access_flag) as app_key:
                                try:
                                    display_name = winreg.QueryValueEx(app_key, "DisplayName")[0]
                                except OSError:
                                    continue  # Skip entries with no DisplayName

                                if not display_name or display_name in seen_names:
                                    continue
                                seen_names.add(display_name)

                                display_ver = ""
                                try:
                                    display_ver = winreg.QueryValueEx(app_key, "DisplayVersion")[0]
                                except OSError:
                                    pass

                                publisher = ""
                                try:
                                    publisher = winreg.QueryValueEx(app_key, "Publisher")[0]
                                except OSError:
                                    pass

                                install_date = ""
                                try:
                                    install_date = winreg.QueryValueEx(app_key, "InstallDate")[0]
                                except OSError:
                                    pass

                                records.append(
                                    SnapshotRecord(
                                        record_id=f"{run_id}-soft-{len(records)}",
                                        run_id=run_id,
                                        category="software",
                                        object_key=f"software:{display_name}",
                                        data={
                                            "name": display_name,
                                            "version": str(display_ver),
                                            "publisher": str(publisher),
                                            "install_date": str(install_date),
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
                        gap_id=f"gap-{self.source_id}-{subkey_path}",
                        run_id=run_id,
                        source_id=self.source_id,
                        category=self.category,
                        reason=f"Access denied reading {subkey_path}: {err}",
                        impact="Some installed applications in this hive could not be enumerated.",
                    )
                )
            except OSError:
                continue

        status = SourceStatus.SUCCESS
        if gaps:
            status = SourceStatus.PARTIAL
        if truncated:
            notes = f"Enumerated {len(records)} software packages (truncated at limit {max_records})."
        else:
            notes = f"Enumerated {len(records)} software packages."

        return CollectionResult(
            source_id=self.source_id,
            category=self.category,
            status=status,
            records=records,
            coverage_gaps=gaps,
            notes=notes,
        )
