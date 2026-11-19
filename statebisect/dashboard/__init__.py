"""StateBisect Local Dashboard and API package."""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from typing import Optional

from ..storage import Storage
from .handlers import DashboardApiRouter
from .server import DashboardServer

__all__ = [
    "DashboardApiRouter",
    "DashboardServer",
    "run_dashboard",
]

logger = logging.getLogger("statebisect.dashboard")


def run_dashboard(
    storage: Storage,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
) -> None:
    """Run StateBisect local dashboard with optional automatic browser launch."""
    server = DashboardServer(storage=storage, host=host, port=port)
    server.start()
    dashboard_url = server.url

    print(f"\n================================================================================")
    print(f"                      STATEBISECT LOCAL DASHBOARD                              ")
    print(f"================================================================================")
    print(f"  URL              : {dashboard_url}")
    print(f"  Loopback Binding : {host} (Strictly Non-Host Accessible)")
    print(f"  Security Model   : Anti-CSRF Header, Host-Validation, Loopback-Only")
    print(f"  Storage Source   : {storage.db_path}")
    print(f"--------------------------------------------------------------------------------")
    print(f"  Press Ctrl+C to stop the dashboard server.")
    print(f"================================================================================\n")

    if open_browser:
        def _open() -> None:
            time.sleep(0.5)
            try:
                webbrowser.open(dashboard_url)
            except Exception as e:
                logger.warning("Could not automatically launch browser: %s", e)

        t = threading.Thread(target=_open, daemon=True)
        t.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping StateBisect Dashboard server...")
    finally:
        # Mandatory Exception-Handling Rule: never return, break, or continue in finally!
        server.stop()
        print("StateBisect Dashboard stopped cleanly.")
