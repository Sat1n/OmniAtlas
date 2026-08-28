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
| `parser.py` | Markdown anchor extraction & Tree-sitter AST symbol verification |
| `linter.py` | Bidirectional collision check & token budget guard |
| `installer.py` | One-shot pre-commit hook installer (`omni-atlas init`) |
| `graph.py` | Topology DAG builder & Cytoscape.js data conversion engine |
| `server.py` | Zero-dependency stdlib web server hosting the dashboard (`omni-atlas ui`) |

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
* Extracted anchor record: [SymbolAnchor](src/core/parser.py#class:SymbolAnchor)
* Parsed document record: [MarkdownDoc](src/core/parser.py#class:MarkdownDoc)

### Linter Engine (`linter.py`)

* Bidirectional collision orchestrator: [LinterEngine](src/core/linter.py#class:LinterEngine)
* Reverse sync record: [SyncCheck](src/core/linter.py#class:SyncCheck)
* Token budget record: [TokenCheck](src/core/linter.py#class:TokenCheck)

### Hook Installer (`installer.py`)

* Pre-commit hook installer: [HookInstaller](src/core/installer.py#class:HookInstaller)
* Idempotent guard injection: [install](src/core/installer.py#function:install)
* Installation outcome record: [InstallResult](src/core/installer.py#class:InstallResult)

### Topology Graph Engine (`graph.py`)

* DAG builder & Cytoscape converter: [TopologyGraphBuilder](src/core/graph.py#class:TopologyGraphBuilder)
* Graph assembly entrypoint: [build](src/core/graph.py#function:build)
* Cytoscape element export: [to_dict](src/core/graph.py#function:to_dict)

### Web Dashboard Server (`server.py`)

* Stdlib dashboard server: [AtlasWebServer](src/core/server.py#class:AtlasWebServer)
* Headless / SSH detection: [is_headless_environment](src/core/server.py#function:is_headless_environment)

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
```
