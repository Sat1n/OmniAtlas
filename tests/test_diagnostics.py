"""Phase 10 diagnostics, doctor, report-bug and MCP diagnose tests."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from core.diagnostics import (
    KIND_AST_PARSE_ERROR,
    KIND_FILE_SKIPPED,
    KIND_UNMATCHED_API_ROUTE,
    MAX_PARSE_BYTES,
    DiagnosticCollector,
    bug_report_markdown,
    build_bug_report,
    environment_info,
    get_collector,
    reset_collector,
    sanitize_path,
    sanitize_text,
    scan_workspace,
)
from core.mcp import McpServer
from core.parser import LanguageRegistry
from omni_atlas.cli.main import app

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


@pytest.fixture(autouse=True)
def fresh_collector():
    """Every test starts with a clean process-global collector."""
    reset_collector(".")
    yield
    reset_collector(".")


def test_sanitize_path_never_leaks_user_dirs(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    inside = root / "src" / "core" / "a.py"
    assert sanitize_path(inside, root) == "./src/core/a.py"
    outside = tmp_path / "elsewhere" / "b.py"
    assert sanitize_path(outside, root) == "b.py"
    assert sanitize_path("src/core/c.py", root) == "src/core/c.py"
    text = sanitize_text(f"error at {inside} line 3", root)
    assert "/home/" not in text and "elsewhere" not in text


def test_collector_dedupes_and_bounds() -> None:
    collector = DiagnosticCollector(root=".", capacity=3)
    for _ in range(5):
        collector.record(KIND_FILE_SKIPPED, "a/b.min.js", "minified")
    assert len(collector.events()) == 1
    assert collector.events()[0].count == 5
    for index in range(5):
        collector.record(KIND_FILE_SKIPPED, f"file{index}.js", f"skip {index}")
    assert len(collector.events()) <= 3  # ring buffer bound


def test_registry_records_syntax_error() -> None:
    facts = LanguageRegistry().parse_file(FIXTURES / "syntax_error.cpp")
    assert facts.language == "cpp"  # parsing degrades, never raises
    events = get_collector().events()
    assert any(
        event.kind == KIND_AST_PARSE_ERROR and event.file.endswith("syntax_error.cpp")
        for event in events
    )


def test_registry_skips_minified_and_oversized(tmp_path: Path) -> None:
    registry = LanguageRegistry()
    minified = tmp_path / "bundle.min.js"
    minified.write_text("var a=1;", encoding="utf-8")
    facts = registry.parse_file(minified)
    assert facts.skip_reason == "minified bundle"

    oversized = tmp_path / "huge.cpp"
    oversized.write_text("x" * 16, encoding="utf-8")
    import core.parser as parser_module

    original = parser_module.MAX_PARSE_BYTES
    parser_module.MAX_PARSE_BYTES = 8
    try:
        facts = registry.parse_file(oversized)
    finally:
        parser_module.MAX_PARSE_BYTES = original
    assert facts.skip_reason == "oversized file"
    kinds = get_collector().counts()
    assert kinds.get(KIND_FILE_SKIPPED, 0) >= 2


def test_linker_records_unmatched_api_route() -> None:
    registry = LanguageRegistry()
    registry.parse_file(FIXTURES / "index.ts")
    registry.parse_file(FIXTURES / "main.go")
    from core.linker import ApiLinker

    ApiLinker(registry).build_links(
        [str(FIXTURES / "index.ts"), str(FIXTURES / "main.go")]
    )
    events = get_collector().events()
    assert any(
        event.kind == KIND_UNMATCHED_API_ROUTE
        and "/api/unknown-endpoint" in event.message
        for event in events
    )


def test_scan_workspace_reports_fixture_syntax_error() -> None:
    report = scan_workspace(".")
    assert report.total_files > 10
    assert any(
        item["file"].endswith("syntax_error.cpp") for item in report.syntax_errors
    )
    assert report.health < 100
    assert any(
        item["path"] == "/api/unknown-endpoint" for item in report.unmatched_api
    )
    payload = report.to_dict()
    assert payload["health"] == report.health


def test_environment_info_lists_grammars() -> None:
    env = environment_info()
    assert env["python"]
    packages = {grammar["language"] for grammar in env["grammars"]}
    assert {"python", "typescript", "go", "rust", "c", "cpp"} <= packages
    loaded = [g for g in env["grammars"] if g["loaded"]]
    assert len(loaded) >= 6


def test_cli_doctor_detects_syntax_error_file() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Health:" in result.stdout
    assert "syntax_error.cpp" in result.stdout
    assert "loaded" in result.stdout


def test_cli_report_bug_markdown_is_sanitized() -> None:
    result = runner.invoke(app, ["report-bug"])
    assert result.exit_code == 0
    assert "# OmniAtlas Bug Report" in result.stdout
    assert "/home/" not in result.stdout
    assert "AST_PARSE_ERROR" in result.stdout


def test_cli_report_bug_json_and_out(tmp_path: Path) -> None:
    target = tmp_path / "bug_report.json"
    result = runner.invoke(app, ["report-bug", "--json", "--out", str(target)])
    assert result.exit_code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["tool"] == "omni-atlas"
    assert payload["environment"]["grammars"]
    assert "/home/" not in target.read_text(encoding="utf-8")


def test_mcp_diagnose_workspace() -> None:
    server = McpServer(".")
    file_mode = server.handle_message({
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {
            "name": "diagnose_workspace",
            "arguments": {"file_path": "src/core/parser.py"},
        },
    })
    payload = json.loads(file_mode["result"]["content"][0]["text"])
    assert payload["parsed"] is True
    assert payload["node_count"] > 100

    workspace_mode = server.handle_message({
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/call",
        "params": {"name": "diagnose_workspace", "arguments": {}},
    })
    payload = json.loads(workspace_mode["result"]["content"][0]["text"])
    assert payload["total_files"] > 10
    assert any(
        item["file"].endswith("syntax_error.cpp")
        for item in payload["syntax_errors"]
    )
    assert "recent_diagnostics" in payload
