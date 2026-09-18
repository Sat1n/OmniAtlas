---
id: omni_atlas_core
type: logic_node
inputs: [git_index, markdown_docs]
outputs: [omni_atlas_linter]
tags: [core, incremental, parsing, ast]
---

# Core Module Index (L2)

The `core` package hosts the incremental change collector and the dual
parsing engines of the OmniAtlas Linter. All lint targets originate from
the Git staging area — full-repository scans are architecturally forbidden.

## Internal Topology

| File | Responsibility |
|---|---|
| `git_provider.py` | Incremental Git diff scanning engine (staging area collection) |
| `parser.py` | Markdown anchor extraction, frontmatter parsing, block-safe excerpts, Tree-sitter AST verification & multi-language parser registry (TS/JS, Go, Rust, C/C++) |
| `linter.py` | Bidirectional collision check, token budget guard & informational cross-language API audit |
| `linker.py` | Frontend fetch/axios calls ➔ backend route matcher (Python/Go) producing `api` edges |
| `config.py` | `.omni-atlas.toml` loader with fault-tolerant `[[custom_scm]]` query validation |
| `diagnostics.py` | Diagnostic collector, path sanitizer & workspace health scans (doctor / report-bug) |
| `mcp.py` | Headless MCP server (stdio JSON-RPC) & agent tools (context, doc sync, topology, diagnostics) |
| `installer.py` | One-shot pre-commit hook installer (`omni-atlas init`) |
| `graph.py` | Topology DAG builder, Cytoscape converter & compound container grouping |
| `server.py` | Zero-dependency stdlib web server: dashboard, SSE change stream (`/api/events`), editor launch (`POST /api/open-in-editor`) & IDE detection (`/api/ides`) |

## Symbol Anchors

### Git Collection Engine (`git_provider.py`)

