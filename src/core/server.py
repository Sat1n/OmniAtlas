"""Zero-dependency web server hosting the topology dashboard.

Built entirely on the Python standard library (``http.server``) to honor
the lightweight design principle: no Flask / FastAPI / ASGI machinery.

Responsibilities:

* Serve the single-file frontend (``src/ui/index.html``) with a strict
  path-traversal guard.
* Expose ``GET /api/topology`` — the graph is rebuilt on every request,
  so the dashboard always reflects the latest repository state.
* Detect headless / remote environments (SSH, Docker, missing display
  server) so the CLI can skip the automatic browser launch and print an
  SSH port-forwarding hint instead.
"""

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.graph import TopologyGraphBuilder

#: Frontend assets live next to the ``core`` package (editable checkout
#: and installed wheel share this relative layout).
UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def is_headless_environment() -> bool:
    """Return True when no local browser can sensibly be opened.

    Heuristics: active SSH session (``SSH_CLIENT`` / ``SSH_TTY`` /
    ``SSH_CONNECTION``), running inside a Docker container, or Linux
    without a display server (``DISPLAY`` / ``WAYLAND_DISPLAY`` unset).

    @shape return: bool
    @source env: process-environment#var:SSH_CLIENT|SSH_TTY|DISPLAY
    """
    if any(key in os.environ for key in ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION")):
        return True
    if Path("/.dockerenv").exists():
        return True
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return True
    return False


class AtlasWebServer:
    """Threading HTTP server binding the dashboard and topology API."""

    def __init__(
        self, repo_root: str | Path = ".", host: str = "127.0.0.1", port: int = 8080
    ) -> None:
        self._root = Path(repo_root)
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        """Browser-friendly URL (0.0.0.0 binds every interface but is not
        itself a dialable address).

        @shape return: str
        """
        dialable = "127.0.0.1" if self._host == "0.0.0.0" else self._host
        return f"http://{dialable}:{self._port}"

    def serve_forever(self) -> None:
        """Bind, listen and block until interrupted.

        Raises:
            OSError: when the port is already taken or the bind fails.

        @source topology: src/core/graph.py#class:TopologyGraphBuilder
        """
        handler = _build_handler(self._root)
        self._httpd = ThreadingHTTPServer((self._host, self._port), handler)
        try:
            self._httpd.serve_forever()
        finally:
            self._httpd.server_close()

    def shutdown(self) -> None:
        """Stop the request loop (safe to call from another thread)."""
        if self._httpd is not None:
            self._httpd.shutdown()


def _build_handler(repo_root: Path) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to a repository root."""

    class _AtlasRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            if route == "/api/topology":
                self._send_topology()
                return
            if route in ("/", "/index.html"):
                self._send_file(UI_DIR / "index.html")
                return
            self._send_static(route)

        def _send_topology(self) -> None:
            try:
                payload = TopologyGraphBuilder(repo_root).build().to_json()
            except Exception as exc:  # defensive: never crash the server
                self._send_json_error(500, f"topology build failed: {exc}")
                return
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, route: str) -> None:
            target = (UI_DIR / route.lstrip("/")).resolve()
            ui_root = UI_DIR.resolve()
            if not target.is_file() or not target.is_relative_to(ui_root):
                self._send_json_error(404, f"not found: {route}")
                return
            self._send_file(target)

        def _send_file(self, path: Path) -> None:
            try:
                data = path.read_bytes()
            except OSError:
                self._send_json_error(404, f"not found: {path.name}")
                return
            content_type = mimetypes.guess_type(path.name)[0] or (
                "application/octet-stream"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json_error(self, code: int, message: str) -> None:
            body = json.dumps({"error": message}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            """Silence per-request stderr noise; the CLI panel reports state."""

    return _AtlasRequestHandler
