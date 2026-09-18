"""One-command PyInstaller build for the current platform.

Bundles the CLI, the single-file dashboard and every Tree-sitter
grammar C-extension into a one-file binary named after the OS/arch:

    omni-atlas-linux-x64 · omni-atlas-macos-arm64 · omni-atlas-win-x64.exe

Run with ``python scripts/build.py`` (uv resolves PyInstaller from the
dev dependency group). The output lands in ``dist/``.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Grammar distributions whose compiled C-extensions must be bundled.
GRAMMAR_PACKAGES = (
    "tree_sitter",
    "tree_sitter_python",
    "tree_sitter_typescript",
    "tree_sitter_go",
    "tree_sitter_rust",
    "tree_sitter_c",
    "tree_sitter_cpp",
)

#: Distribution names for importlib metadata (doctor reports versions).
METADATA_PACKAGES = (
    "tree-sitter",
    "tree-sitter-python",
    "tree-sitter-typescript",
    "tree-sitter-go",
    "tree-sitter-rust",
    "tree-sitter-c",
    "tree-sitter-cpp",
)


def target_name(system: str | None = None, machine: str | None = None) -> str:
    """Binary name for the current (or given) platform/architecture.

    @shape return: str (e.g. omni-atlas-linux-x64)
    """
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    os_name = {"darwin": "macos", "windows": "win"}.get(system, "linux")
    arch = {
        "x86_64": "x64",
        "amd64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine)
    suffix = ".exe" if system == "windows" else ""
    return f"omni-atlas-{os_name}-{arch}{suffix}"


def build_command(name: str) -> list[str]:
    """Assemble the PyInstaller argv for this repository."""
    args = [
        "uv", "run", "pyinstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", name,
        "--paths", "src",
        "--add-data", f"{ROOT / 'src' / 'ui'}{os.pathsep}ui",
    ]
    for package in GRAMMAR_PACKAGES:
        args += ["--collect-all", package]
    for package in METADATA_PACKAGES:
        args += ["--copy-metadata", package]
    args.append(str(ROOT / "src" / "main.py"))
    return args


def main() -> int:
    # Windows consoles default to cp1252 — a stray glyph must never crash
    # the build (observed as UnicodeEncodeError on windows-latest CI).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    name = target_name()
    if shutil.which("uv") is None:
        print("error: uv is required on PATH (https://docs.astral.sh/uv/)", file=sys.stderr)
        return 1
    command = build_command(name)
    print("building:", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        return completed.returncode
    binary = ROOT / "dist" / name
    if not binary.is_file():
        print(f"error: expected binary not found: {binary}", file=sys.stderr)
        return 1
    size_mb = binary.stat().st_size / (1024 * 1024)
    print(f"\n[OK] built {binary} ({size_mb:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
