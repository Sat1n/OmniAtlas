"""Pre-commit hook installer tests — cross-project resolution contract."""

import shutil
import stat
import subprocess
import sys
from pathlib import Path

from core.installer import BLOCK_START, HookInstaller, detect_cli_path


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _script_dir(tmp_path: Path) -> Path:
    """A PATH that can spawn bash but demonstrably lacks uv/omni-atlas."""
    bin_dir = tmp_path / "minbin"
    bin_dir.mkdir(exist_ok=True)
    link = bin_dir / "bash"
    if not link.exists():
        link.symlink_to(shutil.which("bash"))
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


def test_detect_cli_path_prefers_frozen_and_argv0(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/fake/frozen-bin")
    assert detect_cli_path() == "/fake/frozen-bin"
    monkeypatch.delattr(sys, "frozen")

    fake = tmp_path / "omni-atlas"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(fake), "init"])
    assert detect_cli_path() == str(fake)

    monkeypatch.setattr(sys, "argv", ["/usr/bin/pytest", "tests"])
    assert detect_cli_path() is None  # indirect runs fall back to PATH/uv


def test_hook_embeds_frozen_cli_and_uses_it(tmp_path: Path, monkeypatch) -> None:
    """A frozen binary's absolute path is embedded and takes priority."""
    fake_cli = tmp_path / "omni-atlas-linux-x64"
    fake_cli.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_cli.chmod(0o755)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = _git_repo(repo_dir)

    install = HookInstaller(repo, cli_path=str(fake_cli)).install()
    text = install.hook_path.read_text(encoding="utf-8")
    assert str(fake_cli) in text  # embedded absolute path

    # Runtime: the embedded CLI exits 1 → commit blocked even without PATH.
    blocked = subprocess.run(
        [shutil.which("bash"), str(install.hook_path)],
        cwd=repo,
        env={"PATH": str(_script_dir(tmp_path))},
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 1

    # A stale embedded path degrades gracefully to the advisory chain.
    fake_cli.unlink()
    install = HookInstaller(repo, cli_path=str(fake_cli)).install()
    skipped = subprocess.run(
        [shutil.which("bash"), str(install.hook_path)],
        cwd=repo,
        env={"PATH": str(_script_dir(tmp_path))},
        capture_output=True,
        text=True,
    )
    assert skipped.returncode == 0
    assert "skipping" in skipped.stderr
