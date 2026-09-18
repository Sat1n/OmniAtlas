"""Headless MCP (Model Context Protocol) server over stdio JSON-RPC.

Implements the MCP essentials with zero dependencies — newline-delimited
JSON-RPC 2.0 on stdin/stdout, the ``initialize`` handshake with tool
capabilities, ``tools/list`` and ``tools/call``. No Content-Length
framing is needed: the MCP stdio transport uses line-delimited JSON.

Three agent-facing tools expose the Phase 8 topology and linter:

* ``get_architectural_context`` — suppliers, consumers, cross-language
  API mappings and the bound L1/L2 document anchors for one file.
* ``check_doc_sync`` — structured linter report (stale docs, broken
  anchors, oversized budgets, unmatched API calls) with fix hints.
* ``query_topology`` — keyword/type search over graph nodes and edges.

@source protocol: model-context-protocol#command:initialize/tools/list/tools/call
"""

import json
import sys
from pathlib import Path
from typing import Any, Callable

from core.diagnostics import diagnose_file, get_collector, scan_workspace
from core.git_provider import GitProvider, StagedChanges
from core.graph import TopologyGraphBuilder
from core.linter import LinterEngine
from core.parser import ANCHOR_RE
from core.linker import FRONTEND_EXTENSIONS

#: Server software version reported in the initialize handshake.
SERVER_VERSION = "0.1.0"

#: MCP revision implemented (echoed back when the client speaks a newer one).
PROTOCOL_VERSION = "2024-11-05"

#: JSON-RPC error codes.
_PARSE_ERROR = -32700
_INVALID_PARAMS = -32602
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


