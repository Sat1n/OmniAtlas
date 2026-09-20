"""Phase 8 multi-language parsing, cross-language linking and config tests."""

from pathlib import Path

from core.config import CustomScm, load_config
from core.graph import TopologyGraphBuilder
from core.linker import ApiLinker, FRONTEND_EXTENSIONS
from core.parser import LanguageRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def test_registry_languages(registry: LanguageRegistry) -> None:
    available = registry.available_languages
    for lang in ("python", "typescript", "go", "rust", "c", "cpp", "c_sharp"):
        assert lang in available, f"{lang} grammar failed to load"
    assert registry.language_for("a.ts") == "typescript"
    assert registry.language_for("a.tsx") == "tsx"
    assert registry.language_for("a.go") == "go"
    assert registry.language_for("a.cpp") == "cpp"
    assert registry.language_for("a.h") == "c"


def test_typescript_facts(registry: LanguageRegistry) -> None:
    facts = registry.parse_file(FIXTURES / "index.ts")
    names = {(s.name, s.kind) for s in facts.symbols}
    assert ("UserClient", "class") in names
    assert ("submit", "function") in names
    assert ("API_BASE", "var") in names  # exported const
    assert "./api" in facts.imports
    assert ("POST", "/api/users") in facts.endpoints
    assert ("GET", "/api/hello") in facts.endpoints


def test_go_facts(registry: LanguageRegistry) -> None:
    facts = registry.parse_file(FIXTURES / "main.go")
    names = {(s.name, s.kind) for s in facts.symbols}
    assert ("User", "struct") in names
    assert ("main", "function") in names
    assert ("helloHandler", "function") in names
    assert "github.com/gin-gonic/gin" in facts.imports
    assert ("GET", "/api/hello", "helloHandler") in facts.routes
    assert ("POST", "/api/users", "createUser") in facts.routes


def test_cpp_facts(registry: LanguageRegistry) -> None:
    facts = registry.parse_file(FIXTURES / "main.cpp")
    names = {(s.name, s.kind) for s in facts.symbols}
    assert ("Config", "struct") in names
    assert ("Engine", "class") in names
    assert ("main", "function") in names
    assert "util.h" in facts.imports  # local quoted include
    assert "<cstdio>" in facts.imports  # system include kept verbatim


def test_csharp_facts(registry: LanguageRegistry) -> None:
    facts = registry.parse_file(FIXTURES / "Program.cs")
    names = {(s.name, s.kind) for s in facts.symbols}
    assert ("Engine", "class") in names
    assert ("IEngine", "interface") in names
    assert ("EngineState", "enum") in names
    assert ("EngineStats", "struct") in names
    assert ("Start", "method") in names
    assert "System.Collections.Generic" in facts.imports


def test_graph_and_linter_resolve_paths_against_root(tmp_path: Path) -> None:
    """root != cwd (e.g. `mcp --workspace`) must still parse and verify."""
    from core.graph import TopologyGraphBuilder
    from core.linter import LinterEngine

    module = tmp_path / "Rug.Core"
    module.mkdir()
    (module / "Engine.cs").write_text(
        "namespace Rug;\npublic class Engine { public void Start() {} }\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(
        "# Rug\n\n[Engine](Rug.Core/Engine.cs#class:Engine)\n", encoding="utf-8"
    )

    graph = TopologyGraphBuilder(tmp_path).build().to_dict()
    ids = {node["data"]["id"] for node in graph["nodes"]}
    assert "Rug.Core/Engine.cs" in ids
    assert "Rug.Core/Engine.cs#class:Engine" in ids

    engine = LinterEngine(tmp_path)
    checks = engine.check_anchors(["AGENTS.md"])
    assert [c.found for c in checks] == [True]
    assert checks[0].lookup.line
    for token_check in engine.check_token_budgets(["AGENTS.md"]):
        assert token_check.passed


def test_linker_resolves_paths_against_root(tmp_path: Path) -> None:
    from core.linker import ApiLinker

    (tmp_path / "web.ts").write_text(
        'fetch("/api/users", { method: "POST" });\n', encoding="utf-8"
    )
    (tmp_path / "api.go").write_text(
        'package main\nfunc createUser() {}\nfunc main() { r.POST("/api/users", createUser) }\n',
        encoding="utf-8",
    )
    links = ApiLinker().build_links(["web.ts", "api.go"], repo_root=tmp_path)
    assert any(link.target_symbol == "createUser" for link in links)


