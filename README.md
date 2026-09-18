---
id: omni_atlas_readme
type: interface
inputs: []
outputs: []
tags: [docs, cli, mcp, visualization]
---

# OmniAtlas 🌍

> **Code topology & AST dependency graph, out of the box.**

[English] | [简体中文](README_ZH.md)

OmniAtlas is an open-source static-analysis toolchain that keeps
documentation and code in lockstep, then renders the whole project as an
interactive dependency graph for humans and AI agents.

* **Lightweight web dashboard** — `omni-atlas ui` serves a single-file
  Cytoscape topology (docs, files, symbols, I/O nodes, cross-language
  API calls) with live SSE updates from a zero-dependency stdlib server.
* **Full rule validation** — `omni-atlas check` verifies symbol-level
  Markdown anchors against the real Tree-sitter AST, enforces fatal
  doc-sync and token budgets, and audits frontend ➔ backend API links.
* **Native MCP service** — `omni-atlas mcp` speaks the Anthropic Model
  Context Protocol, letting coding agents query architecture, doc sync
  and diagnostics directly.

## ✨ Features

* **Zero-dependency single binary** — UI assets and 7+ Tree-sitter C
  extensions (Python, TypeScript/JS, Go, Rust, C, C++, HTML) are
  embedded; no Python runtime required.
* **Native MCP integration** — one command wires OmniAtlas into Cursor
  or Claude Desktop (`init-mcp`), merging configs safely.
* **Incremental & private** — Git-diff-scoped checks, a deduplicated
  ring-buffer watcher (mtime + git state), and automatic path
  sanitization so reports never leak user directories.
* **Multi-language API linking** — `fetch`/axios/EventSource calls are
  matched to FastAPI/Flask/Gin/stdlib routes as `API_CALL` edges.
* **Custom SCM queries** — extend symbol extraction via
  `.omni-atlas.toml` with inline or file-based Tree-sitter queries.
* **Snapshots & focus** — export SVG/PNG/JSON, import snapshots for
  offline diffing, isolate dependency cones in the dashboard.

## 🚀 Quick Start

```bash
uv tool install omni-atlas
# or
pip install omni-atlas
```

Single-file binaries (no Python needed) are published on the
[GitHub Releases](https://github.com/Sat1n/OmniAtlas/releases) page for
Linux x64, macOS arm64 and Windows x64 — download, `chmod +x`, run.

## 🤖 MCP Integration

```bash
omni-atlas init-mcp --target cursor --write
omni-atlas init-mcp --target claude --write
```

`--write` merges into `.cursor/mcp.json` or the Claude Desktop config
while preserving other MCP servers; without it the JSON is printed to
stdout. Four agent tools are exposed: `get_architectural_context`,
`check_doc_sync`, `query_topology` and `diagnose_workspace`.

Tagged releases (`v*`) build the same one-file binaries for
Linux/macOS/Windows via GitHub Actions and smoke-test each one before
publishing.

## 🧭 CLI Cheatsheet

| Command | Purpose |
|---|---|
| `omni-atlas check --all` | Full-repo dependency & rule validation (CI mode) |
| `omni-atlas ui` | Launch the local topology dashboard |
| `omni-atlas doctor` | Diagnose environment & Tree-sitter C extensions |
| `omni-atlas mcp` | Run the MCP server (stdio JSON-RPC) |
| `omni-atlas graph --json` | Export the topology as machine-readable JSON |
| `omni-atlas report-bug` | Sanitized Markdown/JSON diagnostic report |

## 🗺️ Documentation Map

* `BLUEPRINT.md` — the meta-specification (L1/L2/L3 zoom rules).
* `AGENTS.md` — global architecture panorama.
* `src/core/README.md` — core module index (L2).
* `tests/` — pytest suite and multi-language fixtures.

## 🛠️ Development

```bash
uv sync                    # install with dev dependencies
uv run pytest              # run the test suite
python scripts/build.py    # one-file binary for the current platform
```
