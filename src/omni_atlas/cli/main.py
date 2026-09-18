"""OmniAtlas Linter — CLI entrypoint & command routing.

Supreme framework meta-specification: ``BLUEPRINT.md``.
Project architecture panorama: ``AGENTS.md``.
"""

import json
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.diagnostics import (
    build_bug_report,
    bug_report_markdown,
    environment_info,
    scan_workspace,
)
from core.git_provider import GitProvider, StagedChanges
from core.graph import TopologyGraphBuilder
from core.installer import HookInstaller, InstallResult
from core.linter import AnchorCheck, ApiCheck, LinterEngine, SyncCheck, TokenCheck
from core.mcp import (
    CLIENT_TARGETS,
    McpServer,
    build_client_config,
    build_codex_toml,
    client_config_path,
    merge_client_config,
    merge_codex_config,
)
from core.server import AtlasWebServer, is_headless_environment

VERSION = "0.2.1"

app = typer.Typer(
    name="omni-atlas",
    help="OmniAtlas Linter — symbol-level static analysis for documentation integrity.",
    add_completion=False,
)
console = Console()


@app.command()
def check(
    all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Scan every project file instead of only the Git staging area (CI mode).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit pure machine-readable JSON (no ANSI colors, no tables).",
    ),
    exclude: list[str] = typer.Option(
        None,
        "--exclude",
        "-x",
        help="Glob to exclude (repeatable, e.g. -x '**/bin' -x '*.user').",
    ),
) -> None:
    """Run an incremental lint pass over the Git staging area.

    Default mode inspects only staged files (pre-commit fast gate);
    ``--all`` performs a full-project sweep (CI/CD pipeline mode).
    ``--json`` prints the headless report consumed by agents and CI.
    """
    provider = GitProvider()
    if all:
        changes = provider.collect_all_files(extra_excludes=exclude)
        mode = "all"
    else:
        changes = provider.collect_staged_changes(extra_excludes=exclude)
        mode = "staged"

    if not json_output:
        console.print(
            f"[bold green]Initializing OmniAtlas Linter v{VERSION}...[/bold green]"
        )

    if not changes.code_files and not changes.doc_files:
        if json_output:
            _emit_json(_build_check_payload(mode, changes, [], [], [], []))
        else:
            console.print(
                "[yellow]No target files detected. Everything is clean.[/yellow]"
            )
        raise typer.Exit(code=0)

    if not json_output:
        _render_changes(
            changes, "Full Project Scan" if all else "Staged Changes Detected"
        )

    engine = LinterEngine(extra_excludes=exclude)
    anchor_checks = engine.check_anchors(changes.doc_files)
    sync_checks = engine.check_reverse_sync(changes)
    token_checks = engine.check_token_budgets(changes.doc_files)
    api_checks = engine.check_api_links(changes)

    missing = [c for c in anchor_checks if not c.found]
    stale = [c for c in sync_checks if not c.in_sync]
    oversized = [c for c in token_checks if not c.passed]

    if json_output:
        _emit_json(
            _build_check_payload(
                mode, changes, anchor_checks, sync_checks, token_checks, api_checks
            )
        )
    else:
        _render_anchor_integrity(anchor_checks)
        _render_doc_sync(sync_checks)
        _render_token_guard(token_checks)
        _render_api_links(api_checks)

    if missing or stale or oversized:
        if not json_output:
            _render_repair_advice(missing, stale, oversized)
        raise typer.Exit(code=1)

    if not json_output:
        console.print("[bold green]✔ All checks passed![/bold green]")
    raise typer.Exit(code=0)


