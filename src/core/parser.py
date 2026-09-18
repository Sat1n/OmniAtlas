"""Markdown anchor extraction & Tree-sitter AST parsing.

Three engines live in this module:

* :class:`MarkdownParser` — extracts YAML frontmatter and symbol-level
  anchors (``[Title](path.py#class:Name)``) from L1/L2 documents.
* :class:`PythonASTParser` — verifies anchors against the real AST via
  Tree-sitter and extracts ``@shape`` / ``@source`` machine-readable tags.
* :class:`LanguageRegistry` — multi-language parser registry
  (TypeScript/JavaScript, Go, Rust, C/C++) extracting symbols, imports
  and the frontend/backend API facts consumed by the cross-language
  linker (:mod:`core.linker`).

Per AGENTS.md, validation never reads full files for L3 checks; the AST
engines only load the precise boundaries of the requested symbol.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Query, QueryCursor

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


# --------------------------------------------------------------------- #
# Multi-language registry (TypeScript/JS, Go, Rust, C/C++ + HTML)
# --------------------------------------------------------------------- #

#: HTTP methods recognised on backend route declarations.
HTTP_METHODS = {"get", "post", "put", "delete", "patch"}

#: Frontend extensions whose inline scripts are scanned for fetch/axios/
#: EventSource calls with a lightweight regex (no HTML grammar dependency).
_HTML_URL_RE = re.compile(
    r"""(?:fetch|axios(?:\.\w+)?|EventSource)\(\s*['"`]([^'"`]+)['"`]"""
)
_HTML_METHOD_RE = re.compile(r"""method\s*:\s*['"](\w+)['"]""")

#: Tree-sitter grammars, loaded lazily and defensively: a missing or
#: ABI-incompatible wheel degrades that language instead of the tool.
#: Python reuses the already-required ``tree_sitter_python`` wheel and
#: contributes backend route extraction to the cross-language linker.
_GRAMMAR_SPECS = (
    ("python", "tree_sitter_python", "language", (".py",)),
    ("typescript", "tree_sitter_typescript", "language_typescript", (".ts", ".js", ".jsx")),
    ("tsx", "tree_sitter_typescript", "language_tsx", (".tsx",)),
    ("go", "tree_sitter_go", "language", (".go",)),
    ("rust", "tree_sitter_rust", "language", (".rs",)),
    ("c", "tree_sitter_c", "language", (".c", ".h")),
    ("cpp", "tree_sitter_cpp", "language", (".cc", ".cpp", ".hpp")),
)


@dataclass
class SymbolDecl:
    """One symbol extracted from a multi-language source file."""

    name: str
    kind: str  # class | function | method | var | struct | enum
    line: int


@dataclass
class FileFacts:
    """Structured facts extracted from one source file.

    @shape symbols: list[SymbolDecl]
    @shape imports: list[str] (raw import targets, resolvable or not)
    @shape endpoints: list[tuple[str, str]] (frontend method, url)
    @shape routes: list[tuple[str, str, str]] (backend method, path, handler)
    """

    path: str
    language: str | None = None
    symbols: list[SymbolDecl] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    endpoints: list[tuple[str, str]] = field(default_factory=list)
    routes: list[tuple[str, str, str]] = field(default_factory=list)


class LanguageRegistry:
    """Dispatches files to per-language Tree-sitter parsers.

    Built-in extractors cover TypeScript/JavaScript (classes, functions,
    exported consts, imports, fetch/axios calls), Go (structs, functions,
    methods, package imports, Gin routes), Rust (structs, enums, fns,
    use declarations) and C/C++ (structs, classes, functions,
    #include dependencies). HTML files are scanned for fetch/axios calls
    with a regex. Custom ``[[custom_scm]]`` queries from
    :mod:`core.config` extend symbol extraction for any grammar.

    @source config: src/core/config.py#function:load_config
    """

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._ext_map: dict[str, str] = {}
        for lang, module_name, func_name, extensions in _GRAMMAR_SPECS:
            try:
                module = __import__(module_name, fromlist=[func_name])
                language = Language(getattr(module, func_name)())
            except (ImportError, AttributeError, TypeError, RuntimeError):
                continue  # grammar unavailable — language degrades gracefully
            self._parsers[lang] = Parser(language)
            for ext in extensions:
                self._ext_map[ext] = lang
        self._custom: list[tuple[str, str, Any]] = []  # (lang, name, Query)

    @property
    def available_languages(self) -> list[str]:
        """Grammars that loaded successfully in this environment."""
        return sorted(self._parsers)

    def language_for(self, file_path: str | Path) -> str | None:
        """Return the registry language id for a path, or None."""
        return self._ext_map.get(Path(file_path).suffix.lower())

    def load_custom_scm(self, entries: list[Any]) -> None:
        """Compile ``[[custom_scm]]`` entries; invalid queries are skipped.

        @shape entries: list[CustomScm] (see core.config)
        @source entries: src/core/config.py#function:load_config
        """
        for entry in entries:
            lang = str(getattr(entry, "language", "") or "").strip()
            name = str(getattr(entry, "name", "custom") or "custom").strip() or "custom"
            if lang not in self._parsers:
                continue
            source = getattr(entry, "query", None)
            scm_path = getattr(entry, "path", None)
            if not source and scm_path:
                try:
                    source = Path(scm_path).read_text(encoding="utf-8")
                except OSError:
                    continue
            if not source:
                continue
            language = self._parsers[lang].language
            try:
                query = Query(language, source)
            except Exception as exc:  # invalid SCM must not break parsing
                console_print(
                    f"[yellow]omni-atlas: skipping invalid custom SCM "
                    f"'{name}' ({lang}): {exc}[/yellow]"
                )
                continue
            self._custom.append((lang, name, query))

    def parse_file(self, file_path: str | Path) -> FileFacts:
        """Extract structured facts from any supported source file.

        @shape return: FileFacts
        @source extensions: src/core/git_provider.py#var:CODE_EXTENSIONS
        """
        path = Path(file_path)
        facts = FileFacts(path=path.as_posix())
        if path.suffix.lower() == ".html":
            facts.language = "html"
            self._extract_html(path, facts)
            return facts
        lang = self.language_for(path)
        if lang is None or lang not in self._parsers:
            return facts
        try:
            source = path.read_bytes()
        except OSError:
            return facts
        facts.language = lang
        tree = self._parsers[lang].parse(source)
        extractor = {
            "typescript": self._extract_ts,
            "tsx": self._extract_ts,
            "go": self._extract_go,
            "rust": self._extract_rust,
            "c": self._extract_c,
            "cpp": self._extract_c,
            "python": self._extract_python,
        }[lang]
        extractor(tree.root_node, facts)
        self._run_custom(lang, tree.root_node, facts)
        return facts

    # -- extraction drivers ----------------------------------------- #

    @staticmethod
    def _walk(node):
        stack = [node]
        while stack:
            current = stack.pop()
            yield current
            stack.extend(reversed(current.children))

    @staticmethod
    def _field(node, name):
        child = node.child_by_field_name(name)
        return child

    @staticmethod
    def _text(node) -> str:
        return node.text.decode("utf-8") if node is not None else ""

    @staticmethod
    def _string_value(node) -> str | None:
        """Unwrap string / raw_string nodes to their inner text."""
        if node is None:
            return None
        if node.type in (
            "string_fragment",
            "string_content",
            "interpreted_string_literal_content",
        ):
            return LanguageRegistry._text(node)
        for child in node.children:
            if child.type in (
                "string_fragment",
                "string_content",
                "raw_string_content",
                "interpreted_string_literal_content",
            ):
                return LanguageRegistry._text(child)
        return None

    def _extract_ts(self, root, facts: FileFacts) -> None:
        for node in self._walk(root):
            ntype = node.type
            if ntype == "class_declaration":
                name = self._field(node, "name")
                facts.symbols.append(SymbolDecl(self._text(name), "class", node.start_point[0] + 1))
            elif ntype == "function_declaration":
                name = self._field(node, "name")
                facts.symbols.append(SymbolDecl(self._text(name), "function", node.start_point[0] + 1))
            elif ntype == "method_definition":
                name = self._field(node, "name")
                facts.symbols.append(SymbolDecl(self._text(name), "method", node.start_point[0] + 1))
            elif ntype == "export_statement":
                for child in self._walk(node):
                    if child.type in ("lexical_declaration", "variable_declaration"):
                        for declarator in child.children:
                            if declarator.type == "variable_declarator":
                                name = self._field(declarator, "name")
                                facts.symbols.append(
                                    SymbolDecl(self._text(name), "var", node.start_point[0] + 1)
                                )
                        break
            elif ntype == "import_statement":
                source_node = self._field(node, "source")
                value = self._string_value(source_node)
                if value:
                    facts.imports.append(value)
            elif ntype == "call_expression":
                self._extract_ts_call(node, facts)

    def _extract_ts_call(self, node, facts: FileFacts) -> None:
        fn = self._field(node, "function")
        args = self._field(node, "arguments")
        if fn is None or args is None or not args.named_children:
            return
        name = self._text(self._field(fn, "field") or fn) if fn.type == "member_expression" else self._text(fn)
        if fn.type == "identifier" and name == "fetch":
            url = self._string_value(args.named_children[0])
            if url:
                facts.endpoints.append((self._ts_method(args), url))
        elif fn.type == "member_expression" and name.startswith("axios"):
            url = self._string_value(args.named_children[0])
            if url:
                method = name.split(".", 1)[1] if "." in name else self._ts_method(args)
                facts.endpoints.append((method.upper() or "GET", url))

    @staticmethod
    def _ts_method(args) -> str:
        for node in args.children:
            if node.type == "object":
                for pair in node.children:
                    if pair.type == "pair":
                        key = pair.child_by_field_name("key")
                        value = pair.child_by_field_name("value")
                        if key is not None and LanguageRegistry._text(key) == "method":
                            text = LanguageRegistry._text(value).strip("'\"")
                            if text:
                                return text.upper()
        return "GET"

    def _extract_go(self, root, facts: FileFacts) -> None:
        for node in self._walk(root):
            ntype = node.type
            if ntype == "type_spec":
                name = self._field(node, "name")
                for child in node.children:
                    if child.type in ("struct_type", "interface_type"):
                        facts.symbols.append(
                            SymbolDecl(self._text(name), "struct", node.start_point[0] + 1)
                        )
            elif ntype == "function_declaration":
                name = self._field(node, "name")
                facts.symbols.append(SymbolDecl(self._text(name), "function", node.start_point[0] + 1))
            elif ntype == "method_declaration":
                name = self._field(node, "name")
                facts.symbols.append(SymbolDecl(self._text(name), "method", node.start_point[0] + 1))
            elif ntype == "import_declaration":
                for spec in self._walk(node):
                    if spec.type == "import_spec":
                        value = self._string_value(spec.child_by_field_name("path"))
                        if value:
                            facts.imports.append(value)
            elif ntype == "call_expression":
                self._extract_go_route(node, facts)

    def _extract_go_route(self, node, facts: FileFacts) -> None:
        fn = self._field(node, "function")
        args = self._field(node, "arguments")
        if fn is None or fn.type != "selector_expression" or args is None:
            return
        method = self._text(fn.child_by_field_name("field")).lower()
        if method not in HTTP_METHODS or not args.named_children:
            return
        path = self._string_value(args.named_children[0])
        if not path:
            return
        handler = (
            self._text(args.named_children[1]) if len(args.named_children) > 1 else ""
        )
        # Method expressions like h.Handle reduce to the bare handler name.
        handler = handler.rsplit(".", 1)[-1]
        facts.routes.append((method.upper(), path, handler))

    def _extract_python(self, root, facts: FileFacts) -> None:
        """Backend routes: FastAPI/Flask decorators + stdlib route checks.

        Decorators: ``@app.get("/x")`` / ``@app.route("/x")``. Stdlib:
        ``route == "/x"`` comparisons inside ``do_GET`` / ``do_POST``
        handlers route to the enclosing method (our own server style).
        """
        for node in self._walk(root):
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type != "decorator":
                        continue
                    definition = self._field(node, "definition")
                    handler = self._function_name(definition)
                    for call in self._walk(child):
                        if call.type != "call":
                            continue
                        route = self._python_decorator_route(call)
                        if route:
                            method, path = route
                            facts.routes.append((method, path, handler or ""))
            elif node.type == "function_definition":
                name = self._function_name(node)
                if name not in ("do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
                    continue
                method = name[3:].upper()
                for child in self._walk(node):
                    if child.type != "comparison_operator" or len(child.children) < 3:
                        continue
                    left, op, right = child.children[0], child.children[1], child.children[-1]
                    # Stdlib dispatch idiom: the compared operand is a bare
                    # local (route == "/x"), an attribute chain, or a
                    # subscript (self.path.split("?")[0] == "/x") — any
                    # literal "/..." comparison declares a route. The `in`
                    # form (route in ("/x", "/y")) declares a route bundle.
                    if op.type == "in" and right.type in ("tuple", "list"):
                        for element in right.children:
                            path = self._string_value(element)
                            if path and path.startswith("/"):
                                handler = self._python_dispatch_target(child) or name
                                facts.routes.append((method, path, handler))
                        continue
                    if op.type != "==":
                        continue
                    path = self._string_value(right)
                    if path and path.startswith("/"):
                        handler = self._python_dispatch_target(child) or name
                        facts.routes.append((method, path, handler))

    def _python_dispatch_target(self, comparison) -> str | None:
        """Resolve the handler a stdlib route branch dispatches to.

        ``if route == "/api/x": self._handle_x(); return`` — the first
        ``self.<method>()`` call inside the branch is the real handler,
        which is far more useful than the enclosing ``do_GET`` shell.
        """
        if_node = comparison.parent
        if if_node is None or if_node.type != "if_statement":
            return None
        consequence = self._field(if_node, "consequence")
        if consequence is None:
            return None
        for node in self._walk(consequence):
            if node.type != "call":
                continue
            fn = self._field(node, "function")
            if fn is None or fn.type != "attribute":
                continue
            obj = self._field(fn, "object")
            attr = self._field(fn, "attribute")
            if obj is not None and self._text(obj) == "self" and attr is not None:
                return self._text(attr)
        return None

    def _python_decorator_route(self, call) -> tuple[str, str] | None:
        fn = self._field(call, "function")
        args = self._field(call, "arguments")
        if fn is None or fn.type != "attribute" or args is None:
            return None
        method = self._text(fn.child_by_field_name("attribute")).lower()
        if method == "route":
            method = "get"  # Flask @app.route defaults to GET
        if method not in HTTP_METHODS or not args.named_children:
            return None
        path = self._string_value(args.named_children[0])
        if not path:
            return None
        return method.upper(), path

    @staticmethod
    def _function_name(node) -> str:
        if node is None:
            return ""
        name = node.child_by_field_name("name")
        return name.text.decode("utf-8") if name is not None else ""

    def _extract_rust(self, root, facts: FileFacts) -> None:
        for node in self._walk(root):
            ntype = node.type
            if ntype in ("struct_item", "enum_item", "function_item"):
                name = self._field(node, "name")
                kind = {"struct_item": "struct", "enum_item": "enum", "function_item": "function"}[ntype]
                facts.symbols.append(SymbolDecl(self._text(name), kind, node.start_point[0] + 1))
            elif ntype == "use_declaration":
                text = self._text(self._field(node, "argument"))
                if text:
                    facts.imports.append(text.strip())

    def _extract_c(self, root, facts: FileFacts) -> None:
        for node in self._walk(root):
            ntype = node.type
            if ntype in ("struct_specifier", "class_specifier"):
                name = self._field(node, "name")
                if name is not None:
                    kind = "class" if ntype == "class_specifier" else "struct"
                    facts.symbols.append(SymbolDecl(self._text(name), kind, node.start_point[0] + 1))
            elif ntype == "function_definition":
                declarator = self._field(node, "declarator")
                name = self._c_function_name(declarator)
                if name:
                    facts.symbols.append(SymbolDecl(name, "function", node.start_point[0] + 1))
            elif ntype == "preproc_include":
                raw = self._text(node.child_by_field_name("path"))
                if raw.startswith('"'):
                    facts.imports.append(raw.strip('"'))  # local include
                elif raw:
                    facts.imports.append(raw)  # <system> include, kept as-is

    @staticmethod
    def _c_function_name(declarator) -> str | None:
        while declarator is not None:
            if declarator.type in ("identifier", "field_identifier"):
                return LanguageRegistry._text(declarator)
            if declarator.type == "qualified_identifier":
                name = declarator.child_by_field_name("name")
                return LanguageRegistry._text(name) if name is not None else None
            inner = declarator.child_by_field_name("declarator")
            if inner is None:
                for child in declarator.children:
                    if child.type in ("identifier", "field_identifier"):
                        return LanguageRegistry._text(child)
                return None
            declarator = inner
        return None

    @staticmethod
    def _extract_html(path: Path, facts: FileFacts) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        matches = list(_HTML_URL_RE.finditer(text))
        for index, match in enumerate(matches):
            url = match.group(1)
            method = "GET"
            # The method lookup window must stop at the next call AND at a
            # fixed cap, or a neighbouring `method: "POST"` leaks over.
            next_start = (
                matches[index + 1].start() if index + 1 < len(matches)
                else match.end() + 200
            )
            window_end = min(match.end() + 200, next_start)
            method_match = _HTML_METHOD_RE.search(text[match.end():window_end])
            if method_match:
                method = method_match.group(1).upper()
            facts.endpoints.append((method, url))

    def _run_custom(self, lang: str, root, facts: FileFacts) -> None:
        """Run validated [[custom_scm]] queries and collect @symbol captures."""
        for query_lang, name, query in self._custom:
            if query_lang != lang:
                continue
            for pattern_match in QueryCursor(query).matches(root):
                for capture_node in pattern_match[1].get("symbol", []):
                    name_node = self._field(capture_node, "name")
                    label = self._text(name_node) or self._text(capture_node).splitlines()[0][:60]
                    facts.symbols.append(
                        SymbolDecl(label, name, capture_node.start_point[0] + 1)
                    )


def console_print(message: str) -> None:
    """Rich-aware console print for parser warnings (kept dependency-light)."""
    from rich.console import Console

    Console().print(message)
