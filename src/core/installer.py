"""One-shot Git pre-commit hook installer (``omni-atlas init``).

Locates the repository's effective hooks directory, then injects a guard
block that runs ``omni-atlas check`` before every commit. The guard
resolves the CLI the same way a developer would:

1. ``omni-atlas`` on ``PATH`` (``uv tool install`` / ``pip install``)
2. ``uv run omni-atlas`` (project-local dependency)
3. otherwise: warn and let the commit through — tooling problems must
   never block work.

Only exit code ``1`` (a real lint violation) blocks the commit;
invocation failures are advisory.

Installation is fully idempotent and non-destructive:

* no hook yet            → create one (shebang + guard block, chmod +x)
* foreign hook exists    → append the guard block, foreign lines intact
* OmniAtlas block exists → refresh in place, never duplicated
"""

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Markers delimit the injected region so re-runs can find and refresh it.
BLOCK_START = "# >>> OmniAtlas pre-commit hook >>>"
BLOCK_END = "# <<< OmniAtlas pre-commit hook <<<"

def detect_cli_path() -> str | None:
    """Absolute path of the running OmniAtlas CLI, when identifiable.

    Covers PyInstaller one-file binaries (``sys.executable``) and console
    scripts invoked directly (``sys.argv[0]`` named ``omni-atlas*``).
    Returns None for indirect runs (tests, ``python -m`` wrappers) so the
    caller can fall back to PATH / uv resolution.

    @shape return: str | None
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidate = Path(argv0).resolve()
        if candidate.name.startswith("omni-atlas") and candidate.is_file():
            return str(candidate)
    return None


def build_hook_block(cli_path: str | None = None) -> str:
    """Render the pre-commit guard, optionally embedding the CLI path.

    Resolution order at commit time: the embedded absolute path (frozen
    binaries / direct console-script runs), then ``omni-atlas`` on PATH,
    then ``uv run``. Exit-code contract: only ``check``'s exit 1 (lint
    violation) blocks; missing tooling (2) warns and lets the commit
    through.
    """
    embedded = shlex.quote(cli_path) if cli_path else '""'
    return f"""{BLOCK_START}
# Injected by `omni-atlas init` — blocks commits when staged code changes
# are not synchronized with their referencing documentation.
OMNI_ATLAS_CLI={embedded}
omni_atlas_check() {{
    if [ -n "$OMNI_ATLAS_CLI" ] && [ -x "$OMNI_ATLAS_CLI" ]; then
        "$OMNI_ATLAS_CLI" check
    elif command -v omni-atlas >/dev/null 2>&1; then
        omni-atlas check
    elif command -v uv >/dev/null 2>&1; then
        uv run omni-atlas check 2>/dev/null
    else
        return 2
    fi
}}
omni_atlas_check
omni_atlas_status=$?
if [ "$omni_atlas_status" -eq 1 ]; then
    exit 1
elif [ "$omni_atlas_status" -ne 0 ]; then
    echo "OmniAtlas: CLI unavailable — skipping documentation sync check (install with: uv tool install omni-atlas)." >&2
fi
{BLOCK_END}"""


#: Backwards-compatible default block (no embedded CLI path).
HOOK_BLOCK = build_hook_block(None)


@dataclass
class InstallResult:
    """Outcome of one hook installation attempt.

    @shape status: str ("created" | "appended" | "updated" | "unchanged")
    @shape hook_path: Path
    """

    status: str
    hook_path: Path


class HookInstaller:
    """Installs the OmniAtlas guard into the repository's pre-commit hook."""

    def __init__(self, repo_root: str | Path = ".", cli_path: str | None = None) -> None:
        self._root = Path(repo_root)
        # Explicit override > auto-detected running CLI (frozen/argv0).
        self._cli = cli_path if cli_path is not None else detect_cli_path()

    def install(self) -> InstallResult:
        """Create or update ``pre-commit`` without destroying foreign hooks.

        Raises:
            RuntimeError: when git is unavailable or the target directory
                is not a Git repository.

        @shape return: InstallResult(status, hook_path)
        @source hooks_dir: git#command:rev-parse --git-path hooks
        """
        hook = self._locate_hooks_dir() / "pre-commit"
        block = build_hook_block(self._cli)

        if not hook.exists():
            hook.write_text(
                f"#!/usr/bin/env bash\n\n{block}\n", encoding="utf-8"
            )
            self._make_executable(hook)
            return InstallResult("created", hook)

        text = hook.read_text(encoding="utf-8")
        if BLOCK_START in text:
            refreshed = self._replace_block(text, block)
            if refreshed == text:
                self._make_executable(hook)
                return InstallResult("unchanged", hook)
            hook.write_text(refreshed, encoding="utf-8")
            self._make_executable(hook)
            return InstallResult("updated", hook)

        # Foreign hook: keep every existing line intact, only append.
        hook.write_text(
            text.rstrip("\n") + f"\n\n{block}\n", encoding="utf-8"
        )
        self._make_executable(hook)
        return InstallResult("appended", hook)

    def _locate_hooks_dir(self) -> Path:
        """Resolve the effective hooks directory via ``git rev-parse``.

        Honors ``core.hooksPath`` and handles linked worktrees, where
        ``.git`` is a file rather than a directory.

        @shape return: Path (absolute hooks directory, created if missing)
        @source stdout: git#command:rev-parse --git-path hooks
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-path", "hooks"],
                cwd=self._root,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Git executable not found. "
                "Please ensure `git` is installed and available on PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "Current directory is not a Git repository. "
                "Run `git init` before invoking `omni-atlas init`."
            ) from exc

        hooks_dir = (self._root / result.stdout.strip()).resolve()
        hooks_dir.mkdir(parents=True, exist_ok=True)
        return hooks_dir

    @staticmethod
    def _replace_block(text: str, block: str) -> str:
        """Swap the region between the OmniAtlas markers for the current block."""
        start = text.index(BLOCK_START)
        end = text.index(BLOCK_END, start) + len(BLOCK_END)
        return text[:start] + block + text[end:]

    @staticmethod
    def _make_executable(path: Path) -> None:
        """chmod +x — Git silently skips hook scripts lacking the exec bit."""
        path.chmod(path.stat().st_mode | 0o111)