def _emit_json(payload: dict) -> None:
    """Print pure JSON on stdout — no rich, no ANSI, no banners."""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _build_check_payload(
    mode: str,
    changes: StagedChanges,
    anchor_checks: list[AnchorCheck],
    sync_checks: list[SyncCheck],
    token_checks: list[TokenCheck],
    api_checks: list[ApiCheck],
) -> dict:
    """Serialize every check result into the headless JSON contract."""
    missing = [c for c in anchor_checks if not c.found]
    stale = [c for c in sync_checks if not c.in_sync]
    oversized = [c for c in token_checks if not c.passed]
    unmatched = [c for c in api_checks if not c.matched]
    return {
        "ok": not (missing or stale or oversized),
        "mode": mode,
        "files": {"code": changes.code_files, "docs": changes.doc_files},
        "anchors": {
            "total": len(anchor_checks),
            "found": len(anchor_checks) - len(missing),
            "missing": [
                {
                    "doc_file": c.doc_file,
                    "target_file": c.anchor.file_path,
                    "symbol": f"{c.anchor.symbol_type}:{c.anchor.symbol_name}",
                }
                for c in missing
            ],
        },
        "sync": {
            "total": len(sync_checks),
            "stale": [
                {"code_file": c.code_file, "doc_file": c.doc_file} for c in stale
            ],
        },
        "tokens": [
            {
                "doc_file": c.doc_file,
                "level": c.level,
                "tokens": c.tokens,
                "limit": c.limit,
                "passed": c.passed,
            }
            for c in token_checks
        ],
        "api_links": {
            "total": len(api_checks),
            "linked": len(api_checks) - len(unmatched),
            "unmatched": [
                {"source_file": c.source_file, "method": c.method, "path": c.path}
                for c in unmatched
            ],
        },
    }


@app.command()
def graph(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the Cytoscape topology JSON (pure, machine-readable).",
    ),
    exclude: list[str] = typer.Option(
        None, "--exclude", "-x", help="Glob to exclude (repeatable)."
    ),
) -> None:
    """Export the project architecture graph (Cytoscape JSON with --json)."""
    builder = TopologyGraphBuilder(extra_excludes=exclude).build()
    payload = builder.to_dict()
    if json_output:
        _emit_json(payload)
        raise typer.Exit(code=0)

    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for node in payload["nodes"]:
        data = node["data"]
        by_kind[data["kind"]] = by_kind.get(data["kind"], 0) + 1
        by_status[data["status"]] = by_status.get(data["status"], 0) + 1
    lines = [
        f"nodes [bold]{payload['meta']['node_count']}[/bold] · "
        f"edges [bold]{payload['meta']['edge_count']}[/bold]",
        "",
        "kinds: " + ", ".join(f"{k}×{v}" for k, v in sorted(by_kind.items())),
        "status: " + ", ".join(f"{k}×{v}" for k, v in sorted(by_status.items())),
        "",
        "Use [bold]--json[/bold] for the full Cytoscape payload.",
    ]
    console.print(Panel("\n".join(lines), title="OmniAtlas Topology", border_style="green"))
    raise typer.Exit(code=0)