def test_anchor_regex_supports_multilang_targets() -> None:
    from core.parser import ANCHOR_RE

    match = ANCHOR_RE.search("[Engine](Rug.Core/Engine.cs#class:Engine)")
    assert match and match.group("path") == "Rug.Core/Engine.cs"
    match = ANCHOR_RE.search("[Start](cmd/main.go#method:Start)")
    assert match and match.group("type") == "method"
    match = ANCHOR_RE.search("[Point](src/lib.rs#struct:Point)")
    assert match and match.group("type") == "struct"
    # Legacy Python anchors keep parsing unchanged.
    match = ANCHOR_RE.search("[P](src/core/parser.py#class:MarkdownParser)")
    assert match and match.group("path").endswith(".py")


def test_symbol_resolver_dispatches_by_language() -> None:
    from core.parser import SymbolResolver

    resolver = SymbolResolver()
    found = resolver.lookup(FIXTURES / "Program.cs", "class", "Engine")
    assert found.found and found.line
    missing = resolver.lookup(FIXTURES / "Program.cs", "class", "Missing")
    assert not missing.found
    # Python targets keep the rich docstring-tag path.
    python = resolver.lookup("src/core/parser.py", "class", "MarkdownParser")
    assert python.found


def test_linter_validates_csharp_anchor(tmp_path: Path) -> None:
    from core.linter import LinterEngine

    doc = tmp_path / "ARCH.md"
    doc.write_text(
        "---\nid: arch\n---\n\n# Arch\n\n"
        "[Engine](tests/fixtures/Program.cs#class:Engine)\n",
        encoding="utf-8",
    )
    engine = LinterEngine(".")
    checks = engine.check_anchors([str(doc)])
    assert len(checks) == 1 and checks[0].found and checks[0].lookup.line

    doc.write_text(
        "# Arch\n\n[Engine](tests/fixtures/Program.cs#class:Gone)\n",
        encoding="utf-8",
    )
    checks = engine.check_anchors([str(doc)])
    assert checks and not checks[0].found  # broken anchors are surfaced


def test_api_linker_matches_fixtures(registry: LanguageRegistry) -> None:
    files = [str(p) for p in FIXTURES.iterdir()] + [
        "src/ui/index.html",
        "src/core/server.py",
    ]
    links = ApiLinker(registry).build_links(files)
    keys = {(l.source_file, l.method, l.path, l.target_symbol) for l in links}
    # Fixture cross-language link: TS frontend -> Go backend.
    assert any(
        src.endswith("index.ts") and method == "POST" and path == "/api/users"
        and symbol == "createUser"
        for src, method, path, symbol in keys
    )
    # Real dashboard link: HTML frontend -> Python backend handler.
    assert any(
        src == "src/ui/index.html" and path == "/api/open-in-editor"
        and symbol == "_open_in_editor"
        for src, method, path, symbol in keys
    )


def test_graph_includes_ui_and_api_edges() -> None:
    graph = TopologyGraphBuilder(".").build().to_dict()
    nodes = {n["data"]["id"]: n["data"] for n in graph["nodes"]}
    assert "src/ui/index.html" in nodes
    assert nodes["src/ui/index.html"]["language"] == "html"
    api_edges = [e for e in graph["edges"] if e["data"]["kind"] == "api"]
    targets = {e["data"]["target"] for e in api_edges}
    assert "src/core/server.py#function:_open_in_editor" in targets


def test_custom_scm_valid_and_invalid(registry: LanguageRegistry) -> None:
    # An invalid query must be skipped without raising.
    registry.load_custom_scm([CustomScm(language="python", name="broken", query="(((")])
    # A valid query adds symbols under the entry name as kind.
    registry.load_custom_scm([
        CustomScm(
            language="python",
            name="class",
            query="(class_definition name: (identifier) @symbol)",
        )
    ])
    facts = registry.parse_file("src/core/graph.py")
    assert any(s.kind == "class" and s.name == "TopologyGraphBuilder" for s in facts.symbols)


def test_load_config(tmp_path: Path) -> None:
    config_file = tmp_path / ".omni-atlas.toml"
    config_file.write_text(
        '[[custom_scm]]\nlanguage = "go"\nname = "interface"\n'
        'query = "(type_spec name: (type_identifier) @symbol)"\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert len(config.custom_scm) == 1
    assert config.custom_scm[0].language == "go"
    # Malformed TOML degrades to an empty config instead of raising.
    config_file.write_text("[[custom_scm]\nbroken", encoding="utf-8")
    assert load_config(tmp_path).custom_scm == []
    # Missing config file is fine too.
    assert load_config(tmp_path / "nope").custom_scm == []
