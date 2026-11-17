"""Built-in read-only collection sources."""

from .drivers import DriversSource
from .event_logs import EventLogsSource
from .installed_software import InstalledSoftwareSource
from .network import NetworkSource
from .os_info import OsInfoSource
from .services import ServicesSource
from .windows_updates import WindowsUpdatesSource

__all__ = [
    "DriversSource",
    "EventLogsSource",
    "InstalledSoftwareSource",
    "NetworkSource",
    "OsInfoSource",
    "ServicesSource",
    "WindowsUpdatesSource",
]
