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
| `parser.py` | Markdown anchor extraction, frontmatter parsing, block-safe excerpts & Tree-sitter AST symbol verification |
| `linter.py` | Bidirectional collision check & token budget guard |
| `installer.py` | One-shot pre-commit hook installer (`omni-atlas init`) |
| `graph.py` | Topology DAG builder, Cytoscape converter & compound container grouping |
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
* Block-safe excerpt extractor: [excerpt](src/core/parser.py#function:excerpt)
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
