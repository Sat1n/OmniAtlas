"""Scaffolding tests: templates created safely and lint-clean."""

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from core.linter import LinterEngine
from core.parser import MarkdownParser
from core.scaffold import Scaffolder
from omni_atlas.cli.main import app

runner = CliRunner()


def test_scaffold_creates_three_templates(tmp_path: Path) -> None:
    results = Scaffolder(tmp_path).scaffold()
    statuses = {r.path.name: r.status for r in results}
    assert statuses == {
        "BLUEPRINT.md": "created",
        "AGENTS.md": "created",
        "README.md": "created",
    }
    assert (tmp_path / "src" / "README.md").is_file()
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("---")
    frontmatter = MarkdownParser().parse(tmp_path / "AGENTS.md").frontmatter
    assert frontmatter.get("type") == "logic_node"
    assert str(frontmatter.get("id", "")).endswith("_root")


def test_scaffold_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# custom\n", encoding="utf-8")
    results = Scaffolder(tmp_path).scaffold()
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "# custom\n"
    statuses = {r.path.name: r.status for r in results}
    assert statuses["AGENTS.md"] == "exists"


def test_scaffolded_docs_pass_linter(monkeypatch, tmp_path: Path) -> None:
    Scaffolder(tmp_path).scaffold()
    monkeypatch.chdir(tmp_path)  # L1/L2 classification is cwd-relative
    engine = LinterEngine(tmp_path)
    for doc in ["BLUEPRINT.md", "AGENTS.md", "src/README.md"]:
        anchors = engine.check_anchors([doc])
        assert all(check.found for check in anchors), doc
        for token_check in engine.check_token_budgets([doc]):
            assert token_check.passed, f"{doc}: {token_check.tokens}/{token_check.limit}"


def test_cli_init_scaffolds_and_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0
    assert (tmp_path / "BLUEPRINT.md").is_file()
    assert (tmp_path / "src" / "README.md").is_file()
    (tmp_path / "AGENTS.md").write_text("# custom\n", encoding="utf-8")
    second = runner.invoke(app, ["init"])
    assert second.exit_code == 0
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "# custom\n"


def test_cli_init_no_scaffold(monkeypatch, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--no-scaffold"])
    assert result.exit_code == 0
    assert not (tmp_path / "BLUEPRINT.md").exists()
    assert (tmp_path / ".git" / "hooks" / "pre-commit").is_file()
