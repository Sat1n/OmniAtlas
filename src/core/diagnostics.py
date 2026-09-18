"""System diagnostics: event collection, sanitization and health scans.

A process-global :class:`DiagnosticCollector` captures the silent-failure
modes of the analysis pipeline so ``doctor``, ``report-bug`` and the MCP
``diagnose_workspace`` tool can surface them:

* ``AST_PARSE_ERROR`` — Tree-sitter produced ERROR nodes for a file.
* ``UNMATCHED_API_ROUTE`` — a frontend call found no backend route.
* ``INVALID_CUSTOM_SCM`` — a ``[[custom_scm]]`` entry failed validation.
* ``FILE_SKIPPED`` — vendored / minified / oversized file left out.

Every recorded path goes through :func:`sanitize_path`: absolute paths
are rebased onto the repository root (``./``), paths outside the repo
keep only their basename, and home directories are never exported —
reports are safe to paste into public issue trackers.

Zero dependencies beyond the stdlib (rich is used only by the CLI).
"""

import json
import os
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Event kinds surfaced by the diagnostics pipeline.
KIND_AST_PARSE_ERROR = "AST_PARSE_ERROR"
KIND_UNMATCHED_API_ROUTE = "UNMATCHED_API_ROUTE"
KIND_INVALID_CUSTOM_SCM = "INVALID_CUSTOM_SCM"
KIND_FILE_SKIPPED = "FILE_SKIPPED"

#: Files above this size are skipped by the multi-language parser.
MAX_PARSE_BYTES = 1_000_000


def sanitize_path(path: str | Path, root: str | Path | None = None) -> str:
    """Rebase a path for public reports — never leak user directories.

    In-repo paths become ``./relative``; anything outside the repository
    keeps only its basename.

    @shape return: str (sanitized relative path)
    """
    raw = Path(path)
    root_path = Path(root).resolve() if root else None
    try:
        if raw.is_absolute():
            resolved = raw.resolve()
            if root_path is not None:
                try:
                    return "./" + resolved.relative_to(root_path).as_posix()
                except ValueError:
                    return resolved.name
            return raw.name
    except OSError:
        return raw.name
    text = raw.as_posix()
    if text.startswith("./"):
        return text
    return text.lstrip("/") if text.startswith("/") else text


def sanitize_text(text: str, root: str | Path | None = None) -> str:
    """Strip absolute paths and home directories out of free text.

    Used for stack traces in bug reports: line numbers, symbol names and
    exception types stay, filesystem identity does not.
    """
    if root:
        try:
            text = text.replace(str(Path(root).resolve()), ".")
            text = text.replace(str(Path(root).resolve()) + "/", "./")
        except OSError:
            pass
    home = str(Path.home())
    if home and home != "/" and home in text:
        text = text.replace(home, "~")
    return text


@dataclass
class DiagnosticEvent:
    """One collected diagnostic occurrence (deduplicated by key)."""

    kind: str
    file: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    count: int = 1
    timestamp: float = field(default_factory=time.time)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.file, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file": self.file,
            "message": self.message,
            "detail": self.detail,
            "count": self.count,
        }


class DiagnosticCollector:
    """Thread-safe ring buffer of diagnostic events."""

    def __init__(self, root: str | Path | None = None, capacity: int = 200) -> None:
        self._root = Path(root).resolve() if root else None
        self._events: deque[DiagnosticEvent] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    @property
    def root(self) -> Path | None:
        return self._root

    def set_root(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def record(self, kind: str, file: str | Path, message: str, **detail: Any) -> DiagnosticEvent:
        """Record one event; identical repeats increment ``count`` instead."""
        event = DiagnosticEvent(
            kind=kind,
            file=sanitize_path(file, self._root),
            message=sanitize_text(message, self._root),
            detail={k: sanitize_text(str(v), self._root) for k, v in detail.items()},
        )
        with self._lock:
            for existing in reversed(self._events):
                if existing.key == event.key:
                    existing.count += 1
                    existing.timestamp = event.timestamp
                    return existing
            self._events.append(event)
        return event

    def events(self, limit: int | None = None) -> list[DiagnosticEvent]:
        """Most recent events (oldest first), optionally capped."""
        with self._lock:
            snapshot = list(self._events)
        return snapshot[-limit:] if limit else snapshot

    def counts(self) -> dict[str, int]:
        """Total occurrences per kind (deduplicated events expanded)."""
        totals: dict[str, int] = {}
        for event in self.events():
            totals[event.kind] = totals.get(event.kind, 0) + event.count
        return totals

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


_COLLECTOR: DiagnosticCollector | None = None
_COLLECTOR_LOCK = threading.Lock()


def get_collector(root: str | Path | None = None) -> DiagnosticCollector:
    """Return the process-global collector (optionally binding a root)."""
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        if _COLLECTOR is None:
            _COLLECTOR = DiagnosticCollector(root)
        elif root is not None and _COLLECTOR.root is None:
            _COLLECTOR.set_root(root)
        return _COLLECTOR


def reset_collector(root: str | Path | None = None) -> DiagnosticCollector:
    """Replace the global collector (used by tests and CLI entrypoints)."""
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        _COLLECTOR = DiagnosticCollector(root)
        return _COLLECTOR


# --------------------------------------------------------------------- #
# Environment / workspace health
# --------------------------------------------------------------------- #

#: Grammar distribution packages per registry language id.
GRAMMAR_PACKAGES = {
    "python": "tree-sitter-python",
    "typescript": "tree-sitter-typescript",
    "tsx": "tree-sitter-typescript",
    "go": "tree-sitter-go",
    "rust": "tree-sitter-rust",
    "c": "tree-sitter-c",
    "cpp": "tree-sitter-cpp",
    "c_sharp": "tree-sitter-c-sharp",
}


def _package_version(name: str) -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(name)
    except Exception:
        return None


def environment_info() -> dict[str, Any]:
    """Python / platform / Tree-sitter environment snapshot.

    @shape return: dict(python, platform, tree_sitter, grammars)
    """
    import sys

    info: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "tree_sitter": _package_version("tree-sitter"),
        "grammars": [],
    }
    try:
        from core.parser import LanguageRegistry

        registry = LanguageRegistry()
        available = set(registry.available_languages)
        for language in sorted(GRAMMAR_PACKAGES):
            info["grammars"].append({
                "language": language,
                "package": GRAMMAR_PACKAGES[language],
                "version": _package_version(GRAMMAR_PACKAGES[language]),
                "loaded": language in available,
                "abi": registry.grammar_abi(language),
            })
    except Exception as exc:  # diagnostics must survive a broken environment
        info["registry_error"] = sanitize_text(str(exc))
    return info


