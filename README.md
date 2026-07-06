# OmniAtlas 🌍

> **Universal Knowledge Mapping & Navigation Framework for Humans and AI Agents.**

[English] | [简体中文](README_ZH.md)

---

## 💡 What is OmniAtlas?

As AI Agents transition into complex, real-world applications—whether navigating tens of thousands of lines of code, tracing corporate financial pipelines, or retrieving semantic context from massive heterogeneous datasets—we face critical systemic bottlenecks: **Context Fragmentation**, **Long-Context Forgetting (Lost in the Middle)**, and **massive token wastage caused by blind file wandering/traversal**.

**OmniAtlas** is a lightweight, open-source knowledge organization specification and toolchain. It does not bind you to any proprietary platform or introduce heavy graph databases. Instead, using standard **Markdown relative links** and **structured YAML frontmatter**, it implicitly weaves isolated resources—source code, invoices, asset vouchers, or business docs—into a highly efficient **Knowledge Mesh**.

Designed as a shared *lingua franca* for both humans and AI Agents, OmniAtlas equips small language models (such as 8B/70B parameter variants) with a high-definition, lightweight "GPS map," allowing them to instantly locate and stream-consume any localized context in massive projects.

---

## 🎯 Core Design Goals

* **Clean & Intuitive (Detail Hiding)**: High-level documentation focuses strictly on abstraction and connectivity, offloading all raw implementation details back to the physical source files.
* **Extreme Token Efficiency**: Enforces strict token and character ceilings for each documentation layer, enabling progressive chunk-based consumption tailored for smaller LLMs.
* **High-Cohesion, Clone-and-Go**: The entire meta-specification operates through a single config blueprint that serves as the Agent's code of conduct and native skill, enabling drop-in replication for any new project.
* **Bi-Directional Transparency**: Human developers can instantly audit data flows and logical topologies, while AI Agent behaviors and operations become completely observable to humans.

---

## 🗺️ Navigation Architecture: Progressive Zoom Levels

OmniAtlas utilizes a rigorous layered abstraction design, allowing multi-tiered zooming just like a high-precision satellite map:

* **`BLUEPRINT.md` (Meta-Specification)**: The constitution and operational standard of the project. It explicitly defines the documentation layers (L1, L2, L3), naming conventions, and link-graph protocols.
* **L1 — `AGENTS.md` (Global Panorama - Root)**: High-level system architecture, core technology/business domains, hard constraints, and foundational design rationales.
* **L2 — `[Module].md` (Sub-domain Index - Sub-folder)**: Index documents named after their respective directories (e.g., `utils.md`, `finance.md`). They define the architecture of specific modules and link downward progressively to prevent document bloat.
* **L3 — Node-Level Detail (Source Files & Comments)**: Deep dive into physical source files. Specialized entities (e.g., deep learning custom environments, complex functional pipelines) must explicitly document input/output shapes, upstream dependencies, and exact semantic definitions.

---

## 🛠️ The Engineering Trio: Eradicating "Documentation Rot"

To ensure the architecture remains production-grade, OmniAtlas provides an automation CLI built around three core mechanisms:

1. **Documentation Linter (Anti-Rot Checking)**: A lightweight static analysis tool that runs in CI/CD or pre-commit hooks. It guarantees link integrity and ensures entity/signature updates stay synchronized, strictly blocking outdated documentation from misleading the Agent.
2. **Symbol-Level Precision Anchoring**: Supports precise anchor protocols such as `[Auth Node](src/auth.py#class:AuthManager)`. By leveraging Abstract Syntax Trees (AST), the downstream tool chain anchors the Agent directly to the exact lines of code, eliminating token-heavy file scanning.
3. **Incremental Update Protocol**: Utilizes clear Markdown comment blocks (e.g., ``) to demarcate atomic sections. Agents perform append-only or block-replace routines, keeping the map updated without rewriting entire files—drastically reducing small model hallucinations.

---

## 🔮 Future Evolution

* 📊 **Automated Data Flow Visualization**: Automatically rendering end-to-end Data Lineage Graphs compiled straight from YAML frontmatter tags.
* 🕵️‍♂️ **"Librarian" Agent**: A dedicated, ultra-lightweight routing agent that traces dependency pipelines and call-chains instantly using only the OmniAtlas mesh, without ever reading the heavy underlying source files.