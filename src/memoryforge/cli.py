from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from memoryforge.importer import SourceValidationError, import_local_file
from memoryforge.workspace import (
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    init_workspace,
    search_sources,
)

app = typer.Typer(
    name="memoryforge",
    no_args_is_help=True,
    help="Import immutable local text snapshots and search them with SQLite FTS5.",
)


@app.command()
def init(
    workspace: Annotated[Path, typer.Argument(help="Directory to initialize.")],
) -> None:
    try:
        init_workspace(workspace)
    except (WorkspaceSecurityError, OSError, sqlite3.Error) as exc:
        _exit_with_safe_error(exc)
    typer.echo("Initialized MemoryForge workspace.")


@app.command("import")
def import_command(
    path: Annotated[Path, typer.Argument(help="Local Markdown or text file.")],
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Initialized workspace directory."),
    ] = Path("."),
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="Lowercase SourceVersion category."),
    ] = "notes",
) -> None:
    try:
        result = import_local_file(
            workspace,
            path,
            category=category,
            source_root=Path.cwd(),
        )
    except (
        SourceValidationError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Full-text search query.")],
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Initialized workspace directory."),
    ] = Path("."),
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", min=1, max=100, help="Maximum result count."),
    ] = 10,
) -> None:
    try:
        results = search_sources(workspace, query, limit=limit)
    except (
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(
        json.dumps(
            [result.model_dump(mode="json") for result in results],
            ensure_ascii=False,
            indent=2,
        )
    )


def _exit_with_safe_error(exc: Exception) -> None:
    if isinstance(exc, WorkspaceSecurityError):
        message = "workspace security check failed"
    elif isinstance(exc, FileNotFoundError):
        message = "workspace is not initialized"
    elif isinstance(exc, (OSError, sqlite3.Error)):
        message = "workspace operation failed safely"
    else:
        message = str(exc)
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1) from None
