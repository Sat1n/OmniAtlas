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

## Symbol Anchors

### Git Collection Engine (`git_provider.py`)

* Staged file collector: [GitProvider](src/core/git_provider.py#class:GitProvider)
* Incremental boundary collection: [collect_staged_changes](src/core/git_provider.py#function:collect_staged_changes)
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
```
