"""Cross-language API dependency linker.

Bridges the frontend/backend gap that symbol anchors cannot express:

* **Frontend calls** — ``fetch('/api/...')`` / ``axios`` URLs extracted
  from TS/JS/HTML sources by :class:`core.parser.LanguageRegistry`.
* **Backend routes** — FastAPI/Flask decorators (``@app.get("/x")``),
  Gin handlers (``r.GET("/x", h)``) and stdlib ``route == "/x"``
  comparisons inside ``do_GET``/``do_POST`` extracted from Python/Go.

When a frontend call matches a backend route (same path, same HTTP
method — method ``ANY`` on either side matches everything), the linker
emits an :class:`ApiLink` that the topology graph renders as a dashed
cross-language ``API_CALL`` edge.

@source facts: src/core/parser.py#class:LanguageRegistry
"""

import re
from dataclasses import dataclass
from pathlib import Path

from core.parser import FileFacts, LanguageRegistry

#: Frontend extensions scanned for fetch/axios calls.
FRONTEND_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".html"}

#: Python extensions scanned for backend route declarations.
BACKEND_EXTENSIONS = {".py", ".go"}

#: Path suffixes ignored when matching (query strings / fragments).
_URL_STRIP_RE = re.compile(r"[?#].*$")


@dataclass
class ApiLink:
    """One matched frontend-call -> backend-route dependency."""

    source_file: str  # frontend file (HTML/TS/JS)
    target_file: str  # backend file (Python/Go)
    target_symbol: str  # handler function name
    method: str
    path: str

    @property
    def target_symbol_id(self) -> str:
        """Graph node id of the backend handler symbol."""
        return f"{self.target_file}#function:{self.target_symbol}"


class ApiLinker:
    """Matches frontend API calls against backend routes."""

    def __init__(self, registry: LanguageRegistry | None = None) -> None:
        self._registry = registry or LanguageRegistry()

    def build_links(self, files: list[str], repo_root: str | Path = ".") -> list[ApiLink]:
        """Parse every relevant file and return all matched API links.

        @shape return: list[ApiLink]
        @source facts: src/core/parser.py#function:parse_file
        """
        facts_by_file: dict[str, FileFacts] = {}
        for path in files:
            suffix = Path(path).suffix.lower()
            if suffix not in FRONTEND_EXTENSIONS and suffix not in BACKEND_EXTENSIONS:
                continue
            facts_by_file[path] = self._registry.parse_file(path)

        routes: list[tuple[str, str, FileFacts]] = []
        for facts in facts_by_file.values():
            for method, path, handler in facts.routes:
                routes.append((method.upper(), self._normalize(path), facts))

        links: list[ApiLink] = []
        seen: set[tuple[str, str, str]] = set()
        for facts in facts_by_file.values():
            for method, url in facts.endpoints:
                normalized = self._normalize(url)
                route_method, route_path, route_facts = self._match(
                    method.upper(), normalized, routes
                )
                if route_facts is None:
                    continue
                key = (facts.path, route_path, route_facts.path)
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    ApiLink(
                        source_file=facts.path,
                        target_file=route_facts.path,
                        target_symbol=self._handler_name(route_facts, route_method, route_path),
                        method=route_method,
                        path=route_path,
                    )
                )
        return links

    @staticmethod
    def _normalize(url: str) -> str:
        """Strip query strings, fragments and template placeholders."""
        path = _URL_STRIP_RE.sub("", url.strip())
        # Template literals / path params: ${id} and :id collapse to {*}.
        path = re.sub(r"\$\{[^}]*\}", "{*}", path)
        path = re.sub(r"/:[^/]+", "/{*}", path)
        return path.rstrip("/") or "/"

    @staticmethod
    def _match(
        method: str, path: str, routes: list[tuple[str, str, FileFacts]]
    ) -> tuple[str, str, FileFacts | None]:
        """Exact path + method first, then ANY-method fallbacks."""
        for route_method, route_path, facts in routes:
            if route_path == path and method in (route_method, "ANY"):
                return route_method, route_path, facts
        for route_method, route_path, facts in routes:
            if route_path == path and route_method == "ANY":
                return route_method, route_path, facts
        return "", "", None

    @staticmethod
    def _handler_name(facts: FileFacts, method: str, path: str) -> str:
        """Handler symbol of the matched route (last one wins)."""
        name = ""
        for route_method, route_path, handler in facts.routes:
            if (
                ApiLinker._normalize(route_path) == path
                and method in (route_method, "ANY")
            ):
                name = handler
        return name