class ArchitectureTools:
    """Agent tools backed by the knowledge graph and the linter."""

    def __init__(self, repo_root: str | Path = ".") -> None:
        self._root = Path(repo_root)

    # -- tool: get_architectural_context ----------------------------- #

    def architectural_context(self, file_path: str) -> dict[str, Any]:
        """Suppliers, consumers, API mappings and doc anchors for a file.

        @shape return: dict(file, symbols, suppliers, consumers, api, documents)
        """
        rel = self._normalize(file_path)
        graph = TopologyGraphBuilder(self._root).build().to_dict()
        nodes = {n["data"]["id"]: n["data"] for n in graph["nodes"]}
        file_id = self._locate_file(nodes, rel)
        if file_id is None:
            raise ValueError(f"file not found in topology: {file_path}")
        rel = nodes[file_id].get("path") or rel

        member_ids = {
            nid for nid, data in nodes.items()
            if data.get("path") == rel and nid != file_id
        }
        cluster = {file_id} | member_ids

        suppliers: list[dict[str, Any]] = []
        consumers: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        api_out: list[dict[str, Any]] = []
        api_in: list[dict[str, Any]] = []

        for edge in graph["edges"]:
            data = edge["data"]
            source, target, kind = data["source"], data["target"], data["kind"]
            if kind == "contains" or (source in cluster and target in cluster):
                continue
            if source in cluster:
                other, incoming = target, False
            elif target in cluster:
                other, incoming = source, True
            else:
                continue
            other_data = nodes.get(other, {})
            record = {
                "id": other,
                "label": other_data.get("label", other),
                "kind": other_data.get("kind"),
                "status": other_data.get("status"),
                "path": other_data.get("path"),
            }
            if kind == "api":
                record["method"] = data.get("method")
                record["path_or_route"] = data.get("path")
                (api_in if incoming else api_out).append(record)
            elif kind == "anchor":
                if incoming:  # doc -> file edge: the doc is bound to this file
                    documents.append({
                        **record,
                        "level": other_data.get("kind"),
                        "anchors": self._doc_anchors(rel, other),
                    })
            elif kind == "import":
                (suppliers if not incoming else consumers).append(record)
            elif kind in ("source", "lineage"):
                (suppliers if incoming else consumers).append(record)
            else:
                consumers.append(record)

        symbols = [
            {
                "id": nid,
                "name": nodes[nid].get("label"),
                "kind": nodes[nid].get("kind"),
                "line": nodes[nid].get("line_number"),
            }
            for nid in sorted(member_ids)
        ]
        return {
            "file": rel,
            "language": nodes[file_id].get("language"),
            "symbols": symbols,
            "suppliers": suppliers,
            "consumers": consumers,
            "api": {"outgoing": api_out, "incoming": api_in},
            "documents": documents,
        }

    # -- tool: check_doc_sync ---------------------------------------- #

    def check_doc_sync(self, path_filter: str | None = None) -> dict[str, Any]:
        """Structured linter report over the current working-tree changes.

        A code file is checked against docs referencing it: the doc must
        itself be modified, otherwise it is reported STALE with a fix hint.

        @shape return: dict(ok, anchors, stale_docs, oversized, api_links)
        """
        provider = GitProvider()
        all_files = provider.collect_all_files(self._root)
        docs = [d for d in all_files.doc_files if self._matches(d, path_filter)]
        engine = LinterEngine(self._root)

        anchor_checks = engine.check_anchors(docs)
        token_checks = engine.check_token_budgets(docs)

        modified = set(provider.collect_modified_files(self._root))
        sync_checks = engine.check_reverse_sync(
            StagedChanges(
                code_files=sorted(modified),
                doc_files=sorted(m for m in modified if m.endswith(".md")),
            )
        )
        api_checks = engine.check_api_links(
            StagedChanges(
                code_files=sorted(
                    m for m in modified
                    if Path(m).suffix.lower() in FRONTEND_EXTENSIONS
                ),
                doc_files=[],
            )
        )

        missing = [c for c in anchor_checks if not c.found]
        stale = [
            c for c in sync_checks
            if not c.in_sync and self._matches(c.doc_file, path_filter)
        ]
        oversized = [c for c in token_checks if not c.passed]
        unmatched = [c for c in api_checks if not c.matched]

        return {
            "ok": not (missing or stale or oversized),
            "path_filter": path_filter,
            "anchors": {
                "total": len(anchor_checks),
                "missing": [
                    {
                        "doc_file": c.doc_file,
                        "target_file": c.anchor.file_path,
                        "symbol": f"{c.anchor.symbol_type}:{c.anchor.symbol_name}",
                        "suggestion": (
                            f"Fix or remove the broken anchor in {c.doc_file}"
                        ),
                    }
                    for c in missing
                    if self._matches(c.doc_file, path_filter)
                ],
            },
            "stale_docs": [
                {
                    "code_file": c.code_file,
                    "doc_file": c.doc_file,
                    "suggestion": (
                        f"Update {c.doc_file} and include it in the same "
                        f"commit as {c.code_file}"
                    ),
                }
                for c in stale
            ],
            "oversized": [
                {
                    "doc_file": c.doc_file,
                    "level": c.level,
                    "tokens": c.tokens,
                    "limit": c.limit,
                    "suggestion": f"Chunk {c.doc_file} into smaller sections",
                }
                for c in oversized
            ],
            "api_links": {
                "unmatched": [
                    {
                        "source_file": c.source_file,
                        "method": c.method,
                        "path": c.path,
                        "suggestion": "Add the missing backend route",
                    }
                    for c in unmatched
                ],
            },
        }

    # -- tool: query_topology ---------------------------------------- #

    def query_topology(self, keyword: str, node_type: str | None = None) -> dict[str, Any]:
        """Search topology nodes by keyword (and optional kind filter).

        @shape return: dict(matches, relations, total)
        """
        needle = keyword.lower()
        graph = TopologyGraphBuilder(self._root).build().to_dict()
        nodes = {n["data"]["id"]: n["data"] for n in graph["nodes"]}

        matches = []
        for nid, data in nodes.items():
            if node_type and data.get("kind") != node_type:
                continue
            haystack = " ".join(
                str(part) for part in (
                    nid,
                    data.get("label", ""),
                    data.get("path", ""),
                    data.get("language", ""),
                    json.dumps(data.get("meta", {}), ensure_ascii=False),
                )
            ).lower()
            if needle in haystack:
                matches.append({
                    "id": nid,
                    "label": data.get("label"),
                    "kind": data.get("kind"),
                    "status": data.get("status"),
                    "path": data.get("path"),
                    "line": data.get("line_number"),
                })
        matches = matches[:50]
        match_ids = {m["id"] for m in matches}
        relations = [
            {
                "source": e["data"]["source"],
                "target": e["data"]["target"],
                "kind": e["data"]["kind"],
                "method": e["data"].get("method"),
                "path": e["data"].get("path"),
            }
            for e in graph["edges"]
            if e["data"]["source"] in match_ids or e["data"]["target"] in match_ids
        ]
        return {"total": len(matches), "matches": matches, "relations": relations[:100]}

    # -- tool: diagnose_workspace ------------------------------------ #

    def diagnose_workspace(self, file_path: str | None = None) -> dict[str, Any]:
        """Architecture-transparency probe for agents.

        With ``file_path``: whether that file parses, its Tree-sitter
        node count and warnings. Without: whole-workspace parse health,
        skipped files and unmatched API endpoints.

        @shape return: dict
        """
        if file_path:
            return diagnose_file(file_path, self._root)
        report = scan_workspace(self._root).to_dict()
        report["recent_diagnostics"] = [
            event.to_dict() for event in get_collector(self._root).events(20)
        ]
        return report

    # -- helpers ----------------------------------------------------- #

    def _normalize(self, file_path: str) -> str:
        path = Path(file_path)
        if path.is_absolute():
            try:
                path = path.relative_to(self._root.resolve())
            except ValueError:
                pass
        return path.as_posix().lstrip("./")

    @staticmethod
    def _matches(candidate: str, path_filter: str | None) -> bool:
        return not path_filter or path_filter in candidate

    @staticmethod
    def _locate_file(nodes: dict[str, dict[str, Any]], rel: str) -> str | None:
        for nid, data in nodes.items():
            if data.get("kind") == "file" and data.get("path") == rel:
                return nid
        for nid, data in nodes.items():
            p = data.get("path")
            if data.get("kind") == "file" and p and (p.endswith(rel) or rel.endswith(p)):
                return nid
        return rel if rel in nodes else None

    def _doc_anchors(self, target_file: str, doc_path: str) -> list[dict[str, Any]]:
        """Anchor lines in ``doc_path`` pointing at ``target_file``."""
        try:
            text = (self._root / doc_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        found = []
        for number, line in enumerate(text.splitlines(), 1):
            for match in ANCHOR_RE.finditer(line):
                if Path(match.group("path")).as_posix() == target_file:
                    found.append({"line": number, "text": line.strip()})
        return found


#: Tool catalogue advertised through MCP ``tools/list``.
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_architectural_context",
        "description": (
            "Call before modifying a file: returns upstream suppliers, "
            "downstream consumers, cross-language API mappings and the bound "
            "L1/L2 markdown document anchors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Repository-relative file path.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "check_doc_sync",
        "description": (
            "Call after editing code: runs the linter over working-tree "
            "changes and reports STALE docs, broken anchors, oversized "
            "budgets and unmatched API calls with fix suggestions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path_filter": {
                    "type": "string",
                    "description": "Optional substring filter for files.",
                },
            },
        },
    },
    {
        "name": "query_topology",
        "description": (
            "Search the architecture graph by keyword (and optional node "
            "kind): matched nodes with symbol locations plus their edges."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "node_type": {
                    "type": "string",
                    "description": "Optional kind filter, e.g. function, class, file.",
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "diagnose_workspace",
        "description": (
            "Call before trusting the graph: reports parse health. With a "
            "file_path it checks that specific file (parsed, node count, "
            "warnings); without it returns workspace-wide syntax errors, "
            "skipped files and unmatched API endpoints."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Optional repository-relative file to inspect.",
                },
            },
        },
    },
]


