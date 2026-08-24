"""OmniAtlas Linter — CLI entrypoint & command routing.

Supreme framework meta-specification: ``BLUEPRINT.md``.
Project architecture panorama: ``AGENTS.md``.
"""

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.git_provider import GitProvider, StagedChanges
from core.linter import AnchorCheck, LinterEngine, SyncCheck, TokenCheck

VERSION = "0.0.1"

app = typer.Typer(
    name="omni-atlas",
    help="OmniAtlas Linter — symbol-level static analysis for documentation integrity.",
    add_completion=False,
)
console = Console()


@app.command()
def check() -> None:
    """Run an incremental lint pass over the Git staging area."""
    console.print(
        f"[bold green]Initializing OmniAtlas Linter v{VERSION}...[/bold green]"
    )

    changes = GitProvider().collect_staged_changes()

    if not changes.code_files and not changes.doc_files:
        console.print(
            "[yellow]No staged changes detected. Everything is clean.[/yellow]"
        )
        raise typer.Exit(code=0)

    _render_changes(changes)

    engine = LinterEngine()
    anchor_checks = engine.check_anchors(changes.doc_files)
    sync_checks = engine.check_reverse_sync(changes)
    token_checks = engine.check_token_budgets(changes.doc_files)

    _render_anchor_integrity(anchor_checks)
    _render_doc_sync(sync_checks)
    _render_token_guard(token_checks)

    missing = [c for c in anchor_checks if not c.found]
    stale = [c for c in sync_checks if not c.in_sync]
    oversized = [c for c in token_checks if not c.passed]

    if missing or stale or oversized:
        _render_repair_advice(missing, stale, oversized)
        raise typer.Exit(code=1)

    console.print("[bold green]✔ All checks passed![/bold green]")
    raise typer.Exit(code=0)


def _render_changes(changes: StagedChanges) -> None:
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
        title="Staged Changes Detected",
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