@dataclass
class WorkspaceReport:
    """Aggregated parse-health of a workspace."""

    root: str
    total_files: int = 0
    parsed_ok: int = 0
    skipped: list[dict[str, Any]] = field(default_factory=list)
    syntax_errors: list[dict[str, Any]] = field(default_factory=list)
    unmatched_api: list[dict[str, Any]] = field(default_factory=list)

    @property
    def health(self) -> float:
        """Health score in percent (skipped files do not count)."""
        counted = self.total_files - len(self.skipped)
        if counted <= 0:
            return 100.0
        return round(self.parsed_ok / counted * 100, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "total_files": self.total_files,
            "parsed_ok": self.parsed_ok,
            "health": self.health,
            "skipped": self.skipped,
            "syntax_errors": self.syntax_errors,
            "unmatched_api": self.unmatched_api,
        }


def _first_error_line(root) -> int | None:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            return node.start_point[0] + 1
        stack.extend(reversed(node.children))
    return None


def diagnose_file(file_path: str | Path, root: str | Path = ".") -> dict[str, Any]:
    """Parse one file and report its Tree-sitter health.

    @shape return: dict(file, language, parsed, node_count, error_count, ...)
    """
    from core.parser import LanguageRegistry

    root_path = Path(root)
    path = Path(file_path)
    if not path.is_absolute():
        path = root_path / path
    registry = LanguageRegistry()
    result: dict[str, Any] = {
        "file": sanitize_path(path, root_path),
        "language": registry.language_for(path) or ("html" if path.suffix == ".html" else None),
        "parsed": False,
        "node_count": 0,
        "error_count": 0,
        "first_error_line": None,
    }
    if not path.is_file():
        result["error"] = "file not found"
        return result
    if path.suffix.lower() == ".html":
        result["parsed"] = True  # regex-scanned, no grammar to fail
        return result
    language = registry.language_for(path)
    parser = registry.parser_for(language) if language else None
    if parser is None:
        result["error"] = "no grammar loaded for this language"
        return result
    try:
        source = path.read_bytes()
    except OSError as exc:
        result["error"] = sanitize_text(str(exc), root_path)
        return result
    tree = parser.parse(source)
    result["parsed"] = True
    result["node_count"] = sum(1 for _ in _iter_nodes(tree.root_node))
    if tree.root_node.has_error:
        result["error_count"] = sum(
            1 for node in _iter_nodes(tree.root_node) if node.type == "ERROR" or node.is_missing
        )
        result["first_error_line"] = _first_error_line(tree.root_node)
    return result


