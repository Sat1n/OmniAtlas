"""Topology graph construction & Cytoscape.js data conversion engine.

Builds a directed project graph from the three BLUEPRINT zoom levels:

* **Lineage edges** — YAML frontmatter ``inputs`` / ``outputs`` link L1/L2
  documents into the global Data Lineage Graph (BLUEPRINT §2).
* **Anchor edges** — symbol-level anchors (BLUEPRINT §3) link documents
  to the L3 classes / functions / variables they describe.
* **Source edges** — ``@source`` machine tags (BLUEPRINT §5) link L3
  symbols to their upstream dependencies.

Node statuses overlay the live Git state captured by
:meth:`core.git_provider.GitProvider.collect_modified_files`:

* ``PASS``     — backing file untouched.
* ``MODIFIED`` — the file backing this node carries uncommitted changes.
* ``STALE``    — a document whose referenced code changed without a
  synchronized documentation update (Fatal Sync Enforcement, AGENTS.md §5).
"""

import json
import re
from pathlib import Path
from typing import Any

from core.git_provider import GitProvider
from core.parser import MarkdownParser, PythonASTParser

NODE_PASS = "PASS"
NODE_MODIFIED = "MODIFIED"
NODE_STALE = "STALE"

#: Internal ``@source`` reference: ``some/path.py#class:Name``.
_SOURCE_REF_RE = re.compile(
    r"(?P<path>[^()\s]+\.py)"
    r"#(?P<type>class|function|var):"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)

#: Drawer snippets are capped so the JSON payload stays light.
_SNIPPET_LIMIT = 600


