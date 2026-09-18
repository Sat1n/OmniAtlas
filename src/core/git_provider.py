"""Incremental Git diff scanning engine.

Collects staged file boundaries via ``git diff --cached`` — the default
source of truth for incremental lint targets. ``check`` routes never
perform full-repository scans; the opt-in full sweep
(:meth:`GitProvider.collect_all_files`) exists exclusively for the
CI-oriented ``omni-atlas check --all`` mode.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from rich.console import Console
from rich.text import Text

from core.diagnostics import KIND_FILE_SKIPPED, get_collector

console = Console()

#: Directories never scanned for project files (shared with ``linter.py``).
#: ``vendor`` holds third-party bundles (cytoscape, marked, dagre) that
#: are shipped, not analyzed.
IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", "vendor"}

#: Code extensions in the analysis universe — Python plus the
#: multi-language set (TS/JS, Go, Rust, C/C++) and frontend HTML.
CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".html",
}


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

    def collect_staged_changes(self) -> StagedChanges:
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

        changes = StagedChanges()
        for line in result.stdout.splitlines():
            path = line.strip()
            if not path:
                continue
            if Path(path).suffix in CODE_EXTENSIONS:
                changes.code_files.append(path)
            elif path.endswith(".md"):
                changes.doc_files.append(path)
        return changes

    def collect_all_files(self, root: str | Path = ".") -> StagedChanges:
        """Walk the entire project tree and classify ``.py`` / ``.md`` files.

        Powers the CI-oriented full scan (``omni-atlas check --all``).
        Vendored and hidden trees (``.venv``, ``__pycache__``, ``.git``,
        any dot-directory) are skipped; results reuse the
        :class:`StagedChanges` shape so both scan modes feed the same
        downstream pipeline.

        @shape return: StagedChanges(code_files, doc_files)
        @source root: filesystem#path:. (repository root)
        """
        changes = StagedChanges()
        collector = get_collector(root)
        for path in sorted(Path(root).rglob("*")):
            if not path.is_file():
                continue
            if any(
                part in IGNORED_DIRS or part.startswith(".")
                for part in path.parts
            ):
                if "vendor" in path.parts:
                    collector.record(
                        KIND_FILE_SKIPPED, path, "vendored third-party file skipped",
                        reason="vendor",
                    )
                continue
            if path.suffix in CODE_EXTENSIONS:
                changes.code_files.append(path.as_posix())
            elif path.suffix == ".md":
                changes.doc_files.append(path.as_posix())
        return changes

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