def _iter_nodes(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def scan_workspace(
    root: str | Path = ".", extra_excludes: list[str] | None = None
) -> WorkspaceReport:
    """Parse every workspace file and aggregate the health report.

    @shape return: WorkspaceReport
    @source files: src/core/git_provider.py#function:collect_all_files
    """
    from core.diagnostics import get_collector
    from core.git_provider import GitProvider
    from core.linker import ApiLinker
    from core.parser import LanguageRegistry

    root_path = Path(root)
    collector = get_collector(root_path)
    registry = LanguageRegistry()
    report = WorkspaceReport(root=sanitize_path(root_path, root_path))

    changes = GitProvider().collect_all_files(
        root_path, extra_excludes=extra_excludes
    )
    for rel in changes.code_files:
        report.total_files += 1
        path = root_path / rel
        if path.suffix.lower() == ".html":
            report.parsed_ok += 1
            continue
        diagnosis = diagnose_file(path, root_path)
        if diagnosis.get("error"):
            report.skipped.append({"file": diagnosis["file"], "reason": diagnosis["error"]})
            continue
        if diagnosis["error_count"]:
            report.syntax_errors.append({
                "file": diagnosis["file"],
                "language": diagnosis["language"],
                "error_nodes": diagnosis["error_count"],
                "first_error_line": diagnosis["first_error_line"],
            })
            collector.record(
                KIND_AST_PARSE_ERROR,
                path,
                f"{diagnosis['error_count']} syntax error node(s)",
                line=diagnosis["first_error_line"] or "",
            )
        else:
            report.parsed_ok += 1

    linker = ApiLinker(registry)
    audit = linker.audit(changes.code_files)
    for source_file, method, url in audit.unmatched:
        report.unmatched_api.append({
            "source_file": source_file,
            "method": method,
            "path": url,
        })
    return report


# --------------------------------------------------------------------- #
# Bug report building
# --------------------------------------------------------------------- #

def build_bug_report(root: str | Path = ".") -> dict[str, Any]:
    """Assemble the full sanitized bug-report payload.

    @shape return: dict(tool, generated_at, environment, workspace, diagnostics)
    """
    collector = get_collector(root)
    workspace = scan_workspace(root)
    return {
        "tool": "omni-atlas",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": environment_info(),
        "workspace": workspace.to_dict(),
        "diagnostics_totals": collector.counts(),
        "diagnostics_recent": [e.to_dict() for e in collector.events(50)],
    }


def bug_report_markdown(report: dict[str, Any]) -> str:
    """Render a bug-report payload as paste-safe Markdown.

    All paths were sanitized at collection time — no absolute user
    directories and no business code appear in the output.
    """
    env = report.get("environment", {})
    workspace = report.get("workspace", {})
    lines = [
        "# OmniAtlas Bug Report",
        "",
        f"Generated: `{report.get('generated_at', '')}`",
        "",
        "## Environment",
        "",
        f"- Python: `{env.get('python')}`",
        f"- Platform: `{env.get('platform')}`",
        f"- tree-sitter core: `{env.get('tree_sitter')}`",
        "",
        "| Grammar | Package | Version | ABI | Loaded |",
        "|---|---|---|---|---|",
    ]
    for grammar in env.get("grammars", []):
        lines.append(
            f"| {grammar['language']} | {grammar['package']} | "
            f"{grammar.get('version') or '—'} | {grammar.get('abi') or '—'} | "
            f"{'yes' if grammar.get('loaded') else 'no'} |"
        )

    lines += [
        "",
        "## Workspace Health",
        "",
        f"- Files: {workspace.get('total_files', 0)} · "
        f"parsed OK: {workspace.get('parsed_ok', 0)} · "
        f"health: **{workspace.get('health', 0)}%**",
        f"- Skipped: {len(workspace.get('skipped', []))} · "
        f"syntax warnings: {len(workspace.get('syntax_errors', []))} · "
        f"unmatched API calls: {len(workspace.get('unmatched_api', []))}",
    ]

    syntax_errors = workspace.get("syntax_errors", [])
    if syntax_errors:
        lines += ["", "### Syntax Warning Files", ""]
        for item in syntax_errors[:20]:
            lines.append(
                f"- `{item['file']}` ({item.get('language')}): "
                f"{item.get('error_nodes')} error node(s), first at line "
                f"{item.get('first_error_line')}"
            )
    skipped = workspace.get("skipped", [])
    if skipped:
        lines += ["", "### Skipped Files", ""]
        for item in skipped[:20]:
            lines.append(f"- `{item['file']}`: {item.get('reason')}")
    unmatched = workspace.get("unmatched_api", [])
    if unmatched:
        lines += ["", "### Unmatched API Calls", ""]
        for item in unmatched[:20]:
            lines.append(
                f"- `{item['source_file']}`: {item['method']} {item['path']}"
            )

    lines += ["", "## Recent Diagnostics (last 50)", ""]
    recent = report.get("diagnostics_recent", [])
    if not recent:
        lines.append("_No diagnostic events collected during this run._")
    for event in recent:
        detail = f" `{json.dumps(event['detail'], ensure_ascii=False)}`" if event.get("detail") else ""
        lines.append(
            f"- `[{event['kind']}]` `{event['file']}` — {event['message']}"
            f" (×{event.get('count', 1)}){detail}"
        )
    lines += [
        "",
        "---",
        "_Paths are repository-relative; no user directories or source "
        "code are included._",
        "",
    ]
    return "\n".join(lines)