@app.command()
def init_mcp(
    target: str = typer.Option(
        "cursor", "--target", "-t",
        help="Client flavour: cursor | claude | opencode | codex.",
    ),
    workspace: str = typer.Option(
        None, "--workspace", "-w",
        help="Workspace root to analyze (default: current directory).",
    ),
    write: bool = typer.Option(
        False, "--write", help="Merge into the client config file (preserves other servers).",
    ),
    out: str = typer.Option(
        None, "--out", help="Override the destination path used with --write.",
    ),
) -> None:
    """Generate (or install) the MCP client configuration for this workspace."""
    if target not in CLIENT_TARGETS:
        text = Text("✖ Error: ", style="bold red")
        text.append(
            f"unknown target '{target}' — expected one of {', '.join(CLIENT_TARGETS)}",
            style="red",
        )
        console.print(text)
        raise typer.Exit(code=1)

    workspace_path = Path(workspace).resolve() if workspace else Path.cwd()

    if target == "codex":
        if not write:
            print(build_codex_toml(workspace_path))
            raise typer.Exit(code=0)
        destination = Path(out).resolve() if out else client_config_path("codex", workspace_path)
        try:
            merge_codex_config(destination, workspace_path)
        except ValueError as exc:
            _render_init_error(exc)
        body = (
            f"✔ MCP server registered for [bold]codex[/bold]\n"
            f"  Config: [cyan]{destination}[/cyan]\n"
            f"  Workspace: [cyan]{workspace_path}[/cyan]\n\n"
            "Everything outside \\[mcp_servers.omni-atlas] was preserved. "
            "Restart the client to pick up the new server."
        )
        console.print(Panel(body, title="OmniAtlas MCP Configured", border_style="green"))
        raise typer.Exit(code=0)

    config = build_client_config(target, workspace_path)
    entry = (config["mcp"] if target == "opencode" else config["mcpServers"])["omni-atlas"]

    if not write:
        print(json.dumps(config, ensure_ascii=False, indent=2))
        raise typer.Exit(code=0)

    destination = Path(out).resolve() if out else client_config_path(target, workspace_path)
    try:
        merge_client_config(
            destination,
            entry,
            "mcp" if target == "opencode" else "mcpServers",
        )
    except ValueError as exc:
        _render_init_error(exc)

    command = entry["command"]
    if isinstance(command, list):  # opencode stores command + args in one array
        command = " ".join(command)
    else:
        command = f"{command} {' '.join(entry['args'])}"
    body = (
        f"✔ MCP server registered for [bold]{target}[/bold]\n"
        f"  Config: [cyan]{destination}[/cyan]\n"
        f"  Command: [cyan]{command}[/cyan]\n"
        f"  Workspace: [cyan]{entry['cwd']}[/cyan]\n\n"
        "Other MCP servers in that file were preserved. Restart the client "
        "to pick up the new server."
    )
    console.print(Panel(body, title="OmniAtlas MCP Configured", border_style="green"))
    raise typer.Exit(code=0)


def _render_init_error(exc: Exception) -> None:
    text = Text("✖ Error: ", style="bold red")
    text.append(str(exc), style="red")
    console.print(text)
    raise typer.Exit(code=1)


@app.command()
def mcp() -> None:
    """Run the headless MCP server (stdio JSON-RPC) for AI coding agents."""
    McpServer().serve_forever()


@app.command()
def doctor(
    exclude: list[str] = typer.Option(
        None, "--exclude", "-x", help="Glob to exclude (repeatable)."
    ),
) -> None:
    """Environment & workspace health check (diagnostics, doctor mode)."""
    env = environment_info()
    report = scan_workspace(".", extra_excludes=exclude)

    env_table = Table(show_header=False, expand=False)
    env_table.add_column(style="bold cyan", no_wrap=True)
    env_table.add_column()
    env_table.add_row("Python", str(env.get("python")))
    env_table.add_row("Platform", str(env.get("platform")))
    env_table.add_row("tree-sitter", str(env.get("tree_sitter")))
    console.print(Panel(env_table, title="Environment", border_style="cyan"))

    grammar_table = Table(show_header=True, header_style="bold magenta")
    grammar_table.add_column("Language")
    grammar_table.add_column("Package")
    grammar_table.add_column("Version", justify="center")
    grammar_table.add_column("ABI", justify="center")
    grammar_table.add_column("Status", justify="center")
    for grammar in env.get("grammars", []):
        loaded = grammar.get("loaded")
        grammar_table.add_row(
            grammar["language"],
            grammar["package"],
            str(grammar.get("version") or "—"),
            str(grammar.get("abi") or "—"),
            "[bold green]loaded[/bold green]" if loaded else "[bold red]missing[/bold red]",
        )
    console.print(Panel(grammar_table, title="Tree-sitter Grammar Packs", border_style="cyan"))

    warnings = len(report.syntax_errors)
    summary = (
        f"files scanned [bold]{report.total_files}[/bold] · "
        f"parsed OK [bold green]{report.parsed_ok}[/bold green] · "
        f"syntax warnings [bold yellow]{warnings}[/bold yellow] · "
        f"skipped [bold]{len(report.skipped)}[/bold] · "
        f"unmatched API calls [bold]{len(report.unmatched_api)}[/bold]"
    )
    health_style = "green" if report.health >= 95 else "yellow" if report.health >= 80 else "red"
    body = f"{summary}\n\nHealth: [bold {health_style}]{report.health}%[/bold {health_style}]"
    if warnings:
        body += f" — {warnings} file(s) had syntax warnings"
    if report.syntax_errors:
        body += "\n\n[bold]Syntax warning files:[/bold]"
        for item in report.syntax_errors[:10]:
            body += (
                f"\n  • [cyan]{item['file']}[/cyan] ({item.get('language')}) — "
                f"{item['error_nodes']} error node(s), first at line "
                f"{item.get('first_error_line')}"
            )
    if report.skipped:
        body += "\n\n[bold]Skipped files:[/bold]"
        for item in report.skipped[:5]:
            body += f"\n  • [dim]{item['file']} ({item.get('reason')})[/dim]"
    body += (
        "\n\n[dim]Full diagnostics:[/dim] [bold]omni-atlas report-bug[/bold] "
        "[dim]· agent query:[/dim] [bold]diagnose_workspace[/bold]"
    )
    console.print(Panel(body, title="Workspace Health", border_style=health_style))
    raise typer.Exit(code=0)


