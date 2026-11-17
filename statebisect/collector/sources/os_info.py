"""Operating system and machine inventory collection source."""

from __future__ import annotations

import hashlib
import platform
import socket
import sys
from typing import Any

from ..base import BaseCollectionSource
from ..models import CollectionResult, SnapshotRecord, SourceStatus


class OsInfoSource(BaseCollectionSource):
    """Collects operating system, architecture, and anonymized machine correlation identity."""

    def __init__(self) -> None:
        super().__init__(
            source_id="os_info",
            category="os",
            description="Operating system release, build, architecture, and host identity",
            version="1.0.0",
            permission_requirement="Standard User",
        )

    def _collect_internal(self, run_id: str, lookback_sec: int, max_records: int) -> CollectionResult:
        records: list[SnapshotRecord] = []
        now_iso = platform.node()

        product_name = platform.system()
        build = platform.version()
        release = platform.release()
        arch = platform.machine()

        # Read Windows NT current version registry if on Windows
        if sys.platform == "win32":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                    0,
                    winreg.KEY_READ,
                ) as k:
                    try:
                        product_name = winreg.QueryValueEx(k, "ProductName")[0]
                    except OSError:
                        pass
                    try:
                        build = winreg.QueryValueEx(k, "CurrentBuild")[0]
                    except OSError:
                        pass
                    try:
                        display_ver = winreg.QueryValueEx(k, "DisplayVersion")[0]
                        release = f"{release} ({display_ver})"
                    except OSError:
                        pass
            except Exception:
                pass

        # Generated privacy-preserving machine ID (never hardware serial)
        machine_hash = hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16]

        data = {
            "product_name": product_name,
            "build": build,
            "release": release,
            "architecture": arch,
            "machine_id": f"machine-{machine_hash}",
            "python_version": platform.python_version(),
        }

        records.append(
            SnapshotRecord(
                record_id=f"{run_id}-os",
                run_id=run_id,
                category="os",
                object_key="system:os",
                data=data,
                timestamp=now_iso,
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
            notes="Collected OS environment metadata.",
        )
