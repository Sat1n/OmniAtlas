"""Project configuration loader (``.omni-atlas.toml``).

Parses the optional repo-root config file:

.. code-block:: toml

    [[custom_scm]]
    language = "go"
    name = "interface"
    query = "(type_spec name: (type_identifier) @symbol)"

    [scan]
    exclude = [".vs", "**/bin", "*.user"]

Each ``[[custom_scm]]`` entry extends the multi-language parser registry
with a custom SCM query — inline (``query``) or loaded from a file
(``path``). Captures named ``@symbol`` become graph symbols labelled
with the entry ``name``.

The ``[scan]`` table declares exclusion globs applied to every file
discovery path (in addition to ``.omniignore`` and ``.gitignore``).

Fault tolerance is a hard requirement: a malformed TOML file or an
invalid SCM query only produces a warning and is skipped — the base AST
pipeline must never break because of user configuration.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from core.diagnostics import KIND_INVALID_CUSTOM_SCM, get_collector

#: Configuration file looked up at the repository root.
CONFIG_FILENAME = ".omni-atlas.toml"


@dataclass
class CustomScm:
    """One validated ``[[custom_scm]]`` entry."""

    language: str
    name: str
    query: str | None = None
    path: str | None = None


@dataclass
class ProjectConfig:
    """Aggregated project configuration."""

    custom_scm: list[CustomScm] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


def load_config(repo_root: str | Path = ".") -> ProjectConfig:
    """Load the project config, degrading gracefully on any error.

    @shape return: ProjectConfig(custom_scm)
    @source file: filesystem#path:.omni-atlas.toml
    """
    config_path = Path(repo_root) / CONFIG_FILENAME
    if not config_path.is_file():
        return ProjectConfig()
    try:
        # Undecodable bytes degrade to replacement chars and surface as a
        # TOML decode error below instead of crashing the whole command.
        data = tomllib.loads(
            config_path.read_text(encoding="utf-8", errors="replace")
        )
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        _warn(f"ignoring {CONFIG_FILENAME}: {exc}")
        return ProjectConfig()

    scan = data.get("scan") or {}
    if isinstance(scan, dict):
        raw_exclude = scan.get("exclude") or []
        if isinstance(raw_exclude, list):
            config_exclude = [str(p).strip() for p in raw_exclude if str(p).strip()]
        else:
            _warn(f"ignoring {CONFIG_FILENAME}: [scan].exclude must be a list")
            config_exclude = []
    else:
        _warn(f"ignoring {CONFIG_FILENAME}: [scan] must be a table")
        config_exclude = []

    entries = data.get("custom_scm", [])
    if not isinstance(entries, list):
        _warn(f"ignoring {CONFIG_FILENAME}: [[custom_scm]] must be a list")
        return ProjectConfig(exclude=config_exclude)

    config = ProjectConfig()
    for raw in entries:
        if not isinstance(raw, dict) or not str(raw.get("language", "")).strip():
            _warn("skipping a [[custom_scm]] entry without a language")
            get_collector(Path(repo_root)).record(
                KIND_INVALID_CUSTOM_SCM,
                CONFIG_FILENAME,
                "[[custom_scm]] entry without a language",
            )
            continue
        query = raw.get("query")
        path = raw.get("path")
        if not query and not path:
            _warn(f"skipping [[custom_scm]] '{raw.get('name', '?')}': needs query or path")
            get_collector(Path(repo_root)).record(
                KIND_INVALID_CUSTOM_SCM,
                CONFIG_FILENAME,
                f"[[custom_scm]] '{raw.get('name', '?')}' needs query or path",
            )
            continue
        config.custom_scm.append(
            CustomScm(
                language=str(raw["language"]).strip(),
                name=str(raw.get("name", "custom")).strip() or "custom",
                query=str(query) if query else None,
                path=str(path) if path else None,
            )
        )
    config.exclude = config_exclude
    return config


def _warn(message: str) -> None:
    """Emit a non-fatal configuration warning to stderr (stdout stays clean)."""
    Console(stderr=True).print(f"[yellow]omni-atlas: {message}[/yellow]")