@app.command()
def report_bug(
    json_output: bool = typer.Option(
        False, "--json", help="Emit the raw JSON report instead of Markdown."
    ),
    out: str = typer.Option(
        None, "--out", help="Write the report to a file (e.g. bug_report.md)."
    ),
) -> None:
    """Generate a sanitized bug-diagnosis report (Markdown or JSON)."""
    report = build_bug_report(".")
    text = (
        json.dumps(report, ensure_ascii=False, indent=2)
        if json_output
        else bug_report_markdown(report)
    )
    if out:
        Path(out).write_text(text, encoding="utf-8")
        console.print(
            f"[bold green]✔ Report written:[/bold green] [cyan]{out}[/cyan] "
            f"({len(text)} chars)"
        )
    else:
        print(text)  # plain print: Markdown must stay unformatted and pipeable
    raise typer.Exit(code=0)


@app.command()
def init() -> None:
    """Install the OmniAtlas pre-commit Git hook (idempotent)."""
    try:
        result = HookInstaller().install()
    except RuntimeError as exc:
        text = Text("✖ Error: ", style="bold red")
        text.append(str(exc), style="red")
        console.print(text)
        raise typer.Exit(code=1)

    _render_install_result(result)
    raise typer.Exit(code=0)


def _render_install_result(result: InstallResult) -> None:
    """Friendly Rich panel summarizing the hook installation outcome."""
    messages = {
        "created": "pre-commit hook created",
        "appended": "OmniAtlas guard appended to the existing pre-commit hook",
        "updated": "existing OmniAtlas guard block refreshed",
        "unchanged": "hook already installed — nothing to do",
    }
    body = (
        f"✔ [bold green]{messages[result.status]}[/bold green]\n"
        f"  Hook path: [cyan]{result.hook_path}[/cyan]\n\n"
        "From now on every [bold]git commit[/bold] automatically runs "
        "[bold]omni-atlas check[/bold] on the staging area.\n"
        "Try it: modify a [cyan].py[/cyan] symbol without syncing the "
        "referencing docs — the commit will be blocked."
    )
    title = (
        "OmniAtlas Git Hook Already Installed"
        if result.status == "unchanged"
        else "OmniAtlas Git Hook Installed"
    )
    console.print(Panel(body, title=title, border_style="green"))


