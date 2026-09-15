"""Markdown anchor extraction & Tree-sitter AST parsing.

Two engines live in this module:

* :class:`MarkdownParser` — extracts YAML frontmatter and symbol-level
  anchors (``[Title](path.py#class:Name)``) from L1/L2 documents.
* :class:`PythonASTParser` — verifies anchors against the real AST via
  Tree-sitter and extracts ``@shape`` / ``@source`` machine-readable tags.

Per AGENTS.md, validation never reads full files for L3 checks; the AST
engine only loads the precise boundaries of the requested symbol.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())

#: ``[Title](path/file.py#symbol_type:SymbolName)`` — BLUEPRINT §3 protocols.
ANCHOR_RE = re.compile(
    r"\[(?P<title>[^\]]+)\]"
    r"\((?P<path>[^()#\s]+\.py)"
    r"#(?P<type>class|function|var):"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\)"
)

#: YAML frontmatter delimited by ``---`` fences at the very top of a doc.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

#: Machine-readable L3 tags (BLUEPRINT §5).
_TAG_RE = re.compile(r"^\s*@(shape|source)\s+(?P<body>.+?)\s*$", re.MULTILINE)

#: Inline code spans — stripped before anchor extraction so that example
#: links inside `` `...` `` are never treated as real anchors.
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

#: CJK characters map ~1:1 onto tokens; latin runs map ~1 word per token.
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:['-][A-Za-z0-9_]+)*")

#: tree-sitter node type per anchor symbol type.
_NODE_TYPE = {"class": "class_definition", "function": "function_definition"}


@dataclass
class SymbolAnchor:
    """A single symbol-level anchor extracted from a Markdown document."""

    title: str
    file_path: str
    symbol_type: str  # "class" | "function" | "var"
    symbol_name: str


@dataclass
class MarkdownDoc:
    """A parsed L1/L2 document: frontmatter data + extracted anchors."""

    path: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    anchors: list[SymbolAnchor] = field(default_factory=list)
    body: str = ""

    def estimate_tokens(self) -> int:
        """Estimate the token footprint of the document body.

        CJK characters count one token each; latin/digit runs count as
        one token per word. Code fences are included — they still cost
        context window budget (BLUEPRINT §1 token constraints).

        @shape return: int
        @source body: src/core/parser.py#class:MarkdownParser
        """
        return len(_CJK_RE.findall(self.body)) + len(_WORD_RE.findall(self.body))

    def excerpt(self, limit: int = 600) -> str:
        """Return a syntax-safe prefix of the document body.

        Cutting is block-aware (BLUEPRINT §4 spirit — never corrupt
        structure): the slice always ends on a complete line, pipe
        tables are kept structurally intact (header + separator row
        never split), and an unterminated fenced code block is closed
        again so downstream renderers never see broken syntax.

        @shape limit: int (maximum characters)
        @shape return: str (markdown-safe excerpt)
        @source body: src/core/parser.py#class:MarkdownParser
        """
        text = self.body.strip()
        if len(text) <= limit:
            return text

        kept: list[str] = []
        used = 0
        for block in _split_blocks(text.splitlines()):
            block_size = sum(len(line) + 1 for line in block)
            if used + block_size <= limit:
                kept.extend(block)
                used += block_size
                continue
            if not kept:
                # Pathological case: even the first block is oversized.
                kept = _fit_first_block(block, limit)
                break
            if block[0].lstrip().startswith(("```", "~~~", "|")):
                break  # never split a table / fence mid-block
            for line in block:  # paragraph: fill complete lines only
                if used + len(line) + 1 > limit:
                    break
                kept.append(line)
                used += len(line) + 1
            break

        if kept:
            return "\n".join(kept).rstrip()
        if text.startswith("|"):
            return ""  # cutting a giant single table would break syntax
        return text[:limit].rsplit(" ", 1)[0].rstrip()


@dataclass
class SymbolLookup:
    """Outcome of verifying one anchor against the Python AST."""

    found: bool
    line: int | None = None
    docstring: str | None = None
    shapes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


class MarkdownParser:
    """Extracts YAML frontmatter and symbol anchors from Markdown files."""

    def parse(self, doc_path: str | Path) -> MarkdownDoc:
        """Parse a Markdown document into a :class:`MarkdownDoc`.

        Missing or unreadable files degrade gracefully to an empty
        document instead of raising.

        @shape return: MarkdownDoc(frontmatter, anchors)
        @source doc_path: git-index#command:diff --cached --name-only
        """
        path = Path(doc_path)
        doc = MarkdownDoc(path=str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return doc

        doc.frontmatter, body = _split_frontmatter(text)
        doc.body = body
        # Example anchors inside fenced/inline code are documentation,
        # not real links — strip those regions before extraction.
        for match in ANCHOR_RE.finditer(_strip_code_regions(body)):
            doc.anchors.append(
                SymbolAnchor(
                    title=match.group("title"),
                    file_path=match.group("path"),
                    symbol_type=match.group("type"),
                    symbol_name=match.group("name"),
                )
            )
        return doc


class PythonASTParser:
    """Verifies symbol anchors via Tree-sitter AST queries.

    Parsing is fault-tolerant: files with syntax errors still yield a
    partial tree, and lookup simply reports the symbol as not found.
    """

    def __init__(self) -> None:
        self._parser = Parser(PY_LANGUAGE)

    def lookup(
        self, file_path: str | Path, symbol_type: str, symbol_name: str
    ) -> SymbolLookup:
        """Check whether a symbol exists and extract its docstring tags.

        @shape return: SymbolLookup(found, line, docstring, shapes, sources)
        @source file_path: src/core/parser.py#class:MarkdownParser
        """
        path = Path(file_path)
        if not path.is_file():
            return SymbolLookup(found=False)
        try:
            source = path.read_bytes()
        except OSError:
            return SymbolLookup(found=False)

        # Tree-sitter is fault-tolerant: syntax errors surface as ERROR
        # nodes inside a still-usable tree, never as Python exceptions.
        tree = self._parser.parse(source)
        node = self._find_symbol(tree.root_node, symbol_type, symbol_name)
        if node is None:
            return SymbolLookup(found=False)

        docstring = _extract_docstring(node)
        shapes: list[str] = []
        sources: list[str] = []
        if docstring:
            for tag in _TAG_RE.finditer(docstring):
                (shapes if tag.group(1) == "shape" else sources).append(
                    tag.group("body")
                )
        return SymbolLookup(
            found=True,
            line=node.start_point[0] + 1,
            docstring=docstring,
            shapes=shapes,
            sources=sources,
        )

    def _find_symbol(self, root, symbol_type: str, symbol_name: str):
        """Depth-first search for the first node matching the anchor."""
        stack = [root]
        while stack:
            node = stack.pop()
            if self._matches(node, symbol_type, symbol_name):
                return node
            stack.extend(reversed(node.children))
        return None

    @staticmethod
    def _matches(node, symbol_type: str, symbol_name: str) -> bool:
        if symbol_type == "var":
            # Module/level assignment: ``NAME = ...``
            if node.type != "assignment":
                return False
            left = node.child_by_field_name("left")
            return (
                left is not None
                and left.type == "identifier"
                and left.text is not None
                and left.text.decode("utf-8") == symbol_name
            )
        if node.type != _NODE_TYPE.get(symbol_type, ""):
            return False
        name = node.child_by_field_name("name")
        return (
            name is not None
            and name.text is not None
            and name.text.decode("utf-8") == symbol_name
        )


def _split_blocks(lines: list[str]) -> list[list[str]]:
    """Group consecutive lines into Markdown blocks.

    Fenced code blocks and pipe tables stay together as one unit so
    excerpting never cuts them mid-syntax; blank lines and paragraphs
    form their own blocks.

    @shape lines: list[str] (markdown body lines)
    @shape return: list[list[str]] (blocks of consecutive lines)
    """
    blocks: list[list[str]] = []
    i = 0
    total = len(lines)
    while i < total:
        stripped = lines[i].lstrip()
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            j = i + 1
            while j < total and not lines[j].lstrip().startswith(fence):
                j += 1
            if j < total:
                j += 1
            blocks.append(lines[i:j])
            i = j
        elif stripped.startswith("|"):
            j = i
            while j < total and lines[j].lstrip().startswith("|"):
                j += 1
            blocks.append(lines[i:j])
            i = j
        elif stripped == "":
            blocks.append([lines[i]])
            i += 1
        else:
            j = i
            while j < total and lines[j].strip() and not lines[j].lstrip().startswith(("|", "```", "~~~")):
                j += 1
            blocks.append(lines[i:j])
            i = j
    return blocks


def _fit_first_block(block: list[str], limit: int) -> list[str]:
    """Whole-line prefix of an oversized first block.

    Tables keep at least their header + separator row (or nothing) and
    code fences are re-closed, so the result is always valid Markdown.

    @shape block: list[str] (lines of the first block)
    @shape return: list[str] (lines that fit, syntax intact)
    """
    kept: list[str] = []
    used = 0
    for line in block:
        if used + len(line) + 1 > limit - 4:  # reserve a closing fence
            break
        kept.append(line)
        used += len(line) + 1
    first = block[0].lstrip()
    if first.startswith(("```", "~~~")):
        if not kept:
            kept.append(first[:3])
        if not kept[-1].lstrip().startswith(("```", "~~~")):
            kept.append(first[:3])
    elif first.startswith("|") and 0 < len(kept) < 2:
        # A lone table header without its separator breaks the syntax.
        kept = []
    return kept


def _strip_code_regions(text: str) -> str:
    """Remove fenced code blocks and inline code spans from Markdown.

    Fenced blocks wrapped by ``` or ~~~ are dropped line-wise; inline
    `` `...` `` spans are blanked out. This exempts example anchors in
    meta-documents (e.g. BLUEPRINT.md) from anchor verification.

    @shape text: str (markdown body)
    @shape return: str (sanitized body, same line count)
    """
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if fence is None and (
            stripped.startswith("```") or stripped.startswith("~~~")
        ):
            fence = stripped[:3]
            lines.append("")
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            lines.append("")
            continue
        lines.append(_INLINE_CODE_RE.sub("", line))
    return "\n".join(lines)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split leading YAML frontmatter from the Markdown body.

    Only flat ``key: value`` pairs with scalar or inline-list values are
    supported — sufficient for the BLUEPRINT §2 contract.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    data: dict[str, Any] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = _parse_yaml_value(value.strip())
    return data, text[match.end():]


def _parse_yaml_value(value: str) -> Any:
    """Parse a scalar or an inline ``[a, b]`` list."""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [item.strip() for item in inner.split(",") if item.strip()]
    return value


def _extract_docstring(node) -> str | None:
    """Return the docstring text of a class/function node, if present."""
    body = node.child_by_field_name("body")
    if body is None:
        return None
    first = next((c for c in body.children if c.is_named), None)
    if first is None or first.type != "expression_statement":
        return None
    expr = next((c for c in first.children if c.is_named), None)
    if expr is None or expr.type != "string":
        return None
    return _strip_string_quotes(expr.text.decode("utf-8"))


def _strip_string_quotes(raw: str) -> str:
    """Strip triple/single quote delimiters from a string literal."""
    for quote in ('"""', "'''", '"', "'"):
        if raw.startswith(quote) and raw.endswith(quote) and len(raw) >= 2 * len(quote):
            return raw[len(quote):-len(quote)]
    return raw