class TopologyGraphBuilder:
    """Assembles the project topology and converts it to Cytoscape data."""

    def __init__(self, repo_root: str | Path = ".") -> None:
        self._root = Path(repo_root)
        self._git = GitProvider()
        self._md_parser = MarkdownParser()
        self._ast_parser = PythonASTParser()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], None] = {}
        self._id_map: dict[str, str] = {}  # frontmatter id -> node id

    def build(self) -> "TopologyGraphBuilder":
        """Scan the project and populate nodes, edges and statuses.

        @shape return: TopologyGraphBuilder (self, chainable)
        @source docs: src/core/git_provider.py#function:collect_all_files
        @source statuses: src/core/git_provider.py#function:collect_modified_files
        """
        self._nodes.clear()
        self._edges.clear()
        self._id_map.clear()

        docs = self._git.collect_all_files(self._root).doc_files
        parsed = {doc: self._md_parser.parse(doc) for doc in docs}
        for doc in docs:
            self._add_doc_node(doc, parsed[doc])
        for doc in docs:
            self._add_lineage_edges(doc, parsed[doc])
        for doc in docs:
            self._add_anchor_edges(doc, parsed[doc])
        self._add_source_edges()
        self._overlay_statuses()
        return self

    def to_dict(self) -> dict[str, Any]:
        """Export the graph in Cytoscape.js element format.

        @shape return: dict(nodes=[{data}], edges=[{data}])
        """
        return {
            "meta": {
                "root": str(self._root),
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
            },
            "nodes": [{"data": node} for node in self._nodes.values()],
            "edges": [
                {
                    "data": {
                        "id": f"{source}|{target}|{kind}",
                        "source": source,
                        "target": target,
                        "kind": kind,
                    }
                }
                for (source, target, kind) in self._edges
            ],
        }

    def to_json(self) -> str:
        """Serialize :meth:`to_dict` as a UTF-8 JSON string.

        @shape return: str (JSON)
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # Graph assembly
    # ------------------------------------------------------------------ #

    def _add_doc_node(self, doc: str, parsed: Any) -> None:
        """Register one L1/L2 document node and map its frontmatter id."""
        level = "l1_doc" if Path(doc).parent == Path(".") else "l2_doc"
        frontmatter = parsed.frontmatter
        self._add_node(
            doc,
            label=str(frontmatter.get("id") or Path(doc).name),
            kind=level,
            path=doc,
            meta={
                "frontmatter": frontmatter,
                "excerpt": parsed.body.strip()[:_SNIPPET_LIMIT],
            },
        )
        doc_id = frontmatter.get("id")
        if doc_id:
            self._id_map[str(doc_id)] = doc

    def _add_lineage_edges(self, doc: str, parsed: Any) -> None:
        """Wire frontmatter inputs/outputs into the Data Lineage Graph."""
        frontmatter = parsed.frontmatter
        for upstream in self._as_list(frontmatter.get("inputs")):
            self._add_edge(self._lineage_endpoint(upstream), doc, "lineage")
        for downstream in self._as_list(frontmatter.get("outputs")):
            self._add_edge(doc, self._lineage_endpoint(downstream), "lineage")

    def _lineage_endpoint(self, ref: str) -> str:
        """Resolve a lineage id to a doc node, or mint an external node."""
        if ref in self._id_map:
            return self._id_map[ref]
        node_id = f"ext:{ref}"
        self._add_node(node_id, label=ref, kind="external", path=None, meta={})
        return node_id

    def _add_anchor_edges(self, doc: str, parsed: Any) -> None:
        """Link the document to every AST-verified symbol anchor."""
        for anchor in parsed.anchors:
            symbol_id = (
                f"{anchor.file_path}#{anchor.symbol_type}:{anchor.symbol_name}"
            )
            if symbol_id not in self._nodes:
                lookup = self._ast_parser.lookup(
                    anchor.file_path, anchor.symbol_type, anchor.symbol_name
                )
                meta: dict[str, Any] = {
                    "line": lookup.line,
                    "shapes": lookup.shapes,
                    "sources": lookup.sources,
                    "docstring": (lookup.docstring or "")[:_SNIPPET_LIMIT],
                }
                if not lookup.found:
                    # Broken anchor = documentation rot: surface it as STALE.
                    meta["broken"] = True
                self._add_node(
                    symbol_id,
                    label=anchor.symbol_name,
                    kind=anchor.symbol_type,
                    path=anchor.file_path,
                    meta=meta,
                )
            self._add_edge(doc, symbol_id, "anchor")

    def _add_source_edges(self) -> None:
        """Wire ``@source`` machine tags between L3 symbols."""
        for node in list(self._nodes.values()):
            for source in node.get("meta", {}).get("sources", []):
                upstream = self._resolve_source_ref(source)
                if upstream and upstream != node["id"]:
                    self._add_edge(upstream, node["id"], "source")

    def _resolve_source_ref(self, source: str) -> str | None:
        """Resolve one ``@source`` body into a node id.

        Internal references (``path.py#type:name``) are tried as-is and
        with a ``src/`` prefix (docstrings may use either convention).
        Anything else (e.g. ``git-index#command:...``) becomes an
        external data-source node.

        @shape return: str | None (node id)
        """
        match = _SOURCE_REF_RE.search(source)
        if match:
            candidates = [match.group("path"), f"src/{match.group('path')}"]
            for path in candidates:
                symbol_id = (
                    f"{path}#{match.group('type')}:{match.group('name')}"
                )
                if symbol_id in self._nodes:
                    return symbol_id
            for path in candidates:
                if (self._root / path).is_file():
                    lookup = self._ast_parser.lookup(
                        path, match.group("type"), match.group("name")
                    )
                    if lookup.found:
                        self._add_node(
                            symbol_id,
                            label=match.group("name"),
                            kind=match.group("type"),
                            path=path,
                            meta={
                                "line": lookup.line,
                                "shapes": lookup.shapes,
                                "sources": lookup.sources,
                                "docstring": (lookup.docstring or "")[
                                    :_SNIPPET_LIMIT
                                ],
                            },
                        )
                        return symbol_id
            return None

        key = source.strip()[:80]
        node_id = f"ext:{key}"
        self._add_node(node_id, label=key, kind="external", path=None, meta={})
        return node_id

    def _overlay_statuses(self) -> None:
        """Stamp PASS / MODIFIED / STALE onto every node from the Git state."""
        modified = set(self._git.collect_modified_files(self._root))

        modified_code: set[str] = set()
        for node in self._nodes.values():
            path = node.get("path")
            if path in modified and node["kind"] in ("class", "function", "var"):
                modified_code.add(path)

        for node in self._nodes.values():
            node["status"] = NODE_PASS
            path = node.get("path")
            kind = node["kind"]
            if kind == "external":
                continue
            if node.get("meta", {}).get("broken"):
                node["status"] = NODE_STALE
            elif path in modified:
                node["status"] = NODE_MODIFIED
            elif kind in ("l1_doc", "l2_doc"):
                # Doc untouched while code it anchors changed → stale.
                targets = {
                    edge[1].rsplit("#", 1)[0]
                    for edge in self._edges
                    if edge[0] == node["id"] and edge[2] == "anchor"
                }
                if targets & modified_code:
                    node["status"] = NODE_STALE

    # ------------------------------------------------------------------ #
    # Low-level helpers
    # ------------------------------------------------------------------ #

    def _add_node(
        self,
        node_id: str,
        *,
        label: str,
        kind: str,
        path: str | None,
        meta: dict[str, Any],
    ) -> None:
        if node_id in self._nodes:
            return
        self._nodes[node_id] = {
            "id": node_id,
            "label": label,
            "kind": kind,
            "status": NODE_PASS,
            "path": path,
            "meta": meta,
        }

    def _add_edge(self, source: str, target: str, kind: str) -> None:
        if source == target:
            return
        self._edges.setdefault((source, target, kind), None)

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        """Normalize a frontmatter value into a list of string ids."""
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []
