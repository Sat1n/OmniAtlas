"""Incremental Git diff scanning engine.

Collects staged file boundaries via ``git diff --cached`` — the default
source of truth for incremental lint targets. ``check`` routes never
perform full-repository scans; the opt-in full sweep
(:meth:`GitProvider.collect_all_files`) exists exclusively for the
CI-oriented ``omni-atlas check --all`` mode.
"""

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from rich.console import Console
from rich.text import Text

from core.diagnostics import KIND_FILE_SKIPPED, get_collector

#: Custom ignore file recognised next to the project config.
IGNORE_FILENAME = ".omniignore"

console = Console()

#: Directories never scanned for project files (shared with ``linter.py``).
#: ``vendor`` holds third-party bundles (cytoscape, marked, dagre) that
#: are shipped, not analyzed.
IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", "vendor"}

#: Code extensions in the analysis universe — Python plus the
#: multi-language set (TS/JS, Go, Rust, C/C++) and frontend HTML.
CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".html",
}


class IgnoreMatcher:
    """Glob-based exclusion policy for file discovery.

    Supports the practical gitignore subset used by ``.omniignore``,
    ``[scan].exclude`` and the CLI ``--exclude`` flag:

    * ``name`` matches any path segment at any depth (``bin``, ``*.user``)
    * ``dir/`` matches directories at any depth (contents follow)
    * ``**/bin`` and ``src/bin`` match whole relative paths (``*`` may
      cross separators, so ``**`` behaves like ``*``)

    Negation (``!``) is intentionally not supported yet.

    @source patterns: filesystem#path:.omniignore
    """

    def __init__(self, patterns: list[str] | None = None) -> None:
        self._patterns = [
            raw.strip() for raw in (patterns or [])
            if raw.strip() and not raw.strip().startswith("#")
        ]

    def __bool__(self) -> bool:
        return bool(self._patterns)

    @classmethod
    def from_file(cls, path: str | Path) -> "IgnoreMatcher":
        """Load patterns from a ``.omniignore`` file (missing file = empty)."""
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return cls([])
        return cls(text.splitlines())

    @property
    def patterns(self) -> list[str]:
        return list(self._patterns)

    def matches(self, rel_path: str) -> bool:
        """True when a repository-relative POSIX path is excluded.

        @shape return: bool
        """
        rel = rel_path.strip("/")
        if not rel:
            return False
        parts = rel.split("/")
        for raw in self._patterns:
            pattern = raw.rstrip("/")
            anchored = raw.startswith("/")
            # `**/foo` matches `foo` at any depth — including the root.
            if pattern.startswith("**/"):
                pattern = pattern[3:]
                anchored = False
            if "/" not in pattern or anchored:
                target = pattern.lstrip("/")
                if fnmatch.fnmatch(rel, target) or fnmatch.fnmatch(rel, target + "/*"):
                    return True
                if not anchored and any(
                    fnmatch.fnmatch(part, target) for part in parts
                ):
                    return True
            else:
                if (
                    fnmatch.fnmatch(rel, pattern)
                    or fnmatch.fnmatch(rel, pattern + "/*")
                    or rel == pattern
                    or rel.startswith(pattern + "/")
                ):
                    return True
        return False


def build_ignore_matcher(
    root: str | Path = ".", extra_excludes: list[str] | None = None
) -> IgnoreMatcher:
    """Merge ``.omniignore`` + ``[scan].exclude`` + CLI extras into one policy.

    @shape return: IgnoreMatcher
    @source config: src/core/config.py#function:load_config
    """
    from core.config import load_config

    root_path = Path(root)
    patterns = list(IgnoreMatcher.from_file(root_path / IGNORE_FILENAME).patterns)
    try:
        patterns += load_config(root_path).exclude
    except Exception:
        pass  # config problems never block scanning
    patterns += list(extra_excludes or [])
    return IgnoreMatcher(patterns)


@dataclass
class StagedChanges:
    """Categorized file paths collected from the Git staging area.

    @shape code_files: list[str]
    @shape doc_files: list[str]
    @source code_files: git-index#command:diff --cached --name-only
    """

    code_files: list[str] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)