@app.command()
def ui(
    host: str = typer.Option(
        "127.0.0.1", "--host", "-h", help="Bind address (use 0.0.0.0 for LAN access)."
    ),
    port: int = typer.Option(8080, "--port", "-p", help="Listen port."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Never auto-open a browser window."
    ),
) -> None:
    """Launch the web topology dashboard (zero-dependency stdlib server)."""
    server = AtlasWebServer(host=host, port=port)
    headless = is_headless_environment()

    try:
        if headless:
            _render_headless_panel(server.url, port)
        else:
            _render_local_panel(server.url, host)
            if not no_browser:
                webbrowser.open(server.url)
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down OmniAtlas dashboard...[/yellow]")
        raise typer.Exit(code=0)
    except OSError as exc:
        text = Text("✖ Error: ", style="bold red")
        text.append(
            f"Cannot bind {host}:{port} ({exc.strerror or exc}). "
            "Pick another port with --port.",
            style="red",
        )
        console.print(text)
        raise typer.Exit(code=1)


def _render_local_panel(url: str, host: str) -> None:
    """Startup panel for desktop sessions where a browser will open."""
    body = (
        f"Dashboard URL: [bold cyan]{url}[/bold cyan]\n"
        f"Bound to: [cyan]{host}[/cyan]\n\n"
        "Click a node to inspect its blast radius and metadata.\n"
        "Press [bold]Ctrl+C[/bold] to stop the server."
    )
    console.print(Panel(body, title="OmniAtlas Topology Dashboard", border_style="green"))


def _render_headless_panel(url: str, port: int) -> None:
    """Startup panel for SSH / Docker / display-less environments."""
    body = (
        "[yellow]Headless / remote environment detected — "
        "browser auto-open skipped.[/yellow]\n\n"
        f"Dashboard URL (on this machine): [bold cyan]{url}[/bold cyan]\n\n"
        "From your [bold]local[/bold] workstation, forward the port:\n"
        f"  [bold cyan]ssh -L {port}:127.0.0.1:{port} <user>@<this-host>[/bold cyan]\n"
        f"Then open [bold cyan]http://127.0.0.1:{port}[/bold cyan] locally.\n\n"
        "Press [bold]Ctrl+C[/bold] to stop the server."
    )
    console.print(
        Panel(body, title="OmniAtlas Topology Dashboard (Remote)", border_style="yellow")
    )


def _render_changes(changes: StagedChanges, title: str) -> None:
    """Pretty-print staged code/doc files as a rich table inside a panel."""
    table = Table(show_header=True, header_style="bold magenta", expand=False)
    table.add_column("Type", justify="center", no_wrap=True)
    table.add_column("File Path")

    for path in changes.code_files:
        table.add_row("[cyan]CODE[/cyan]", path)
    for path in changes.doc_files:
        table.add_row("[blue]DOC[/blue]", path)

    panel = Panel(
        table,
        title=title,
        subtitle=(
            f"{len(changes.code_files)} code · {len(changes.doc_files)} docs"
        ),
        border_style="green",
    )
    console.print(panel)


def _render_anchor_integrity(checks: list[AnchorCheck]) -> None:
    """Section 1: [FOUND] / [NOT FOUND] forward anchor resolution."""
    if not checks:
        console.print(
            "[yellow]No symbol anchors found in staged documents.[/yellow]"
        )
        return

    table = Table(show_header=True, header_style="bold magenta", expand=False)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Symbol")
    table.add_column("Target File")
    table.add_column("Doc", no_wrap=True)
    table.add_column("Line", justify="right")

    found_count = sum(1 for c in checks if c.found)
    for c in checks:
        if c.found:
            status = "[bold green][FOUND][/bold green]"
            line = str(c.lookup.line)
        else:
            status = "[bold red][NOT FOUND][/bold red]"
            line = "—"
        table.add_row(
            status,
            f"{c.anchor.symbol_type}:{c.anchor.symbol_name}",
            c.anchor.file_path,
            c.doc_file,
            line,
        )

    _report_panel(
        table,
        "Anchor Integrity",
        f"{found_count} found · {len(checks) - found_count} missing",
        found_count == len(checks),
    )


