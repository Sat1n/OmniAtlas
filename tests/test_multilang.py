"""Phase 8 multi-language parsing, cross-language linking and config tests."""

from pathlib import Path

from core.config import CustomScm, load_config
from core.graph import TopologyGraphBuilder
from core.linker import ApiLinker, FRONTEND_EXTENSIONS
from core.parser import LanguageRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def test_registry_languages(registry: LanguageRegistry) -> None:
    available = registry.available_languages
    for lang in ("python", "typescript", "go", "rust", "c", "cpp"):
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
