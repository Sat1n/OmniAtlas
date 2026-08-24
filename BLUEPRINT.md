# OmniAtlas Meta-Specification (BLUEPRINT)

**PRIME DIRECTIVE FOR AI AGENTS:** This document serves as your permanent context and supreme behavioral blueprint. You are operating within the OmniAtlas framework. You must strictly adhere to the navigation protocols, architecture constraints, and update mechanisms defined below. Do not hallucinate or guess project structures; navigate strictly via the provided Markdown links.

## 1. Core Architecture & Zoom Levels

The context is divided into three zoom levels to manage token limits and abstract complexity.

### L1 - Root Panorama

* **Location:** `AGENTS.md` at the project root.
* **Token Constraint:** < 2000 Tokens.
* **Responsibility:** Defines global architecture, tech stack, and top-level directory topology.
* **Constraint:** Strictly forbids physical code implementation details. Only permitted to link to L2 domain indexes.

### L2 - Sub-Domain Index

* **Location:** `README.md` within sub-module directories (e.g., `src/module_name/README.md`).
* **Token Constraint:** < 4000 Tokens.
* **Responsibility:** Explains module-specific design, interface contracts, and internal topology.
* **Constraint:** If the module grows beyond limits, you must perform **Chunking** by creating sibling Markdown files (e.g., `sub_concept.md`) and linking to them.

### L3 - Physical Implementation

* **Location:** Source code files (`.py`, `.ts`, etc.).
* **Responsibility:** Actual business logic execution.
* **Constraint:** Specialized or complex functions must utilize machine-readable tags in their docstrings to specify data shapes and upstream sources.

## 2. YAML Data Flow Contract

All L1 and L2 Markdown files must begin with structured YAML frontmatter. This allows external tools to automatically render the global Data Lineage Graph.

**Standard Format:**

```yaml
---
id: unique_module_id
type: logic_node | data_source | interface
inputs: [upstream_id_1, upstream_id_2]
outputs: [downstream_id_1]
tags: [core, ai_model, etc]
---

```

## 3. Symbol-Level Anchoring

When linking to L3 source files from L1 or L2 documents, do not link to the raw file. You must use AST-compatible anchors to ensure downstream tools can extract precise code blocks without parsing the entire file.

**Protocols:**

* **Classes:** `[Authentication](src/auth.py#class:AuthManager)`
* **Functions:** `[Feature Extraction](src/perception/ocr.py#function:extract_features)`
* **Variables:** `[Config](src/config.py#var:GLOBAL_TIMEOUT)`

## 4. Incremental Update Protocol

To prevent formatting corruption and context loss during documentation updates, you are forbidden from rewriting entire Markdown files. You must use block-replace updates.

Documents utilize HTML comments to demarcate update boundaries:

```markdown
Module A depends on Module B.

```

**Constraint:** When updating documentation, you must target specific blocks and only provide the replacement content for that target block.

## 5. Machine-Readable L3 Tags

To ensure accurate upward reflection of L3 data into L2 documentation, complex functions must include the following tags in their docstrings:

* `@shape`: Defines the dimensions/types of inputs and outputs.
* `@source`: Explicitly defines the upstream dependency anchor.

**Example:**

```python
def process_tensor(image_tensor):
    """
    Extracts key features from the image tensor.
    
    @shape image_tensor: [B, C, H, W]
    @shape return: [B, F]
    @source image_tensor: src/dataloader.py#function:load_batch
    """
    pass

```

## 6. Execution Rules for AI Agents

1. **Read Map First:** Before executing any code modification, you must read `AGENTS.md` and trace the links to the relevant L2 module.
2. **Precision Navigation:** Utilize Symbol-Level Anchors to jump directly to L3 code blocks. Do not blindly search irrelevant files.
3. **Synchronize:** After modifying L3 code, you must update the affected L2 documentation using the Incremental Update Protocol.
4. **Zero Documentation Rot:** Code modifications and documentation updates must occur within the same cycle to pass the OmniAtlas Linter.
