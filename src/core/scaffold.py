"""Project scaffolding templates for ``omni-atlas init``.

Creates the documentation skeletons that make a fresh project
linter-ready on day one:

* ``BLUEPRINT.md`` — the compact meta-specification (zoom levels, YAML
  contract, symbol anchors, L3 tags, agent rules).
* ``AGENTS.md`` — the L1 panorama template with YAML frontmatter.
* ``<module_dir>/README.md`` — an L2 sub-domain index example.

Creation is strictly non-destructive: existing files are reported as
``exists`` and left byte-identical. Every example anchor lives inside
inline code, so the very first ``omni-atlas check`` passes — scaffolding
never blocks the initial commit.
"""

import re
from dataclasses import dataclass
from pathlib import Path

#: Files the scaffolder can create, in report order.
SCAFFOLD_BLUEPRINT = "BLUEPRINT.md"
SCAFFOLD_AGENTS = "AGENTS.md"


@dataclass
class ScaffoldResult:
    """Outcome of one scaffold file attempt.

    @shape status: str ("created" | "exists")
    """

    path: Path
    status: str


class Scaffolder:
    """Creates missing documentation skeletons without touching existing files."""

    def __init__(self, repo_root: str | Path = ".") -> None:
        self._root = Path(repo_root)

    @property
    def project_title(self) -> str:
        """Human project name derived from the repository directory."""
        name = self._root.resolve().name
        return name or "Project"

    @property
    def project_slug(self) -> str:
        """Snake-case id prefix for frontmatter ids."""
        slug = re.sub(r"[^a-z0-9]+", "_", self.project_title.lower()).strip("_")
        return slug or "project"

    def scaffold(self, module_dir: str = "src") -> list[ScaffoldResult]:
        """Create the three skeleton documents when they are missing.

        @shape return: list[ScaffoldResult]
        """
        targets = [
            (self._root / SCAFFOLD_BLUEPRINT, self._blueprint()),
            (self._root / SCAFFOLD_AGENTS, self._agents()),
            (self._root / module_dir / "README.md", self._l2_readme()),
        ]
        results: list[ScaffoldResult] = []
        for path, content in targets:
            if path.exists():
                results.append(ScaffoldResult(path, "exists"))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            results.append(ScaffoldResult(path, "created"))
        return results

    # -- templates ---------------------------------------------------- #

    def _blueprint(self) -> str:
        return f"""# {self.project_title} Meta-Specification (BLUEPRINT)

**PRIME DIRECTIVE FOR AI AGENTS:** This document is your permanent
context and supreme behavioral blueprint. Adhere strictly to the
navigation protocols, architecture constraints and update mechanisms
below. Do not hallucinate project structure; navigate via Markdown links.

## 1. Core Architecture & Zoom Levels

### L1 - Root Panorama

* **Location:** `AGENTS.md` at the project root.
* **Token Constraint:** < 2000 tokens.
* **Responsibility:** global architecture, tech stack, directory topology.
* **Constraint:** no implementation details; link to L2 indexes only.

### L2 - Sub-Domain Index

* **Location:** `README.md` inside sub-module directories.
* **Token Constraint:** < 4000 tokens.
* **Responsibility:** module design, interface contracts, internal topology.
* **Constraint:** beyond the limit, chunk into sibling documents and link.

### L3 - Physical Implementation

* **Location:** source files.
* **Responsibility:** actual business logic.
* **Constraint:** complex functions document data shapes and upstream
  sources via machine-readable docstring tags (see section 5).

## 2. YAML Data Flow Contract

L1/L2 documents begin with structured YAML frontmatter:

```yaml
---
id: unique_module_id
type: logic_node
inputs: [upstream_id]
outputs: [downstream_id]
tags: [core]
---
```

## 3. Symbol-Level Anchoring

Link to L3 symbols instead of raw files:

* Class: `[Auth](src/auth.py#class:AuthManager)`
* Function: `[Extract](src/ocr.py#function:extract_features)`
* Variable: `[Timeout](src/config.py#var:REQUEST_TIMEOUT)`

## 4. Incremental Update Protocol

Never rewrite whole Markdown files. Replace targeted blocks only; keep
code changes and their documentation in the same commit.

## 5. Machine-Readable L3 Tags

Complex functions carry docstring tags:

* `@shape` — dimensions/types of inputs and outputs.
* `@source` — upstream dependency anchor.

```python
def process(data):
    \"\"\"
    @shape data: [B, C]
    @shape return: [B, F]
    @source data: src/dataloader.py#function:load_batch
    \"\"\"
```

## 6. Execution Rules for AI Agents

1. **Read map first** — `AGENTS.md`, then the relevant L2 index.
2. **Navigate precisely** — use symbol anchors, never blind searches.
3. **Synchronize** — update affected L2 docs in the same work cycle.
4. **Zero documentation rot** — keep code and docs passing the linter.
"""

    def _agents(self) -> str:
        return f"""---
id: {self.project_slug}_root
type: logic_node
inputs: []
outputs: []
tags: [core]
---

# AGENTS.md - {self.project_title} Panorama

## 1. System Overview

<One paragraph: what this project does, who it serves, and which problem
it solves. Keep it free of implementation details.>

## 2. Technology Stack & Rationale

* **Runtime:** <language / version> — <why this choice>.
* **Key libraries:** <libraries> — <why>.
* **Build & CI:** <tooling>.

## 3. Global Architecture & Directory Topology

```
{self.project_title}/
├── AGENTS.md        # This file: L1 panorama
├── BLUEPRINT.md     # Meta-specification (immutable rules)
├── src/             # Primary source tree (L2 index: src/README.md)
└── tests/           # Test suite
```

## 4. Top-Level Data Flow

```
[Input] ──> [Core Pipeline] ──> [Output]
```

## 5. Architectural Rules & Constraints

1. <Hard rule 1 — e.g. layering and dependency direction.>
2. <Hard rule 2 — e.g. IO boundaries or error handling.>
3. **Zero Documentation Rot:** L3 changes and their L2 documentation
   updates must land in the same commit.

## 6. Sub-Domain Indexes

* <Module name>: [module index](src/README.md)
"""

    def _l2_readme(self) -> str:
        return f"""---
id: {self.project_slug}_module
type: logic_node
inputs: []
outputs: []
tags: [module]
---

# <Module Name> Index (L2)

<One paragraph: this module's responsibility, its boundaries, and what
it explicitly does not do.>

## Internal Topology

| File | Responsibility |
|---|---|
| `example.py` | <what this file owns> |

## Symbol Anchors

Replace the placeholder below with real, resolvable anchors (keep the
inline backticks only while the examples are illustrative):

* <Entry point>: `[ExampleClass](src/module/example.py#class:ExampleClass)`

## Data Flow

```
[Input] ──> [ExampleClass] ──> [Output]
```

## Constraints

* <Module-level invariant 1>
* <Module-level invariant 2>
"""
