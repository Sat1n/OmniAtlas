"""Phase 9 MCP server, agent tools and headless JSON output tests."""

import json

from typer.testing import CliRunner

from core.mcp import McpServer
from main import app

runner = CliRunner()


def rpc(server: McpServer, method: str, params: dict | None = None, id_: int = 1):
    message = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle_message(message)


def tool_call(server: McpServer, name: str, arguments: dict | None = None) -> dict:
    response = rpc(server, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response is not None
    result = response["result"]
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def test_initialize_handshake_and_notifications() -> None:
    server = McpServer(".")
    handshake = rpc(
        server,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
    )
    assert handshake["result"]["protocolVersion"] == "2025-06-18"  # echoed
    assert handshake["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert handshake["result"]["serverInfo"]["name"] == "omni-atlas"
    # Notifications receive no response.
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_registers_agent_tools() -> None:
    server = McpServer(".")
    tools = rpc(server, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "get_architectural_context",
        "check_doc_sync",
        "query_topology",
        "diagnose_workspace",
    }
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"


def test_architectural_context_returns_docs_and_symbols() -> None:
    payload = tool_call(
        McpServer("."),
        "get_architectural_context",
        {"file_path": "src/core/parser.py"},
    )
    assert payload["file"] == "src/core/parser.py"
    assert any(s["name"] == "MarkdownParser" for s in payload["symbols"])
    docs = {d["path"] for d in payload["documents"]}
    assert "src/core/README.md" in docs
    anchors = payload["documents"][0]["anchors"]
    assert anchors and all("line" in a and "text" in a for a in anchors)


def test_query_topology_by_keyword_and_type() -> None:
    payload = tool_call(
        McpServer("."),
        "query_topology",
        {"keyword": "parser", "node_type": "file"},
    )
    ids = {m["id"] for m in payload["matches"]}
    assert "src/core/parser.py" in ids
    assert all(m["kind"] == "file" for m in payload["matches"])


def test_check_doc_sync_structured_report() -> None:
    payload = tool_call(McpServer("."), "check_doc_sync", {})
    assert payload["anchors"]["total"] >= 30
    assert payload["ok"] == (not payload["stale_docs"])
    for stale in payload["stale_docs"]:
        assert stale["suggestion"]
    filtered = tool_call(McpServer("."), "check_doc_sync", {"path_filter": "src/core"})
    assert all("src/core" in s["code_file"] for s in filtered["stale_docs"])


def test_rpc_errors_are_well_formed() -> None:
    server = McpServer(".")
    unknown = rpc(server, "does/not/exist")
    assert unknown["error"]["code"] == -32601
    parse_error = server.process_line("{not json")
    assert parse_error["error"]["code"] == -32700
    bad_args = rpc(
        server,
        "tools/call",
        {"name": "get_architectural_context", "arguments": {}},
    )
    assert bad_args["error"]["code"] == -32602
    unknown_tool = rpc(
        server, "tools/call", {"name": "nope", "arguments": {}}
    )
    assert unknown_tool["error"]["code"] == -32602


def test_cli_check_json_is_pure_json() -> None:
    result = runner.invoke(app, ["check", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)  # must not contain ANSI/banners
    assert payload["mode"] == "staged"
    assert {"ok", "files", "anchors", "sync", "tokens", "api_links"} <= set(payload)


def test_cli_check_all_json() -> None:
    result = runner.invoke(app, ["check", "--all", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "all"
    assert payload["anchors"]["total"] >= 30
    assert payload["ok"] is True


def test_cli_graph_json() -> None:
    result = runner.invoke(app, ["graph", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["meta"]["node_count"] > 0
    assert any(n["data"]["id"] == "src/ui/index.html" for n in payload["nodes"])
    assert any(e["data"]["kind"] == "api" for e in payload["edges"])
