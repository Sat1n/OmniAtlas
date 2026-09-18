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


def test_ui_dir_prefers_meipass(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert server_module._ui_dir() == tmp_path / "ui"
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert server_module._ui_dir().name == "ui"  # repo checkout layout
    assert (server_module._ui_dir() / "index.html").is_file()