* Staged file collector: [GitProvider](src/core/git_provider.py#class:GitProvider)
* Incremental boundary collection: [collect_staged_changes](src/core/git_provider.py#function:collect_staged_changes)
* Full-project CI sweep: [collect_all_files](src/core/git_provider.py#function:collect_all_files)
* Git modification state overlay: [collect_modified_files](src/core/git_provider.py#function:collect_modified_files)
* Classified staging result: [StagedChanges](src/core/git_provider.py#class:StagedChanges)

### Parsing Engines (`parser.py`)

* Markdown frontmatter & anchor extractor: [MarkdownParser](src/core/parser.py#class:MarkdownParser)
* Tree-sitter AST symbol engine: [PythonASTParser](src/core/parser.py#class:PythonASTParser)
* Multi-language registry: [LanguageRegistry](src/core/parser.py#class:LanguageRegistry)
* Unified per-file extraction: [parse_file](src/core/parser.py#function:parse_file)
* Block-safe excerpt extractor: [excerpt](src/core/parser.py#function:excerpt)
* Extracted anchor record: [SymbolAnchor](src/core/parser.py#class:SymbolAnchor)
* Parsed document record: [MarkdownDoc](src/core/parser.py#class:MarkdownDoc)

### Linter Engine (`linter.py`)

* Bidirectional collision orchestrator: [LinterEngine](src/core/linter.py#class:LinterEngine)
* Reverse sync record: [SyncCheck](src/core/linter.py#class:SyncCheck)
* Token budget record: [TokenCheck](src/core/linter.py#class:TokenCheck)
* Cross-language API audit record: [ApiCheck](src/core/linter.py#class:ApiCheck)

### Cross-Language Linker (`linker.py`)

* Frontend/backend call matcher: [ApiLinker](src/core/linker.py#class:ApiLinker)
* Link assembly entrypoint: [build_links](src/core/linker.py#function:build_links)

### Project Configuration (`config.py`)

* Fault-tolerant config loader: [load_config](src/core/config.py#function:load_config)
* Custom SCM entry record: [CustomScm](src/core/config.py#class:CustomScm)

### MCP Agent Server (`mcp.py`)

* Stdio JSON-RPC MCP server: [McpServer](src/core/mcp.py#class:McpServer)
* Agent tool implementations: [ArchitectureTools](src/core/mcp.py#class:ArchitectureTools)
* Tool catalogue: [TOOL_SPECS](src/core/mcp.py#var:TOOL_SPECS)
* Client config entry (runtime detection): [mcp_server_entry](src/core/mcp.py#function:mcp_server_entry)
* Client config document builder: [build_client_config](src/core/mcp.py#function:build_client_config)
* Safe config merge (preserves siblings): [merge_client_config](src/core/mcp.py#function:merge_client_config)

The MCP server exposes ``get_architectural_context`` (suppliers/consumers/
API mappings/doc anchors for a file), ``check_doc_sync`` (structured
linter report with fix hints), ``query_topology`` (keyword search) and
``diagnose_workspace`` (parse health, skipped files, unmatched API) to
AI coding agents. ``omni-atlas check --json`` and ``omni-atlas graph
--json`` provide the same headless contract on the shell, and
``omni-atlas init-mcp`` generates (or merges, with ``--write``) the
client configuration for Cursor and Claude Desktop.

### Distribution (`scripts/build.py` + `.github/workflows/release.yml`)

``omni-atlas ui`` resolves its static assets through
[sys._MEIPASS](src/core/server.py#function:_ui_dir) when running as a
PyInstaller one-file binary. ``python scripts/build.py`` bundles the
CLI, the dashboard and every Tree-sitter C-extension into
``omni-atlas-<os>-<arch>`` with Windows-console-safe output; pushing a
``v*`` tag runs the GitHub Actions matrix (Linux/macOS/Windows),
smoke-tests every binary (``--help`` / ``doctor`` / ``init-mcp``),
generates a categorized changelog from commit prefixes and attaches all
binaries to the release. The published wheel nests the implementation
as ``omni_atlas.core`` (``omni_atlas/__init__`` registers a ``core``
import alias) while the repository keeps ``src/core`` so documentation
anchors stay stable; ``omni-atlas`` on PyPI installs via
``pip install omni-atlas`` / ``uv tool install omni-atlas``.

### Diagnostics & Health (`diagnostics.py`)

* Event collector (ring buffer): [DiagnosticCollector](src/core/diagnostics.py#class:DiagnosticCollector)
* Path sanitizer (no user dirs in reports): [sanitize_path](src/core/diagnostics.py#function:sanitize_path)
* Workspace health scan: [scan_workspace](src/core/diagnostics.py#function:scan_workspace)
* Single-file parse diagnosis: [diagnose_file](src/core/diagnostics.py#function:diagnose_file)
* Environment / grammar snapshot: [environment_info](src/core/diagnostics.py#function:environment_info)
* Sanitized bug report builder: [build_bug_report](src/core/diagnostics.py#function:build_bug_report)

Parse errors, skipped files, invalid custom SCM queries and unmatched
API routes are collected process-wide (deduplicated) so the silent
failure modes of the multi-language pipeline stay visible. ``omni-atlas
doctor`` renders the health panel; ``omni-atlas report-bug`` emits a
paste-safe Markdown/JSON report with all paths rebased.

### Hook Installer (`installer.py`)

* Pre-commit hook installer: [HookInstaller](src/core/installer.py#class:HookInstaller)
* Idempotent guard injection: [install](src/core/installer.py#function:install)
* Installation outcome record: [InstallResult](src/core/installer.py#class:InstallResult)

### Topology Graph Engine (`graph.py`)

* DAG builder & Cytoscape converter: [TopologyGraphBuilder](src/core/graph.py#class:TopologyGraphBuilder)
* Graph assembly entrypoint: [build](src/core/graph.py#function:build)
* Compound container id: [BLUEPRINT_GROUP_ID](src/core/graph.py#var:BLUEPRINT_GROUP_ID)
* File-node hierarchy: [_ensure_file_node](src/core/graph.py#function:_ensure_file_node)
* Terminal command label formatter: [_external_cli_payload](src/core/graph.py#function:_external_cli_payload)
* Cytoscape element export: [to_dict](src/core/graph.py#function:to_dict)

Anchor edges route through intermediate ``*.py`` file nodes
(doc ➔ file ➔ symbol) so leaf symbols never fan out directly from the
L2 module hub. Unresolved frontmatter ``inputs`` / ``outputs`` are
modeled as ``io_node`` placeholders (⬇ input rhomboid / cyan,
⬆ output tag / pink, ``direction`` field). External command / env /
path references (``git-index#command:...``) become terminal-styled
``external_cli`` nodes — cleaned ``$> git diff --cached`` canvas labels
with the full command preserved in ``full_command`` for the drawer.
Standalone L1 documents are corralled under one compound
container node (``group_blueprints``) so the dashboard renders them
inside a single dashed enclosure (Cytoscape parent/compound node,
emitted before children).

### Web Dashboard Server (`server.py`)

* Stdlib dashboard server: [AtlasWebServer](src/core/server.py#class:AtlasWebServer)
* Headless / SSH detection: [is_headless_environment](src/core/server.py#function:is_headless_environment)
* IDE environment detector: [detect_installed_ides](src/core/server.py#function:detect_installed_ides)
* Remote session probe: [is_remote_session](src/core/server.py#function:is_remote_session)
* Repository change watcher: [_RepoWatcher](src/core/server.py#class:_RepoWatcher)
* Server-side editor launcher: [_open_in_editor](src/core/server.py#function:_open_in_editor)

Every code/doc node carries ``absolute_path`` and (for symbols)
``line_number`` so the dashboard can jump to source in three modes:
server CLI execution (``code --goto path:line``, Remote-SSH friendly),
SSH Remote URI (``ide://vscode-remote/ssh-remote+<host><path>:<line>``)
or the local URI scheme. The SSE stream sends ``Cache-Control: no-cache``
and ``X-Accel-Buffering: no`` (proxy buffering was the push-failure root
cause over SSH tunnels) over HTTP/1.1 with Nagle disabled.
``_RepoWatcher`` polls tracked ``.py`` / ``.md`` mtimes **and** the Git
status signature (~0.8s) — commits/stage/reset change statuses without
touching mtimes — and pushes ``graph_update`` SSE events; the browser
patches the canvas incrementally (diff, no layout recalculation) and
flashes the nodes whose status changed. ``/api/ides`` also reports
``launchers`` (server-side spawnability per editor) and ``remote``
(SSH session), driving the context-aware default jump mode.

## Data Flow

```
[Git Index] ──> [GitProvider] ──(staged .md / .py)──> [main.check]
                                                          │
[Markdown Doc] ──> [MarkdownParser] ──(SymbolAnchor)──> [PythonASTParser]
                                                          │
                                                  [FOUND / NOT FOUND]
                                                          │
[Staged Code] ──> [LinterEngine] ──(reverse scan)──> [IN SYNC / STALE DOC]
                        │
              (token budget)──> [PASS / OVERSIZED]

[Full Project] ──> [collect_all_files] ──(--all CI sweep)──> [main.check]
[omni-atlas init] ──> [HookInstaller] ──> [.git/hooks/pre-commit]

[L1/L2 Docs + L3 AST] ──> [TopologyGraphBuilder] ──(nodes/edges JSON)──> [AtlasWebServer]
[omni-atlas ui] ──> [AtlasWebServer] ──(/api/topology + static HTML)──> [Browser Dashboard]
[File Saves] ──> [_RepoWatcher] ──(SSE graph_update + changed paths)──> [Browser auto-refresh]
[/api/ides] ──> [detect_installed_ides] ──(vscode/cursor/pycharm)──> [Editor Deep Links]
[FE fetch/axios + BE decorators/routes] ──> [ApiLinker] ──(API_CALL edges)──> [TopologyGraphBuilder]
[.omni-atlas.toml] ──> [load_config] ──(validated custom SCM)──> [LanguageRegistry]
[AI Agent / CI] ──> [McpServer / check --json / graph --json] ──> [Headless JSON]
[Parse/Link/Skip events] ──> [DiagnosticCollector] ──> [doctor / report-bug / diagnose_workspace]
```
