"""Local HTTP Server for StateBisect Dashboard with strict loopback and security boundaries."""

from __future__ import annotations

import cgi
import http.server
import json
import logging
import mimetypes
import os
import socket
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from ..storage import Storage
from .handlers import DashboardApiRouter

logger = logging.getLogger("statebisect.dashboard")

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 1024 * 1024  # 1 MB maximum payload
ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


class DashboardRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler enforcing security headers, CSRF, and static/API routing."""

    server_version = "StateBisectDashboard/1.0"
    router: DashboardApiRouter

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard stderr request noise unless DEBUG is enabled."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _send_security_headers(self, content_type: str = "application/json; charset=utf-8", is_api: bool = True) -> None:
        """Set standard defensive HTTP response headers."""
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'",
        )
        if is_api:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")

    def _validate_host_and_origin(self) -> bool:
        """Enforce Host and Origin header validation to defend against DNS rebinding and cross-site attacks."""
        host = self.headers.get("Host", "")
        # Host can include port e.g. 127.0.0.1:8080
        host_name = host.split(":")[0].strip("[]")
        if not any(host_name == h or host_name.endswith(f".{h}") for h in ("127.0.0.1", "localhost", "::1")):
            self.send_response(400)
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(b'{"error":"Invalid Host header","code":"INVALID_HOST"}')
            return False

        # If Origin is present, verify loopback
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed_origin = urlparse(origin)
                orig_host = (parsed_origin.hostname or "").strip("[]")
                if orig_host not in ("127.0.0.1", "localhost", "::1"):
                    self.send_response(403)
                    self._send_security_headers()
                    self.end_headers()
                    self.wfile.write(b'{"error":"Untrusted Origin rejected","code":"CROSS_ORIGIN_FORBIDDEN"}')
                    return False
            except Exception:
                self.send_response(400)
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(b'{"error":"Malformed Origin header","code":"MALFORMED_ORIGIN"}')
                return False

        return True

    def _validate_csrf(self) -> bool:
        """Validate anti-CSRF custom request header for state-changing requests."""
        if self.command in ("POST", "PUT", "DELETE", "PATCH"):
            # Require custom header which cannot be set by standard cross-origin form submissions
            custom_header = self.headers.get("X-StateBisect-Request") or self.headers.get("X-Requested-With")
            if not custom_header:
                self.send_response(403)
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(
                    b'{"error":"Missing anti-CSRF header (X-StateBisect-Request or X-Requested-With required)","code":"CSRF_REJECTED"}'
                )
                return False
        return True

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests restricted to loopback."""
        if not self._validate_host_and_origin():
            return
        origin = self.headers.get("Origin", "")
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-StateBisect-Request, X-Requested-With")
        self.send_header("Access-Control-Max-Age", "86400")
        self._send_security_headers(is_api=True)
        self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests for static dashboard assets or API endpoints."""
        if not self._validate_host_and_origin():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query_params = parse_qs(parsed.query)

        # 1. API Route
        if path.startswith("/api/"):
            status_code, data, headers = self.router.dispatch("GET", path, query_params)
            self.send_response(status_code)
            for k, v in headers.items():
                self.send_header(k, v)
            self._send_security_headers(content_type=headers.get("Content-Type", "application/json"), is_api=True)
            self.end_headers()

            if isinstance(data, str):
                self.wfile.write(data.encode("utf-8"))
            else:
                self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))
            return

        # 2. Static Web Asset Route
        self._serve_static(path)

    def do_POST(self) -> None:
        """Handle POST requests for API actions."""
        if not self._validate_host_and_origin() or not self._validate_csrf():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query_params = parse_qs(parsed.query)

        # Read request body safely with size bounds
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_BODY_BYTES:
            self.send_response(413)
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(b'{"error":"Payload Too Large (max 1MB)","code":"PAYLOAD_TOO_LARGE"}')
            return

        body_data: Optional[dict[str, Any]] = None
        if content_length > 0:
            raw_body = self.rfile.read(content_length)
            try:
                body_data = json.loads(raw_body.decode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(f'{{"error":"Malformed JSON: {str(e)}","code":"BAD_JSON"}}'.encode("utf-8"))
                return

        status_code, data, headers = self.router.dispatch("POST", path, query_params, body_data)
        self.send_response(status_code)
        for k, v in headers.items():
            self.send_header(k, v)
        self._send_security_headers(content_type=headers.get("Content-Type", "application/json"), is_api=True)
        self.end_headers()

        if isinstance(data, str):
            self.wfile.write(data.encode("utf-8"))
        else:
            self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def _serve_static(self, req_path: str) -> None:
        """Safely serve static files from STATIC_DIR, preventing path traversal."""
        if req_path in ("", "/"):
            req_path = "/index.html"

        # Sanitize and resolve target path
        clean_rel = req_path.lstrip("/").replace("\\", "/")
        target_path = (STATIC_DIR / clean_rel).resolve()

        # Path traversal guard: must be strictly inside STATIC_DIR
        try:
            target_path.relative_to(STATIC_DIR)
        except ValueError:
            self.send_response(403)
            self._send_security_headers(content_type="text/plain; charset=utf-8", is_api=False)
            self.end_headers()
            self.wfile.write(b"403 Forbidden: Path traversal attempt")
            return

        if not target_path.is_file():
            # If not found, fallback to index.html only for legitimate SPA root routes
            clean_route = req_path.strip("/")
            if clean_route in ("", "overview", "investigations", "environments", "timeline"):
                target_path = STATIC_DIR / "index.html"
            else:
                self.send_response(404)
                self._send_security_headers(content_type="text/plain; charset=utf-8", is_api=False)
                self.end_headers()
                self.wfile.write(b"404 Not Found")
                return

        mime_type, _ = mimetypes.guess_type(str(target_path))
        if not mime_type:
            mime_type = "application/octet-stream"
        if mime_type.startswith("text/") or mime_type in ("application/javascript", "application/json"):
            mime_type += "; charset=utf-8"

        try:
            with open(target_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self._send_security_headers(content_type=mime_type, is_api=False)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_response(500)
            self._send_security_headers(content_type="text/plain; charset=utf-8", is_api=False)
            self.end_headers()
            self.wfile.write(f"500 Internal error reading asset: {str(e)}".encode("utf-8"))


class DashboardServer:
    """Manages the lifecycle of the StateBisect dashboard HTTP server."""

    def __init__(self, storage: Storage, host: str = "127.0.0.1", port: int = 8080) -> None:
        # Enforce loopback binding strictly
        clean_host = host.strip("[]").lower()
        if clean_host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"Security violation: StateBisect dashboard cannot bind to remote host '{host}'. "
                "Dashboard must bind exclusively to loopback ('127.0.0.1', 'localhost', or '::1')."
            )

        self.storage = storage
        self.host = host
        self.port = port
        self.router = DashboardApiRouter(storage)
        self._server: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[Any] = None

    def start(self) -> None:
        """Initialize, bind, and start serving requests in a daemon thread."""
        if self._server:
            return

        handler_class = type(
            "BoundDashboardRequestHandler",
            (DashboardRequestHandler,),
            {"router": self.router},
        )

        # ThreadingHTTPServer handles concurrent requests from browsers without deadlock
        server_address = (self.host, self.port)
        self._server = http.server.ThreadingHTTPServer(server_address, handler_class)
        self._server.daemon_threads = True
        # Resolve actual port if 0 was passed
        self.port = self._server.server_address[1]
        logger.info("StateBisect Dashboard running on http://%s:%d (Loopback only)", self.host, self.port)

        import threading
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        """Get the loopback URL for the dashboard."""
        return f"http://{self.host}:{self.port}"

    def serve_forever(self) -> None:
        """Block and serve requests until stopped."""
        if not self._server:
            self.start()
        try:
            if self._thread and self._thread.is_alive():
                self._thread.join()
        finally:
            # Mandatory Exception-Handling Rule: never return, break, or continue in finally!
            self.stop()

    def stop(self) -> None:
        """Gracefully shut down and release server sockets."""
        if self._server:
            try:
                self._server.shutdown()
            except Exception as e:
                logger.warning("Error during server shutdown: %s", e)
            try:
                self._server.server_close()
            except Exception as e:
                logger.warning("Error closing server socket: %s", e)
            self._server = None
        if self._thread and getattr(self._thread, "is_alive", lambda: False)():
            self._thread.join(timeout=2.0)
            self._thread = None
