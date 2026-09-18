"""Exclusion policy tests: .gitignore, .omniignore, [scan].exclude, --exclude."""

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from core.config import load_config
from core.git_provider import GitProvider, IgnoreMatcher, build_ignore_matcher
from omni_atlas.cli.main import app

runner = CliRunner()


def test_ignore_matcher_gitignore_style_patterns() -> None:
    matcher = IgnoreMatcher([
        ".vs", "**/bin", "**/obj", "**/x64", "**/Debug", "**/Release", "*.user",
    ])
    excluded = [
        ".vs/sln/v16/.suo",
        "Rug.Core/bin/Debug/net8/Rug.dll",
        "Rug.UI/obj/project.assets.json",
        "x64/Release/foo.obj",
        "Rug.Core/Program.cs.user",
        "a/b/bin/thing.cs",
    ]
    kept = [
        "Rug.Core/Program.cs",
        "Rug.UI/MainWindow.xaml.cs",
        "src/debug_tools/readme.md",
        "Rug.Poc/README.md",
    ]
    for path in excluded:
        assert matcher.matches(path), path
    for path in kept:
        assert not matcher.matches(path), path
    assert not IgnoreMatcher([])  # empty policy is falsy


def test_ignore_matcher_anchored_and_dir_patterns() -> None:
    matcher = IgnoreMatcher(["/root_only", "cache/", "logs"])
    assert matcher.matches("root_only/file.txt")
    assert not matcher.matches("nested/root_only/file.txt")  # anchored
    assert matcher.matches("any/cache/data.txt")  # dir at any depth
    assert matcher.matches("deep/logs/app.log")


def test_build_ignore_matcher_merges_all_sources(tmp_path: Path) -> None:
    (tmp_path / ".omniignore").write_text("# comment\n.vs\n**/bin\n", encoding="utf-8")
    (tmp_path / ".omni-atlas.toml").write_text(
        '[scan]\nexclude = ["**/obj", "*.user"]\n', encoding="utf-8"
    )
    matcher = build_ignore_matcher(tmp_path, extra_excludes=["**/Release"])
    assert matcher.matches(".vs/x")
    assert matcher.matches("a/bin/x")
    assert matcher.matches("a/obj/x")
    assert matcher.matches("a/x.user")
    assert matcher.matches("a/Release/x")
    assert not matcher.matches("src/core/parser.py")


def test_config_scan_exclude_parsing(tmp_path: Path) -> None:
    config = tmp_path / ".omni-atlas.toml"
    config.write_text('[scan]\nexclude = [".vs", "  **/bin  "]\n', encoding="utf-8")
    assert load_config(tmp_path).exclude == [".vs", "**/bin"]
    config.write_text("[scan]\nexclude = 'not-a-list'\n", encoding="utf-8")
    assert load_config(tmp_path).exclude == []  # graceful


def test_collect_all_files_respects_gitignore(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("bin/\nobj/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "generated.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "obj").mkdir()
    (tmp_path / "obj" / "generated.py").write_text("z = 3\n", encoding="utf-8")

    changes = GitProvider().collect_all_files(tmp_path)
    assert "src/app.py" in changes.code_files
    assert not any("bin/" in f or "obj/" in f for f in changes.code_files)


def test_collect_all_files_extra_exclude_off_repo(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "b.py").write_text("", encoding="utf-8")
    changes = GitProvider().collect_all_files(
        tmp_path, extra_excludes=["**/skip", "keep/a.py"]
    )
    assert changes.code_files == []


def test_cli_check_all_json_honours_exclude() -> None:
    result = runner.invoke(
        app,
        ["check", "--all", "--json", "--exclude", "tests/fixtures/**"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert not any("fixtures/" in f for f in payload["files"]["code"])
    assert any("src/core/parser.py" in f for f in payload["files"]["code"])
