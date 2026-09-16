"""Zero-dependency web server hosting the topology dashboard.

Built entirely on the Python standard library (``http.server``) to honor
the lightweight design principle: no Flask / FastAPI / ASGI machinery.

Responsibilities:

* Serve the single-file frontend (``src/ui/index.html``) with a strict
  path-traversal guard.
* Expose ``GET /api/topology`` — the graph is rebuilt on every request,
  so the dashboard always reflects the latest repository state.
* Expose ``GET /api/events`` — a Server-Sent Events stream pushing
  ``graph_update`` when tracked ``.py`` / ``.md`` files change on disk
  (mtime polling, no watchdog dependency).
* Expose ``GET /api/ides`` — best-effort detection of locally installed
  editors powering the dashboard's "Open in Editor" deep links.
* Detect headless / remote environments (SSH, Docker, missing display
  server) so the CLI can skip the automatic browser launch and print an
  SSH port-forwarding hint instead.
"""

import json
import mimetypes
import os
import shutil
import sys
import threading
import time
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.git_provider import GitProvider
from core.graph import TopologyGraphBuilder

#: Frontend assets live next to the ``core`` package (editable checkout
#: and installed wheel share this relative layout).
UI_DIR = Path(__file__).resolve().parent.parent / "ui"

#: Poll cadence for the file watcher (seconds) — keeps updates sub-second.
_WATCH_INTERVAL = 0.8


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


#: Editor launchers resolvable through ``PATH``.
_IDE_BINARIES = {
    "vscode": ("code", "codium"),
    "cursor": ("cursor",),
    "pycharm": ("pycharm", "pycharm-professional", "pycharm-community"),
}

#: Well-known install locations beyond ``PATH``.
_IDE_PATHS = {
    "vscode": (
        "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
        "/snap/bin/code",
        "/usr/local/bin/code",
    ),
    "cursor": (
        "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
        "/opt/Cursor/cursor",
        "/usr/local/bin/cursor",
    ),
    "pycharm": (
        "/Applications/PyCharm.app/Contents/MacOS/pycharm",
        "/opt/pycharm/bin/pycharm.sh",
        "/snap/bin/pycharm",
    ),
}

_IDE_DETECTION: list[str] | None = None


@lru_cache(maxsize=1)
def _windows_ide_paths() -> dict[str, tuple[str, ...]]:
    """Resolve Windows install paths under ``%LOCALAPPDATA%``."""
    base = os.environ.get("LOCALAPPDATA", "")
    if not base:
        return {}
    root = Path(base) / "Programs"
    return {
        "vscode": (str(root / "Microsoft VS Code" / "bin" / "Code.cmd"),),
        "cursor": (str(root / "Cursor" / "Cursor.exe"),),
    }


def detect_installed_ides() -> list[str]:
    """Best-effort detection of locally installed editors.

    Heuristics, in order: ``TERM_PROGRAM`` hints (running inside the
    IDE's integrated terminal), launchers on ``PATH``, well-known
    install locations. Purely advisory — the dashboard always offers
    every protocol, detection only annotates the dropdown.

    @shape return: list[str] (subset of vscode/cursor/pycharm)
    @source env: process-environment#var:TERM_PROGRAM
    """
    global _IDE_DETECTION
    if _IDE_DETECTION is not None:
        return _IDE_DETECTION
    term = (os.environ.get("TERM_PROGRAM") or "").lower()
    win_paths = _windows_ide_paths()
    found: list[str] = []
    for ide, binaries in _IDE_BINARIES.items():
        on_path = any(shutil.which(binary) for binary in binaries)
        installed = any(Path(p).exists() for p in _IDE_PATHS[ide]) or any(
            Path(p).exists() for p in win_paths.get(ide, ())
        )
        if on_path or installed or (ide in term):
            found.append(ide)
    _IDE_DETECTION = found
    return found


