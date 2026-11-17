"""Windows Collector package for read-only snapshot collection and diffing."""

from .base import BaseCollectionSource
from .differ import compare_snapshots
from .engine import COLLECTOR_VERSION, PlatformNotSupportedError, WindowsCollector
from .models import (
    CollectionResult,
    CollectionRun,
    CoverageGap,
    DiffChangeType,
    SnapshotDiff,
    SnapshotRecord,
    SourceStatus,
)
from .sources import (
    DriversSource,
    EventLogsSource,
    InstalledSoftwareSource,
    NetworkSource,
    OsInfoSource,
    ServicesSource,
    WindowsUpdatesSource,
)

__all__ = [
    "BaseCollectionSource",
    "COLLECTOR_VERSION",
    "CollectionResult",
    "CollectionRun",
    "CoverageGap",
    "DiffChangeType",
    "DriversSource",
    "EventLogsSource",
    "InstalledSoftwareSource",
    "NetworkSource",
    "OsInfoSource",
    "PlatformNotSupportedError",
    "ServicesSource",
    "SnapshotDiff",
    "SnapshotRecord",
    "SourceStatus",
    "WindowsCollector",
    "WindowsUpdatesSource",
    "compare_snapshots",
]