def _render_doc_sync(checks: list[SyncCheck]) -> None:
    """Section 2: [IN SYNC] / [STALE DOC] reverse synchronization."""
    if not checks:
        console.print(
            "[yellow]No staged code files are referenced by project docs.[/yellow]"
        )
        return

    table = Table(show_header=True, header_style="bold magenta", expand=False)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Code File")
    table.add_column("Referencing Doc")

    in_sync_count = sum(1 for c in checks if c.in_sync)
    for c in checks:
        status = (
            "[bold green][IN SYNC][/bold green]"
            if c.in_sync
            else "[bold red][STALE DOC][/bold red]"
        )
        table.add_row(status, c.code_file, c.doc_file)

    _report_panel(
        table,
        "Doc Synchronization",
        f"{in_sync_count} in sync · {len(checks) - in_sync_count} stale",
        in_sync_count == len(checks),
    )


def _render_token_guard(checks: list[TokenCheck]) -> None:
    """Section 3: [PASS] / [OVERSIZED] token budget monitoring."""
    if not checks:
        console.print("[yellow]No staged documents to measure.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta", expand=False)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Doc")
    table.add_column("Level", justify="center")
    table.add_column("Tokens / Limit", justify="right")

    pass_count = sum(1 for c in checks if c.passed)
    for c in checks:
        status = (
            "[bold green][PASS][/bold green]"
            if c.passed
            else "[bold red][OVERSIZED][/bold red]"
        )
        style = "" if c.passed else "bold red"
        table.add_row(status, c.doc_file, c.level, f"{c.tokens} / {c.limit}", style=style)

    _report_panel(
        table,
        "Token Usage Guard",
        f"{pass_count} pass · {len(checks) - pass_count} oversized",
        pass_count == len(checks),
    )


def _report_panel(table: Table, title: str, subtitle: str, ok: bool) -> None:
    """Wrap a section table in a consistently styled report panel."""
    console.print(
        Panel(table, title=title, subtitle=subtitle, border_style="green" if ok else "red")
    )


def _render_api_links(checks: list[ApiCheck]) -> None:
    """Section 4: [LINKED] / [UNMATCHED] cross-language API audit."""
    if not checks:
        return

    table = Table(show_header=True, header_style="bold magenta", expand=False)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Method", justify="center", no_wrap=True)
    table.add_column("Endpoint")
    table.add_column("Frontend", no_wrap=True)
    table.add_column("Backend", no_wrap=True)

    linked_count = 0
    for c in checks:
        if c.matched:
            linked_count += 1
            status = "[bold green][LINKED][/bold green]"
            target = f"{c.target_file} ➔ {c.target_symbol}"
        else:
            status = "[bold yellow][UNMATCHED][/bold yellow]"
            target = "—"
        table.add_row(status, c.method, c.path, c.source_file, target)

    console.print(
        Panel(
            table,
            title="Cross-Language API Links (informational)",
            subtitle=f"{linked_count} linked · {len(checks) - linked_count} unmatched",
            border_style="green" if linked_count == len(checks) else "yellow",
        )
    )


def _render_repair_advice(
    missing: list[AnchorCheck],
    stale: list[SyncCheck],
    oversized: list[TokenCheck],
) -> None:
    """Friendly fix-it guidance emitted right before a blocking exit."""
    lines: list[str] = []
    if missing:
        lines.append(
            f"• [bold red]Anchor Integrity:[/bold red] {len(missing)} anchor(s) do not "
            "exist in the codebase. Update or remove the broken links in the docs above."
        )
    if stale:
        docs = sorted({c.doc_file for c in stale})
        lines.append(
            f"• [bold red]Doc Synchronization:[/bold red] Code was modified, but the "
            "referencing docs were not updated in sync!\n"
            f"  Update and [bold]git add[/bold]: {', '.join(docs)}"
        )
    if oversized:
        docs = sorted({c.doc_file for c in oversized})
        lines.append(
            f"• [bold red]Token Guard:[/bold red] {', '.join(docs)} exceed the token "
            "ceiling. Split the document into smaller Chunks (BLUEPRINT §1)."
        )
    console.print(
        Panel("\n".join(lines), title="Repair Suggestions", border_style="red")
    )


if __name__ == "__main__":
    app()
