from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from memoryforge import __version__
from memoryforge.changesets import ChangeSetStore
from memoryforge.errors import FeatureUnavailableError, MemoryForgeError, WorkspaceError
from memoryforge.importer import SourceValidationError, import_local_file
from memoryforge.models import Sensitivity
from memoryforge.workspace import (
    Workspace,
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    search_sources,
)

app = typer.Typer(
    name="memoryforge",
    no_args_is_help=True,
    add_completion=False,
    help="Git-backed immutable local knowledge storage and search.",
)

WorkspaceOption = Annotated[
    Path,
    typer.Option("--workspace", "-w", help="Initialized MemoryForge workspace."),
]


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the installed MemoryForge version."),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def init(
    workspace: Annotated[Path, typer.Argument(help="Directory to initialize.")],
) -> None:
    try:
        Workspace.initialize(workspace)
    except (MemoryForgeError, WorkspaceSecurityError, OSError, sqlite3.Error) as exc:
        _exit_with_safe_error(exc)
    typer.echo("Initialized MemoryForge workspace with a Git baseline.")


@app.command("import")
def import_command(
    path: Annotated[Path, typer.Argument(help="Local Markdown or text file.")],
    workspace: WorkspaceOption = Path("."),
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="SourceVersion category."),
    ] = "notes",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", "-t", help="Repeatable tag stored in the immutable manifest."),
    ] = None,
    local_only: Annotated[
        bool,
        typer.Option(help="Keep this source out of future remote model requests."),
    ] = False,
) -> None:
    try:
        result = import_local_file(
            workspace,
            path,
            category=category,
            source_root=Path.cwd(),
            tags=tuple(tag or ()),
            sensitivity=Sensitivity.LOCAL_ONLY if local_only else Sensitivity.PUBLIC,
        )
    except (
        MemoryForgeError,
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
    workspace: WorkspaceOption = Path("."),
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", min=1, max=100, help="Maximum result count."),
    ] = 10,
) -> None:
    try:
        results = search_sources(workspace, query, limit=limit)
    except (
        MemoryForgeError,
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


@app.command()
def ingest(
    pending: Annotated[bool, typer.Option("--pending")] = True,
    source: Annotated[list[str] | None, typer.Option("--source")] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    _ = pending, source
    _require_future_feature(workspace, "ingest")


@app.command()
def review(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        opened = Workspace.open(workspace)
        ChangeSetStore(opened).get(changeset_id)
        raise FeatureUnavailableError("`review` is not enabled in the trusted-storage milestone")
    except FeatureUnavailableError as exc:
        _exit_with_safe_error(exc)
    except (
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)


@app.command()
def apply(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    _ = changeset_id
    _require_future_feature(workspace, "apply")


@app.command()
def reject(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    _ = changeset_id
    _require_future_feature(workspace, "reject")


@app.command()
def ask(
    question: Annotated[str, typer.Argument()],
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    _ = question, as_of
    _require_future_feature(workspace, "ask")


@app.command()
def lint(
    fix_proposal: Annotated[bool, typer.Option("--fix-proposal")] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    _ = fix_proposal
    _require_future_feature(workspace, "lint")


@app.command()
def history(
    page: Annotated[str | None, typer.Option("--page")] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    _ = page
    _require_future_feature(workspace, "history")


@app.command()
def rollback(
    commit_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    _ = commit_id
    _require_future_feature(workspace, "rollback")


@app.command()
def eval(
    eval_config: Annotated[Path, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    _ = eval_config
    _require_future_feature(workspace, "eval")


def _require_future_feature(workspace: Path, command: str) -> None:
    try:
        Workspace.open(workspace)
        raise FeatureUnavailableError(
            f"`{command}` is not enabled in the trusted-storage milestone"
        )
    except FeatureUnavailableError as exc:
        _exit_with_safe_error(exc)
    except (
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)


def _exit_with_safe_error(exc: Exception) -> None:
    if isinstance(exc, FeatureUnavailableError):
        message = str(exc)
        code = 2
    elif isinstance(exc, WorkspaceIntegrityError):
        message = "workspace integrity check failed"
        code = 1
    elif isinstance(exc, (WorkspaceSecurityError, WorkspaceError)):
        message = "workspace security or configuration check failed"
        code = 1
    elif isinstance(exc, FileNotFoundError):
        message = "workspace is not initialized"
        code = 1
    elif isinstance(exc, (OSError, sqlite3.Error)):
        message = "workspace operation failed safely"
        code = 1
    else:
        message = str(exc)
        code = 1
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code) from None


def main() -> None:
    app()