class GitProvider:
    """Collects incremental change boundaries from the Git staging area."""

    def __init__(self, root: str | Path = ".") -> None:
        self._root = Path(root)

    def collect_staged_changes(
        self, extra_excludes: list[str] | None = None
    ) -> StagedChanges:
        """Run ``git diff --cached --name-only`` and classify the results.

        Returns:
            StagedChanges with ``.py`` files under ``code_files`` and
            ``.md`` files under ``doc_files``.

        Exits with code 1 (via rich error message) when the working
        directory is not a Git repository or the git command fails.

        @shape stdout: list[str] (one relative path per line)
        @shape return: StagedChanges(code_files, doc_files)
        @source stdout: git-index#command:diff --cached --name-only
        """
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            self._fail(
                "Git executable not found. "
                "Please ensure `git` is installed and available on PATH."
            )
        except subprocess.CalledProcessError as exc:
            # Keep only the first stderr line: git dumps long usage text
            # after a fatal error, which would flood the terminal.
            stderr = (exc.stderr or "").strip().splitlines()
            first_line = stderr[0] if stderr else "unknown error"
            if "not a git repository" in first_line.lower():
                self._fail(
                    "Current directory is not a Git repository. "
                    "Run `git init` before invoking OmniAtlas."
                )
            self._fail(f"Git command failed: {first_line}")

        matcher = build_ignore_matcher(self._root, extra_excludes)
        changes = StagedChanges()
        for line in result.stdout.splitlines():
            path = line.strip()
            if not path or matcher.matches(path):
                continue
            if Path(path).suffix in CODE_EXTENSIONS:
                changes.code_files.append(path)
            elif path.endswith(".md"):
                changes.doc_files.append(path)
        return changes

    def collect_all_files(
        self, root: str | Path = ".", extra_excludes: list[str] | None = None
    ) -> StagedChanges:
        """Enumerate the whole project and classify code / doc files.

        Powers the CI-oriented full scan (``omni-atlas check --all``).
        Discovery prefers ``git ls-files --cached --others
        --exclude-standard`` so ``.gitignore`` rules apply verbatim;
        outside a Git repository it falls back to a filesystem walk.
        Built-in skips (dot trees, ``.venv``, ``node_modules``,
        ``__pycache__``, ``vendor``) and the merged exclusion policy
        (``.omniignore`` + ``[scan].exclude`` + CLI extras) apply on top.

        @shape return: StagedChanges(code_files, doc_files)
        @source root: filesystem#path:. (repository root)
        @source stdout: git#command:ls-files --cached --others --exclude-standard
        """
        root_path = Path(root)
        matcher = build_ignore_matcher(root_path, extra_excludes)
        collector = get_collector(root_path)
        candidates = self._git_listed_files(root_path)
        if candidates is None:  # not a Git repository — walk the tree
            candidates = (
                path.relative_to(root_path).as_posix()
                for path in sorted(root_path.rglob("*"))
                if path.is_file()
            )

        changes = StagedChanges()
        for rel in candidates:
            parts = Path(rel).parts
            if any(part in IGNORED_DIRS or part.startswith(".") for part in parts):
                if "vendor" in parts:
                    collector.record(
                        KIND_FILE_SKIPPED, root_path / rel,
                        "vendored third-party file skipped", reason="vendor",
                    )
                continue
            if matcher.matches(rel):
                collector.record(
                    KIND_FILE_SKIPPED, root_path / rel,
                    "excluded by ignore policy", reason="excluded",
                )
                continue
            suffix = Path(rel).suffix
            if suffix in CODE_EXTENSIONS:
                changes.code_files.append(rel)
            elif suffix == ".md":
                changes.doc_files.append(rel)
        return changes

    @staticmethod
    def _git_listed_files(root: Path) -> list[str] | None:
        """Tracked + untracked-unignored files via git, or None off-repo."""
        try:
            result = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, OSError):
            return None
        return [line for line in result.stdout.split("\0") if line]

    def collect_modified_files(self, root: str | Path = ".") -> list[str]:
        """List every path carrying staged, unstaged or untracked changes.

        Backs the topology graph status overlay (PASS / MODIFIED / STALE).
        Renames are reported under their new path; C-quoted paths emitted
        by git for special characters are unwrapped.

        @shape return: list[str] (repository-relative POSIX paths)
        @source stdout: git#command:status --porcelain
        """
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return []

        modified: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            raw = line[3:].strip()
            # Renames: "R  old/path.py -> new/path.py" — keep the target.
            if " -> " in raw:
                raw = raw.rsplit(" -> ", 1)[1]
            modified.append(raw.strip('"'))
        return modified

    @staticmethod
    def _fail(message: str) -> NoReturn:
        """Print a friendly rich error and halt execution with exit code 1."""
        # Render as plain Text: git stderr may contain brackets that would
        # otherwise be misparsed as rich markup tags.
        text = Text("✖ Error: ", style="bold red")
        text.append(message, style="red")
        console.print(text)
        raise SystemExit(1)
