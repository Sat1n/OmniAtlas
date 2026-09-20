"""Lifecycle resilience audit: encoding, paths, worktrees, MCP stdio, ports."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from core.config import load_config
from core.git_provider import GitProvider
from core.graph import TopologyGraphBuilder
from core.installer import HookInstaller
from core.linter import LinterEngine
from core.mcp import build_client_config
from core.parser import LanguageRegistry, MarkdownParser, read_text_resilient
from core.scaffold import Scaffolder
from core.server import AtlasWebServer

REPO = Path(__file__).resolve().parents[1]


def _cli_env() -> dict[str, str]:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    env.pop("PYTHONWARNINGS", None)
    return env


def _run_cli(*args: str, cwd: Path, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "omni_atlas.cli.main", *args],
        cwd=cwd,
        env=_cli_env(),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _git(cwd: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   capture_output=True, text=True)


# --------------------------------------------------------------------- #
# 1. Encoding resilience
# --------------------------------------------------------------------- #

def test_non_utf8_markdown_does_not_crash(tmp_path: Path) -> None:
    doc = tmp_path / "gbk.md"
    doc.write_bytes("# 标题\n[Link](x.py#class:Y)\n".encode("gbk"))
    parsed = MarkdownParser().parse(doc)
    assert parsed.body  # replacement-decoded instead of raising
    checks = LinterEngine(tmp_path).check_token_budgets(["gbk.md"])
    assert checks and checks[0].passed


def test_non_utf8_config_degrades_gracefully(tmp_path: Path) -> None:
    (tmp_path / ".omni-atlas.toml").write_bytes('name = "标题"\n'.encode("gbk"))
    config = load_config(tmp_path)
    assert config.custom_scm == [] and config.exclude == []


def test_non_utf8_source_still_scans(tmp_path: Path) -> None:
    source = tmp_path / "broken.cs"
    source.write_bytes(b"public class Alpha {}\n\xff\xfe")
    facts = LanguageRegistry().parse_file(source)
    assert any(symbol.name == "Alpha" for symbol in facts.symbols)


def test_read_text_resilient_retries_permission_error(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "locked.txt"
    target.write_text("hi", encoding="utf-8")
    calls = {"n": 0}
    original = Path.read_text

    def flaky(self, *args, **kwargs):
        if self == target and calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError("transient lock")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky)
    assert read_text_resilient(target) == "hi"
    assert calls["n"] == 1  # one retry was exercised


# --------------------------------------------------------------------- #
# 2. Symlink cycles
# --------------------------------------------------------------------- #

def test_recursive_scan_ignores_symlink_cycles(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "loop").symlink_to(tmp_path)  # self-referencing cycle
    changes = GitProvider().collect_all_files(tmp_path)  # non-git rglob fallback
    assert "pkg/a.py" in changes.code_files  # terminates, finds the real file


# --------------------------------------------------------------------- #
# 3. Spaces / unicode paths
# --------------------------------------------------------------------- #

def test_spaces_and_unicode_paths_end_to_end(tmp_path: Path) -> None:
    proj = tmp_path / "有 空格 proj"
    (proj / "Rug 核心").mkdir(parents=True)
    (proj / "Rug 核心" / "Engine.cs").write_text(
        "namespace R; public class Engine {}\n", encoding="utf-8"
    )
    (proj / "AGENTS.md").write_text(
        "# T\n\n[Engine](Rug 核心/Engine.cs#class:Engine)\n", encoding="utf-8"
    )

    graph = TopologyGraphBuilder(proj).build().to_dict()
    assert any(
        node["data"]["id"] == "Rug 核心/Engine.cs#class:Engine"
        for node in graph["nodes"]
    )
    checks = LinterEngine(proj).check_anchors(["AGENTS.md"])
    assert checks and checks[0].found and checks[0].lookup.line

    entry = build_client_config("cursor", proj)["mcpServers"]["omni-atlas"]
    assert str(proj) in entry["args"]  # --workspace survives unicode paths

    results = Scaffolder(proj).scaffold()
    assert all(r.path.is_file() for r in results)

    _git(proj, "init", "-q")
    install = HookInstaller(proj).install()
    assert install.hook_path.is_file()  # unicode path resolves through rev-parse


# --------------------------------------------------------------------- #
# 4. Git worktrees (.git as a file)
# --------------------------------------------------------------------- #

def test_git_worktree_init_and_scan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(worktree))
    assert (worktree / ".git").is_file()  # linked worktrees use a .git file

    changes = GitProvider().collect_all_files(worktree)
    assert "a.py" in changes.code_files

    install = HookInstaller(worktree).install()
    assert install.hook_path.is_file()
    assert HookInstaller(worktree).install().status == "unchanged"


# --------------------------------------------------------------------- #
# 5. MCP stdio purity & EOF
# --------------------------------------------------------------------- #

def test_mcp_stdout_is_pure_json_and_warnings_go_to_stderr(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init", "-q")
    (proj / "app.py").write_text("class Alpha: pass\n", encoding="utf-8")
    (proj / ".omni-atlas.toml").write_text(
        '[[custom_scm]]\nlanguage = "go"\nname = "broken"\nquery = "((("\n',
        encoding="utf-8",
    )
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "query_topology",
                               "arguments": {"keyword": "Alpha"}}}),
    ]) + "\n"

    proc = _run_cli("mcp", "--workspace", str(proj), cwd=proj, stdin=requests)
    assert proc.returncode == 0
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, "expected JSON-RPC responses on stdout"
    for line in lines:  # every stdout byte is a valid JSON-RPC message
        message = json.loads(line)
        assert message["jsonrpc"] == "2.0"
    assert "skipping invalid custom SCM" in proc.stderr  # warning on stderr only
    assert "skipping invalid custom SCM" not in proc.stdout


def test_mcp_exits_cleanly_on_immediate_eof(tmp_path: Path) -> None:
    proc = _run_cli("mcp", "--workspace", str(tmp_path), cwd=tmp_path, stdin="")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""  # no orphan output, no deadlock


# --------------------------------------------------------------------- #
# 6. Web UI port fallback
# --------------------------------------------------------------------- #

def test_ui_port_fallback_when_busy(tmp_path: Path) -> None:
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    busy_port = busy.getsockname()[1]
    server = AtlasWebServer(host="127.0.0.1", port=busy_port)
    try:
        bound = server.start()
        assert bound != busy_port
        assert busy_port < bound <= busy_port + 20
        assert server.url.endswith(str(bound))
    finally:
        server.close()
        busy.close()


def test_ui_port_zero_binds_ephemeral_port(tmp_path: Path) -> None:
    server = AtlasWebServer(host="127.0.0.1", port=0)
    try:
        bound = server.start()
        assert bound > 0
        assert server.url.endswith(str(bound))
    finally:
        server.close()


# --------------------------------------------------------------------- #
# 7. Git hook staging defence
# --------------------------------------------------------------------- #

def test_check_survives_staged_worktree_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "m.py").write_text("class A:\n    pass\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# T\n\n[A](m.py#class:A)\n", encoding="utf-8")
    _git(repo, "add", "-A")
    (repo / "m.py").unlink()  # staged content remains, worktree file is gone

    proc = _run_cli("check", cwd=repo)
    assert proc.returncode == 1  # graceful failure, not a traceback
    assert "Traceback" not in proc.stdout and "Traceback" not in proc.stderr


def test_check_with_nothing_staged_is_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    proc = _run_cli("check", cwd=repo)
    assert proc.returncode == 0
    assert "No target files detected" in proc.stdout