class _RepoWatcher:
    """Polls tracked ``.py`` / ``.md`` mtimes and broadcasts changes.

    Deliberately stdlib-only (no watchdog): every poll cycle re-lists
    the tracked file universe via GitProvider and diffs mtime
    snapshots. Subscribers block on a ``threading.Condition`` and are
    woken with the changed paths as soon as a difference is seen.

    @shape snapshot: dict[str, float] (relative path -> mtime)
    @source files: src/core/git_provider.py#function:collect_all_files
    """

    def __init__(self, repo_root: str | Path, interval: float = _WATCH_INTERVAL) -> None:
        self._root = Path(repo_root)
        self._interval = interval
        self._cond = threading.Condition()
        self._version = 0
        self._changed: list[str] = []
        self._snapshot: dict[str, float] = {}
        self._stop = threading.Event()

    @property
    def version(self) -> int:
        """Monotonic change counter (0 = pristine snapshot)."""
        with self._cond:
            return self._version

    def start(self) -> None:
        """Take the baseline snapshot and launch the polling thread."""
        self._snapshot = self._scan()
        threading.Thread(target=self._loop, daemon=True, name="repo-watcher").start()

    def stop(self) -> None:
        """Signal the polling thread and wake all waiting SSE streams."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

    def wait_for_change(self, known_version: int, timeout: float) -> tuple[int, list[str]]:
        """Block until the tree changes or ``timeout`` elapses.

        @shape return: tuple[int, list[str]] (current version, changed paths)
        """
        with self._cond:
            if self._version == known_version:
                self._cond.wait(timeout)
            return self._version, list(self._changed)

    def _scan(self) -> dict[str, float]:
        snapshot: dict[str, float] = {}
        changes = GitProvider().collect_all_files(self._root)
        for rel in changes.code_files + changes.doc_files:
            try:
                snapshot[rel] = (self._root / rel).stat().st_mtime
            except OSError:
                continue
        return snapshot

    def _loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self._interval)
            if self._stop.is_set():
                break
            try:
                snapshot = self._scan()
            except Exception:
                continue  # transient git/FS hiccup — retry next cycle
            changed = [
                rel for rel, mtime in snapshot.items()
                if self._snapshot.get(rel) != mtime
            ]
            changed += [rel for rel in self._snapshot if rel not in snapshot]
            if changed:
                self._snapshot = snapshot
                with self._cond:
                    self._version += 1
                    self._changed = changed
                    self._cond.notify_all()


class AtlasWebServer:
    """Threading HTTP server binding the dashboard and topology API."""

    def __init__(
        self, repo_root: str | Path = ".", host: str = "127.0.0.1", port: int = 8080
    ) -> None:
        self._root = Path(repo_root)
        self._host = host
        self._port = port
        self._watcher = _RepoWatcher(repo_root)
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
        @source events: src/core/server.py#class:_RepoWatcher
        """
        self._watcher.start()
        handler = _build_handler(self._root, self._watcher)
        self._httpd = ThreadingHTTPServer((self._host, self._port), handler)
        try:
            self._httpd.serve_forever()
        finally:
            self._watcher.stop()
            self._httpd.server_close()

    def shutdown(self) -> None:
        """Stop the request loop (safe to call from another thread)."""
        if self._httpd is not None:
            self._httpd.shutdown()


def _build_handler(repo_root: Path, watcher: _RepoWatcher) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to a repository root."""

    class _AtlasRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            if route == "/api/topology":
                self._send_topology()
                return
            if route == "/api/events":
                self._send_events()
                return
            if route == "/api/ides":
                self._send_json({"ides": detect_installed_ides()})
                return
            if route in ("/", "/index.html"):
                self._send_file(UI_DIR / "index.html")
                return
            self._send_static(route)

        def _send_events(self) -> None:
            """Stream Server-Sent Events until the client disconnects.

            Emits ``event: graph_update`` with the changed paths on every
            detected save and a ``: heartbeat`` comment every 15s to keep
            intermediaries from closing the idle connection.

            @source watcher: src/core/server.py#class:_RepoWatcher
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            version = watcher.version
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    new_version, changed = watcher.wait_for_change(version, timeout=15)
                    if watcher.is_stopped():
                        return
                    if new_version != version:
                        version = new_version
                        payload = json.dumps(
                            {"type": "graph_update", "changed": changed}
                        )
                        self.wfile.write(
                            f"event: graph_update\ndata: {payload}\n\n".encode("utf-8")
                        )
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

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

        def _send_json(self, obj: dict, code: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json_error(self, code: int, message: str) -> None:
            self._send_json({"error": message}, code)

        def log_message(self, format: str, *args) -> None:
            """Silence per-request stderr noise; the CLI panel reports state."""

    return _AtlasRequestHandler
