"""Phase 11 release/distribution tests: init-mcp, build naming, _MEIPASS."""

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

import core.server as server_module
from core.mcp import build_client_config, mcp_server_entry
from omni_atlas.cli.main import app
from scripts.build import target_name

runner = CliRunner()


def test_init_mcp_prints_valid_json() -> None:
    result = runner.invoke(app, ["init-mcp", "--target", "cursor"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    entry = payload["mcpServers"]["omni-atlas"]
    assert entry["command"]  # absolute binary or uv path
    assert entry["args"] == ["mcp"]
    assert Path(entry["cwd"]).is_absolute()


def test_init_mcp_write_merges_cursor_config(tmp_path: Path) -> None:
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    config = cursor_dir / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"other": {"command": "foo"}}}), encoding="utf-8"
    )
    result = runner.invoke(
        app,
        ["init-mcp", "--target", "cursor", "--workspace", str(tmp_path), "--write"],
    )
    assert result.exit_code == 0
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["other"] == {"command": "foo"}  # preserved
    assert payload["mcpServers"]["omni-atlas"]["cwd"] == str(tmp_path.resolve())


def test_init_mcp_write_claude_out_override(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    result = runner.invoke(
        app,
        ["init-mcp", "--target", "claude", "--out", str(target), "--write"],
    )
    assert result.exit_code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "omni-atlas" in payload["mcpServers"]


def test_init_mcp_write_refuses_invalid_existing_config(tmp_path: Path) -> None:
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    config = cursor_dir / "mcp.json"
    config.write_text("{not valid json", encoding="utf-8")
    result = runner.invoke(
        app,
        ["init-mcp", "--target", "cursor", "--workspace", str(tmp_path), "--write"],
    )
    assert result.exit_code == 1
    assert config.read_text(encoding="utf-8") == "{not valid json"  # untouched


def test_init_mcp_opencode_prints_and_merges(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init-mcp", "--target", "opencode", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    entry = payload["mcp"]["omni-atlas"]
    assert entry["type"] == "local"
    assert entry["command"][-1] == "mcp"  # command array carries the subcommand
    assert entry["enabled"] is True
    assert entry["cwd"] == str(tmp_path.resolve())

    # Merge into an existing opencode.json without clobbering other keys.
    target = tmp_path / "opencode.json"
    target.write_text(
        json.dumps({"mcp": {"existing": {"type": "local", "command": ["foo"]}}, "theme": "dark"}),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["init-mcp", "--target", "opencode", "--workspace", str(tmp_path), "--write"],
    )
    assert result.exit_code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    assert payload["mcp"]["existing"]["command"] == ["foo"]
    assert payload["mcp"]["omni-atlas"]["type"] == "local"


def test_init_mcp_codex_toml_snippet_and_merge(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init-mcp", "--target", "codex", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    assert "[mcp_servers.omni-atlas]" in result.stdout
    assert 'cwd = "' in result.stdout

    target = tmp_path / "config.toml"
    target.write_text(
        'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "other"\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["init-mcp", "--target", "codex", "--workspace", str(tmp_path), "--out", str(target), "--write"],
    )
    assert result.exit_code == 0
    text = target.read_text(encoding="utf-8")
    assert text.count("[mcp_servers.omni-atlas]") == 1
    assert "[mcp_servers.other]" in text and 'model = "gpt-5"' in text

    # Idempotent: a second write replaces the section instead of appending.
    result = runner.invoke(
        app,
        ["init-mcp", "--target", "codex", "--workspace", str(tmp_path), "--out", str(target), "--write"],
    )
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8").count("[mcp_servers.omni-atlas]") == 1


def test_init_mcp_rejects_unknown_target() -> None:
    result = runner.invoke(app, ["init-mcp", "--target", "vscode"])
    assert result.exit_code == 1
    assert "unknown target" in result.stdout


def test_mcp_server_entry_shape() -> None:
    entry = mcp_server_entry("/tmp/ws")
    assert set(entry) == {"command", "args", "cwd"}
    assert entry["cwd"] == str(Path("/tmp/ws").resolve())


def test_target_name_mapping() -> None:
    assert target_name("Linux", "x86_64") == "omni-atlas-linux-x64"
    assert target_name("Darwin", "arm64") == "omni-atlas-macos-arm64"
    assert target_name("Windows", "AMD64") == "omni-atlas-win-x64.exe"
    assert target_name("Windows", "arm64") == "omni-atlas-win-arm64.exe"


def test_version_consistency() -> None:
    """pyproject, CLI banner and MCP serverInfo must never drift apart."""
    import tomllib

    from core.mcp import SERVER_VERSION
    from omni_atlas.cli.main import VERSION

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    package_version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert VERSION == package_version, f"CLI {VERSION} != pyproject {package_version}"
    assert SERVER_VERSION == package_version, f"MCP {SERVER_VERSION} != pyproject {package_version}"


def test_ui_dir_prefers_meipass(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert server_module._ui_dir() == tmp_path / "ui"
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert server_module._ui_dir().name == "ui"  # repo checkout layout
    assert (server_module._ui_dir() / "index.html").is_file()
