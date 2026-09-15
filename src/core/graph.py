"""Topology graph construction & Cytoscape.js data conversion engine.

Builds a directed project graph from the three BLUEPRINT zoom levels:

* **Lineage edges** — YAML frontmatter ``inputs`` / ``outputs`` link L1/L2
  documents into the global Data Lineage Graph (BLUEPRINT §2). Unresolved
  endpoints are modeled as ``io_node`` data placeholders carrying a
  ``direction`` (``input`` / ``output``).
* **Anchor edges** — symbol-level anchors (BLUEPRINT §3) are re-routed
  through intermediate ``*.py`` file nodes (doc ➔ file ➔ symbol) so
  L3 symbols never fan out directly from the L2 module hub.
* **Source edges** — ``@source`` machine tags (BLUEPRINT §5) link L3
  symbols to their upstream dependencies. External command / env / path
  references become terminal-styled ``external_cli`` nodes with cleaned
  ``$> ...`` labels and the full command kept in ``full_command``.

Node statuses overlay the live Git state captured by
:meth:`core.git_provider.GitProvider.collect_modified_files`:

* ``PASS``     — backing file untouched.
* ``MODIFIED`` — the file backing this node carries uncommitted changes.
* ``STALE``    — a document whose referenced code changed without a
  synchronized documentation update (Fatal Sync Enforcement, AGENTS.md §5).

Standalone L1 documents (root ``AGENTS.md`` / ``BLUEPRINT.md`` /
``README.md``) are additionally corralled under a single compound
container node (``group_blueprints``) so the dashboard renders them
inside one shared dashed enclosure (Cytoscape parent/compound node).
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

#: Compound container that corrals standalone L1 blueprint / doc nodes.
BLUEPRINT_GROUP_ID = "group_blueprints"
BLUEPRINT_GROUP_LABEL = "Blueprint & Top Docs"

#: Internal ``@source`` reference: ``some/path.py#class:Name``.
_SOURCE_REF_RE = re.compile(
    r"(?P<path>[^()\s]+\.py)"
    r"#(?P<type>class|function|var):"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)

#: External command / environment / filesystem reference, optionally
#: prefixed by the tag's left-hand name (``stdout:`` / ``code_files:``):
#: ``git-index#command:diff --cached --name-only``.
_CLI_REF_RE = re.compile(
    r"(?P<name>[A-Za-z0-9][\w-]*)"
    r"#(?P<proto>command|var|path)\s*:\s*"
    r"(?P<body>.+)"
)

#: Protocol prefixes stripped from fallback external labels.
_EXTERN_PREFIX_RE = re.compile(r"^[A-Za-z_][\w]*\s*:\s*")

#: Drawer snippets are capped so the JSON payload stays light; block-safe
#: cutting (MarkdownDoc.excerpt) may land slightly below this ceiling.
_SNIPPET_LIMIT = 1400


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
        self._add_blueprint_group()
        return self

    def to_dict(self) -> dict[str, Any]:
        """Export the graph in Cytoscape.js element format.

        Compound container nodes are emitted before their children so a
        parent is always defined before the nodes referencing it.

        @shape return: dict(nodes=[{data}], edges=[{data}])
        """
        nodes = self._ordered_nodes()
        return {
            "meta": {
                "root": str(self._root),
                "node_count": len(nodes),
                "edge_count": len(self._edges),
            },
            "nodes": [{"data": node} for node in nodes],
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
                "excerpt": parsed.excerpt(_SNIPPET_LIMIT),
            },
        )
        doc_id = frontmatter.get("id")
        if doc_id:
            self._id_map[str(doc_id)] = doc

    def _add_lineage_edges(self, doc: str, parsed: Any) -> None:
        """Wire frontmatter inputs/outputs into the Data Lineage Graph."""
        frontmatter = parsed.frontmatter
        for upstream in self._as_list(frontmatter.get("inputs")):
            endpoint = self._lineage_endpoint(upstream, direction="input")
            self._add_edge(endpoint, doc, "lineage")
        for downstream in self._as_list(frontmatter.get("outputs")):
            endpoint = self._lineage_endpoint(downstream, direction="output")
            self._add_edge(doc, endpoint, "lineage")

    def _lineage_endpoint(self, ref: str, direction: str) -> str:
        """Resolve a lineage id to a doc node, or mint an io data node.

        Unresolved references become ``io_node`` placeholders stamped
        with their flow ``direction`` so the dashboard can render them
        as shape- and color-coded markers (⬇ input rhomboid / cyan,
        ⬆ output tag / pink).

        @shape ref: str (frontmatter inputs/outputs id)
        @shape return: str (node id)
        @source frontmatter: src/core/parser.py#function:_split_frontmatter
        """
        if ref in self._id_map:
            return self._id_map[ref]
        node_id = f"io:{ref}"
        self._add_node(
            node_id,
            label=ref,
            kind="io_node",
            path=None,
            meta={},
            direction=direction,
        )
        return node_id

    def _add_anchor_edges(self, doc: str, parsed: Any) -> None:
        """Route anchors through file nodes: doc ➔ ``*.py`` ➔ symbol.

        Inserting the intermediate L2 file node decouples the module hub
        from its leaf symbols, turning the direct doc-to-symbol
        "sunflower" fan-out into a readable two-level flow.

        @shape parsed: MarkdownDoc (anchors verified against the AST)
        @source anchors: src/core/parser.py#class:MarkdownParser
        """
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
            file_id = self._ensure_file_node(anchor.file_path)
            self._add_edge(doc, file_id, "anchor")
            self._add_edge(file_id, symbol_id, "contains")

    def _ensure_file_node(self, file_path: str) -> str:
        """Return the L2 file node id for ``file_path``, minting it if new.

        @shape return: str (node id, equal to the relative file path)
        @source callers: src/core/graph.py#function:_add_anchor_edges
        """
        if file_path not in self._nodes:
            self._add_node(
                file_path,
                label=Path(file_path).name,
                kind="file",
                path=file_path,
                meta={},
            )
        return file_path

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
        External command / environment references become terminal-styled
        ``external_cli`` nodes (label cleaned to ``$> git diff --cached``
        form, full command preserved for the drawer); anything else is a
        generic external data-source node.

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
                        self._add_edge(
                            self._ensure_file_node(path), symbol_id, "contains"
                        )
                        return symbol_id
            return None

        cli_match = _CLI_REF_RE.search(source)
        if cli_match:
            node_id = (
                f"ext:{cli_match.group('name')}#{cli_match.group('proto')}:"
                f"{' '.join(cli_match.group('body').split())}"
            )
        else:
            stripped = _EXTERN_PREFIX_RE.sub("", source.strip())
            node_id = f"ext:{stripped[:80] or source.strip()[:80]}"
        cli = self._external_cli_payload(source)
        if cli:
            self._add_node(
                node_id,
                label=cli["label"],
                kind="external_cli",
                path=None,
                meta={},
            )
            node = self._nodes[node_id]
            node["full_label"] = cli["full_label"]
            node["full_command"] = cli["full_command"]
            node["cli_proto"] = cli["proto"]
        else:
            key = _EXTERN_PREFIX_RE.sub("", source.strip())[:80] or source.strip()[:80]
            self._add_node(node_id, label=key, kind="external", path=None, meta={})
        return node_id

    def _external_cli_payload(self, source: str) -> dict[str, str] | None:
        """Format an external command/env/path reference for the canvas.

        ``stdout: git-index#command:diff --cached --name-only`` becomes
        the short label ``$> git diff --cached`` while the complete
        command survives in ``full_command`` / ``full_label`` for the
        drawer and tooltip. Refs differing only in their tag left-hand
        name (``stdout:`` vs ``code_files:``) collapse onto one node.

        @shape return: dict | None (label, full_label, full_command, proto)
        @source regex: src/core/graph.py#var:_CLI_REF_RE
        """
        match = _CLI_REF_RE.search(source)
        if not match:
            return None
        name = match.group("name")
        proto = match.group("proto")
        body = " ".join(match.group("body").split())
        short = re.split(r"[-_]", name, maxsplit=1)[0]
        if proto == "command":
            tokens = body.split()
            if not tokens:
                return None
            return {
                "label": f"$> {short} {' '.join(tokens[:2])}",
                "full_label": f"$> {short} {' '.join(tokens)}",
                "full_command": f"{short} {' '.join(tokens)}",
                "proto": "command",
            }
        if proto == "var":
            variables = [v.strip() for v in body.split("|") if v.strip()]
            if not variables:
                return None
            return {
                "label": f"${variables[0]}" + ("…" if len(variables) > 1 else ""),
                "full_label": " ".join(f"${v}" for v in variables),
                "full_command": " ".join(variables),
                "proto": "var",
            }
        if proto == "path":
            target = body.split()[0] if body.split() else body
            return {
                "label": f"⌂ {target}",
                "full_label": f"⌂ {body}",
                "full_command": target,
                "proto": "path",
            }
        return None

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
            if kind in ("external", "external_cli", "io_node"):
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

    def _add_blueprint_group(self) -> None:
        """Corral standalone L1 documents into one compound container.

        Root-level documents (``AGENTS.md`` / ``BLUEPRINT.md`` /
        ``README.md``) are visually grouped by a Cytoscape parent node
        instead of floating as orphans on the canvas.

        @shape return: None (mutates self._nodes in place)
        @source levels: src/core/graph.py#function:_add_doc_node
        """
        members = [
            node for node in self._nodes.values() if node["kind"] == "l1_doc"
        ]
        if not members:
            return
        self._nodes[BLUEPRINT_GROUP_ID] = {
            "id": BLUEPRINT_GROUP_ID,
            "label": BLUEPRINT_GROUP_LABEL,
            "kind": "group",
            "status": NODE_PASS,
            "path": None,
            "meta": {"members": len(members)},
        }
        for node in members:
            node["parent"] = BLUEPRINT_GROUP_ID

    def _ordered_nodes(self) -> list[dict[str, Any]]:
        """Return nodes with compound containers first (parent-before-child)."""
        groups = [n for n in self._nodes.values() if n["kind"] == "group"]
        singles = [n for n in self._nodes.values() if n["kind"] != "group"]
        return groups + singles

    def _add_node(
        self,
        node_id: str,
        *,
        label: str,
        kind: str,
        path: str | None,
        meta: dict[str, Any],
        direction: str | None = None,
    ) -> None:
        if node_id in self._nodes:
            return
        node = {
            "id": node_id,
            "label": label,
            "kind": kind,
            "status": NODE_PASS,
            "path": path,
            "meta": meta,
        }
        if direction is not None:
            node["direction"] = direction
        self._nodes[node_id] = node

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
