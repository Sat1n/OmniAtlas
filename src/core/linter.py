"""Core double-verification engine (collision logic).

Implements the bidirectional collision check plus the token guard:

* **Forward** — every anchor in a staged document must resolve to a real
  AST symbol (verified via :class:`core.parser.SymbolResolver`, which
  dispatches ``.py`` anchors to the Python AST and every other extension
  to the multi-language registry).
* **Reverse** — every staged code change must be synchronized with each
  project document that references it (Fatal Sync Enforcement,
  AGENTS.md §5 rule 3). A referencing doc missing from the staging area
  is reported as ``STALE_DOC``.
* **Token guard** — L1/L2 documents must stay within their BLUEPRINT §1
  token budgets (2000 / 4000); violations are reported as ``OVERSIZED``.
"""

from dataclasses import dataclass
from pathlib import Path

from core.git_provider import (
    IGNORED_DIRS,
    GitProvider,
    StagedChanges,
    build_ignore_matcher,
)
from core.linker import FRONTEND_EXTENSIONS, ApiLinker
from core.parser import (
    LanguageRegistry,
    MarkdownParser,
    SymbolAnchor,
    SymbolLookup,
    SymbolResolver,
)

#: Token ceilings defined by BLUEPRINT §1 zoom levels.
L1_TOKEN_LIMIT = 2000
L2_TOKEN_LIMIT = 4000


@dataclass
class AnchorCheck:
    """Forward collision result: one document anchor vs the AST."""

    doc_file: str
    anchor: SymbolAnchor
    lookup: SymbolLookup

    @property
    def found(self) -> bool:
        return self.lookup.found


@dataclass
class SyncCheck:
    """Reverse collision result: one staged code file vs a referencing doc."""

    code_file: str
    doc_file: str
    in_sync: bool


@dataclass
class TokenCheck:
    """Token budget result for one staged document."""

    doc_file: str
    level: str  # "L1" | "L2"
    tokens: int
    limit: int

    @property
    def passed(self) -> bool:
        return self.tokens <= self.limit


@dataclass
class ApiCheck:
    """Cross-language link audit for one frontend API call."""

    source_file: str
    method: str
    path: str
    matched: bool
    target_file: str = ""
    target_symbol: str = ""


class LinterEngine:
    """Orchestrates the bidirectional collision check and token guard."""

    def __init__(
        self, repo_root: str | Path = ".", extra_excludes: list[str] | None = None
    ) -> None:
        self._root = Path(repo_root)
        self._exclude = list(extra_excludes or [])
        self._md_parser = MarkdownParser()
        self._registry = LanguageRegistry()
        self._resolver = SymbolResolver(self._registry)
        self._linker = ApiLinker(self._registry)

    def check_anchors(self, doc_files: list[str]) -> list[AnchorCheck]:
        """Forward check: resolve every anchor of staged docs in the AST.

        @shape return: list[AnchorCheck]
        @source doc_files: core/git_provider.py#function:collect_staged_changes
        """
        results: list[AnchorCheck] = []
        for doc in doc_files:
            for anchor in self._md_parser.parse(doc).anchors:
                lookup = self._resolver.lookup(
                    anchor.file_path, anchor.symbol_type, anchor.symbol_name
                )
                results.append(AnchorCheck(doc, anchor, lookup))
        return results

    def check_reverse_sync(self, staged: StagedChanges) -> list[SyncCheck]:
        """Reverse check: docs referencing staged code must be staged too.

        Scans project Markdown files for references to each staged ``.py``
        path. A referencing document absent from the staging area means the
        documentation may now diverge from the code — ``STALE_DOC``.

        @shape return: list[SyncCheck]
        @source staged: core/git_provider.py#function:collect_staged_changes
        """
        staged_docs = set(staged.doc_files)
        checks: list[SyncCheck] = []
        docs = self._discover_markdown_docs()
        for code_file in staged.code_files:
            norm = Path(code_file).as_posix()
            for doc_path in docs:
                try:
                    text = doc_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                # Raw-text match (code fences included): even a topology
                # mention inside a diagram block counts as a reference.
                if norm in text:
                    rel = doc_path.relative_to(self._root).as_posix()
                    checks.append(SyncCheck(norm, rel, rel in staged_docs))
        return checks

    def check_token_budgets(self, doc_files: list[str]) -> list[TokenCheck]:
        """Token guard: enforce BLUEPRINT §1 ceilings on staged docs.

        Root-level documents are L1 (2000 tokens); documents nested in
        sub-directories are L2 (4000 tokens).

        @shape return: list[TokenCheck]
        @source doc_files: core/git_provider.py#function:collect_staged_changes
        """
        checks: list[TokenCheck] = []
        for doc in doc_files:
            parsed = self._md_parser.parse(doc)
            level = "L1" if Path(doc).parent == Path(".") else "L2"
            limit = L1_TOKEN_LIMIT if level == "L1" else L2_TOKEN_LIMIT
            checks.append(TokenCheck(doc, level, parsed.estimate_tokens(), limit))
        return checks

    def check_api_links(self, staged: StagedChanges) -> list[ApiCheck]:
        """Audit staged frontend calls against backend routes (informational).

        Parses every project code file through :class:`core.linker.ApiLinker`
        and reports each endpoint declared by a *staged* frontend file as
        linked or unmatched. Results are advisory — they never change the
        process exit code.

        @shape return: list[ApiCheck]
        @source links: core/linker.py#function:build_links
        """
        frontend = sorted({
            f for f in staged.code_files
            if Path(f).suffix.lower() in FRONTEND_EXTENSIONS
        })
        if not frontend:
            return []
        all_files = GitProvider().collect_all_files(
            self._root, extra_excludes=self._exclude
        ).code_files
        links = self._linker.build_links(all_files, self._root)
        checks: list[ApiCheck] = []
        seen: set[tuple[str, str, str]] = set()
        for file in frontend:
            for method, url in self._registry.parse_file(file).endpoints:
                normalized = ApiLinker._normalize(url)
                key = (file, method, url)
                if key in seen:
                    continue
                seen.add(key)
                link = next(
                    (l for l in links
                     if l.source_file == file and l.path == normalized),
                    None,
                )
                checks.append(
                    ApiCheck(
                        source_file=file,
                        method=method,
                        path=url,
                        matched=link is not None,
                        target_file=link.target_file if link else "",
                        target_symbol=link.target_symbol if link else "",
                    )
                )
        return checks

    def _discover_markdown_docs(self) -> list[Path]:
        """List project Markdown files, skipping vendored/hidden trees.

        The merged exclusion policy (``.omniignore`` + ``[scan].exclude``
        + CLI extras) applies here too, so a doc directory excluded from
        scanning is also exempt from reverse-sync checks.
        """
        matcher = build_ignore_matcher(self._root, self._exclude)
        docs: list[Path] = []
        for path in sorted(self._root.rglob("*.md")):
            if any(part in IGNORED_DIRS for part in path.parts):
                continue
            if matcher.matches(path.relative_to(self._root).as_posix()):
                continue
            docs.append(path)
        return docs
