"""Typer command line interface for the MemoryForge lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from memoryforge import __version__
from memoryforge.errors import FeatureUnavailableError, MemoryForgeError
from memoryforge.importer import SourceImporter
from memoryforge.models import Sensitivity, SourceCategory
from memoryforge.workspace import Workspace

app = typer.Typer(
    add_completion=False,
    help="Git-backed, auditable knowledge workspaces for personal developers.",
    no_args_is_help=True,
)

WorkspaceOption = Annotated[
    Path,
    typer.Option(
        "--workspace",
        "-w",
        help="MemoryForge workspace; defaults to the current directory.",
    ),
]


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the installed MemoryForge version."),
    ] = False,
) -> None:
    """Manage an auditable personal developer knowledge workspace."""

    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def init(
    workspace: Annotated[
        Path,
        typer.Argument(help="Directory in which to create the workspace."),
    ],
) -> None:
    """Create Raw, Wiki, manifest, index, schema, and Git foundations."""

    try:
        created = Workspace.initialize(workspace)
    except MemoryForgeError as error:
        _fail(error)
    typer.echo(f"Initialized MemoryForge workspace: {created.root}")


@app.command("import")
def import_source(
    path: Annotated[
        Path,
        typer.Argument(help="UTF-8 Markdown or plain-text file to archive."),
    ],
    category: Annotated[
        SourceCategory,
        typer.Option("--category", "-c", help="Knowledge category for the source."),
    ] = SourceCategory.NOTES,
    tag: Annotated[
        Optional[list[str]],
        typer.Option("--tag", "-t", help="Repeatable user tag saved in the manifest."),
    ] = None,
    local_only: Annotated[
        bool,
        typer.Option(help="Prevent this source from being sent to remote providers."),
    ] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Archive one source immutably, write its manifest, and update FTS."""

    try:
        current_workspace = Workspace.open(workspace)
        result = SourceImporter(current_workspace).import_file(
            path,
            category=category,
            tags=tuple(tag or ()),
            sensitivity=Sensitivity.LOCAL_ONLY if local_only else Sensitivity.PUBLIC,
        )
    except MemoryForgeError as error:
        _fail(error)

    outcome = "duplicate" if result.duplicate else "imported"
    payload = {
        "outcome": outcome,
        "source": result.source.model_dump(mode="json"),
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def ingest(
    pending: Annotated[bool, typer.Option("--pending", help="Compile all pending sources.")] = True,
    source: Annotated[
        Optional[list[str]],
        typer.Option("--source", help="Compile only this source ID; repeat as needed."),
    ] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Compile sources into a staged ChangeSet (scheduled for milestone two)."""

    _require_future_feature(workspace, "ingest")


@app.command()
def review(
    changeset_id: Annotated[str, typer.Argument(help="ChangeSet to inspect.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Show a structured ChangeSet diff (scheduled for milestone two)."""

    _require_future_feature(workspace, "review")


@app.command()
def apply(
    changeset_id: Annotated[str, typer.Argument(help="Approved ChangeSet to apply.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Apply an approved ChangeSet to the stable Wiki (milestone two)."""

    _require_future_feature(workspace, "apply")


@app.command()
def reject(
    changeset_id: Annotated[str, typer.Argument(help="ChangeSet to reject.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Reject a ChangeSet without changing the stable Wiki (milestone two)."""

    _require_future_feature(workspace, "reject")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question for the knowledge base.")],
    as_of: Annotated[
        Optional[str],
        typer.Option("--as-of", help="Optional YYYY-MM-DD time boundary."),
    ] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Answer with citations and temporal awareness (milestone three)."""

    _require_future_feature(workspace, "ask")


@app.command()
def lint(
    fix_proposal: Annotated[
        bool,
        typer.Option("--fix-proposal", help="Propose fixes instead of writing directly."),
    ] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Inspect knowledge health without mutating the Wiki (milestone three)."""

    _require_future_feature(workspace, "lint")


@app.command()
def history(
    page: Annotated[
        Optional[str],
        typer.Option("--page", help="Limit history to one Wiki page."),
    ] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Inspect version history (scheduled after ChangeSet application exists)."""

    _require_future_feature(workspace, "history")


@app.command()
def rollback(
    commit_id: Annotated[str, typer.Argument(help="Git commit to restore.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Restore an applied ChangeSet via Git (scheduled for milestone two)."""

    _require_future_feature(workspace, "rollback")


@app.command()
def eval(
    eval_config: Annotated[Path, typer.Argument(help="Public evaluation configuration.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Run a reproducible baseline comparison (scheduled for milestone four)."""

    _require_future_feature(workspace, "eval")


def _require_future_feature(workspace: Path, command: str) -> None:
    """Verify the workspace, then report an intentionally unimplemented feature."""

    try:
        Workspace.open(workspace)
        raise FeatureUnavailableError(
            f"`{command}` is not enabled in the trusted-storage milestone. "
            "The command is registered now so its contract stays stable."
        )
    except MemoryForgeError as error:
        _fail(error)


def _fail(error: MemoryForgeError) -> None:
    """Render a recoverable domain failure consistently for CLI users."""

    typer.echo(f"error: {error}", err=True)
    raise typer.Exit(code=2)


def main() -> None:
    """Expose the console-script entry point."""

    app()