class McpServer:
    """MCP server speaking newline-delimited JSON-RPC 2.0 over stdio."""

    def __init__(self, repo_root: str | Path = ".") -> None:
        self._tools = ArchitectureTools(repo_root)
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "initialize": self._initialize,
            "ping": lambda params: {},
            "tools/list": lambda params: {"tools": TOOL_SPECS},
            "tools/call": self._call_tool,
        }

    def serve_forever(self) -> None:
        """Read JSON-RPC messages line by line until stdin closes."""
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            response = self.process_line(line)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()

    def process_line(self, line: str) -> dict[str, Any] | None:
        """Parse and dispatch one transport line (no response for notifications).

        @shape return: dict | None (JSON-RPC response)
        """
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return self._error(None, _PARSE_ERROR, "parse error")
        if not isinstance(message, dict):
            return self._error(None, _PARSE_ERROR, "parse error")
        return self.handle_message(message)

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC message (shared by stdio and tests)."""
        method = message.get("method")
        request_id = message.get("id")
        is_notification = "id" not in message
        if is_notification:
            return None  # notifications/initialized, notifications/cancelled
        handler = self._handlers.get(method) if isinstance(method, str) else None
        if handler is None:
            return self._error(request_id, _METHOD_NOT_FOUND, f"unknown method: {method}")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return self._error(request_id, _INVALID_PARAMS, "params must be an object")
        try:
            result = handler(params)
        except ValueError as exc:
            return self._error(request_id, _INVALID_PARAMS, str(exc))
        except Exception as exc:  # defensive: a tool bug must not kill the server
            return self._error(request_id, _INTERNAL_ERROR, f"internal error: {exc}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        client_version = params.get("protocolVersion")
        return {
            "protocolVersion": (
                client_version if isinstance(client_version, str) and client_version
                else PROTOCOL_VERSION
            ),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "omni-atlas", "version": SERVER_VERSION},
        }

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        dispatch: dict[str, Callable[..., dict[str, Any]]] = {
            "get_architectural_context": self._tools.architectural_context,
            "check_doc_sync": self._tools.check_doc_sync,
            "query_topology": self._tools.query_topology,
            "diagnose_workspace": self._tools.diagnose_workspace,
        }
        handler = dispatch.get(name)
        if handler is None:
            raise ValueError(f"unknown tool: {name}")
        try:
            payload = handler(**arguments)
        except TypeError as exc:
            raise ValueError(f"invalid tool arguments: {exc}") from exc
        except ValueError:
            raise
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"tool error: {exc}"}],
                "isError": True,
            }
        return {
            "content": [
                {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}
            ],
            "isError": False,
        }

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
