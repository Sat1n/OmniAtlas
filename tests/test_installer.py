"""Pre-commit hook installer tests — cross-project resolution contract."""

import shutil
import stat
import subprocess
from pathlib import Path

from core.installer import BLOCK_START, HookInstaller


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _script_dir(tmp_path: Path) -> Path:
    """A PATH that can spawn bash but demonstrably lacks uv/omni-atlas."""
    bin_dir = tmp_path / "minbin"
    bin_dir.mkdir()
    (bin_dir / "bash").symlink_to(shutil.which("bash"))
    return bin_dir


def test_hook_created_and_idempotent(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    first = HookInstaller(repo).install()
    assert first.status == "created"
    hook = first.hook_path
    text = hook.read_text(encoding="utf-8")
    assert BLOCK_START in text and "omni-atlas check" in text
    assert "uv run omni-atlas check" in text  # fallback path present
    assert hook.stat().st_mode & stat.S_IXUSR
    assert HookInstaller(repo).install().status == "unchanged"


def test_hook_appends_foreign_lines(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
    result = HookInstaller(repo).install()
    assert result.status == "appended"
    text = hook.read_text(encoding="utf-8")
    assert "echo foreign" in text and BLOCK_START in text


def test_hook_skips_when_cli_unavailable(tmp_path: Path) -> None:
    """Missing tooling must warn but never block a commit."""
    repo = _git_repo(tmp_path)
    install = HookInstaller(repo).install()
    proc = subprocess.run(
        [shutil.which("bash"), str(install.hook_path)],
        cwd=repo,
        env={"PATH": str(_script_dir(tmp_path))},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "skipping" in proc.stderr


def test_hook_blocks_only_on_exit_one(tmp_path: Path) -> None:
    """Exit 1 (lint violation) blocks; exit 0 passes."""
    repo = _git_repo(tmp_path)
    install = HookInstaller(repo).install()
    fake_bin = _script_dir(tmp_path)
    fake = fake_bin / "omni-atlas"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)

    blocked = subprocess.run(
        [shutil.which("bash"), str(install.hook_path)],
        cwd=repo,
        env={"PATH": str(fake_bin)},
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 1

    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    passed = subprocess.run(
        [shutil.which("bash"), str(install.hook_path)],
        cwd=repo,
        env={"PATH": str(fake_bin)},
        capture_output=True,
        text=True,
    )
    assert passed.returncode == 0
