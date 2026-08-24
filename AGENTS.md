# AGENTS.md - OmniAtlas Linter Project Panorama

## id: omni_atlas_root
type: logic_node
inputs: []
outputs: []
tags: [core, infrastructure, linter]

## 1. System Overview

This repository contains the core implementation of the OmniAtlas Linter toolchain (`omni-atlas`). The project is a lightweight, high-performance static analysis CLI designed to enforce the OmniAtlas Meta-Specification (`BLUEPRINT.md`). It prevents documentation rot and model hallucination by dynamically validating symbol-level anchors against the project's actual Abstract Syntax Tree (AST) using incremental Git analysis.

## 2. Technology Stack & Rationale

* **Runtime:** Python 3.12 (Selected for optimal stability, robust type-hinting features, and mature pre-compiled binary wheel support for C-extensions).
* **Package Management:** `uv` (Fast, reliable, rust-backed project initialization and dependency management).
* **CLI Framework:** `typer` (Type-hint driven command-line interface generation).
* **AST Parser Engine:** `tree-sitter` & `tree-sitter-python` (C-optimized, highly incremental syntax tree parser ensuring microsecond-level symbol verification and fault-tolerant parsing).
* **Terminal UI:** `rich` (For high-fidelity, aesthetic terminal reporting and structural error visualization).

## 3. Global Architecture & Directory Topology

```
omni-atlas/
├── BLUEPRINT.md          # Supreme framework meta-specification (Immutable Rule)
├── AGENTS.md             # This file (Global project architecture panorama)
├── pyproject.toml        # Project metadata and uv dependency locks
└── src/
    ├── main.py           # CLI Entrypoint & command routing (Typer)
    ├── core/
    │   ├── git_provider.py # Incremental Git diff scanning engine
    │   ├── parser.py       # Markdown anchor extraction & Tree-sitter AST parsing
    │   └── linter.py       # Core double-verification engine (Collision logic)
    └── utils/
        └── reporter.py     # Rich-text console output formatting & error stack display

```

## 4. Top-Level Data Flow

```
[Git Diff] ──(Changed Files)──> [git_provider.py]
                                      │
                               (Target Filters)
                                      ▼
[BLUEPRINT.md / AGENTS.md] ───> [parser.py] <─── [Tree-sitter AST]
                                      │
                               (Extracted Anchors)
                                      ▼
                                [linter.py] (Collision & Ceiling Check)
                                      │
                                (Exit Status)
                                      ▼
                               [reporter.py] ───> Terminal Output (0 or 1)

```

## 5. Architectural Rules & Constraints

1. **Strict Incrementalism:** The system must never invoke full-repository scans during `check` routines. All targets must be derived strictly from `git diff` boundaries to maintain $O(\Delta)$ operational complexity.
2. **No Full-File Reads for Validation:** When checking L3 implementations, `parser.py` must only load the precise boundaries of the symbol requested via AST queries to prevent token pollution.
3. **Fatal Sync Enforcement:** If a code file is altered, any corresponding Markdown document containing its anchors must be present in the same git staging area (`git diff --cached`). Failure to synchronize will result in a hard execution halt (Exit Code 1).