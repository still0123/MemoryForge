from __future__ import annotations

import difflib
import json
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import typer

from memoryforge import __version__
from memoryforge.changesets import ChangeSetStore
from memoryforge.compiler import compilation_payload, compile_pending_sources
from memoryforge.errors import FeatureUnavailableError, MemoryForgeError, WorkspaceError
from memoryforge.importer import SourceValidationError, import_local_file
from memoryforge.models import Sensitivity
from memoryforge.query import answer_question
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
    if not pending:
        _exit_with_safe_error(ValueError("ingest currently requires --pending"))
    try:
        opened = Workspace.open(workspace)
        compilation = compile_pending_sources(
            opened,
            source_ids=tuple(source or ()),
        )
        if compilation is None:
            typer.echo(
                json.dumps(
                    {"status": "no_pending", "pending": []},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        stored = ChangeSetStore(opened).create(
            compilation.changeset,
            compilation.candidate_files,
        )
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
    typer.echo(json.dumps(compilation_payload(stored), ensure_ascii=False, indent=2))


@app.command()
def review(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        opened = Workspace.open(workspace)
        stored = ChangeSetStore(opened).get(changeset_id)
        diffs: dict[str, str] = {}
        for path, candidate in sorted(stored.candidate_files.items()):
            stable_path = opened.root / path
            stable = stable_path.read_text(encoding="utf-8") if stable_path.is_file() else ""
            diffs[path] = "".join(
                difflib.unified_diff(
                    stable.splitlines(keepends=True),
                    candidate.splitlines(keepends=True),
                    fromfile=path,
                    tofile=f"{path} (proposed)",
                )
            )
    except (
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(
        json.dumps(
            {
                "changeset_id": stored.changeset.changeset_id,
                "status": stored.changeset.status.value,
                "candidate_files": stored.candidate_files,
                "unified_diff": diffs,
                "claims": [claim.model_dump(mode="json") for claim in stored.changeset.claims],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def apply(
    changeset_id: Annotated[str, typer.Argument()],
    approve: Annotated[bool, typer.Option("--approve")] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    if not approve:
        _exit_with_safe_error(ValueError("apply requires explicit --approve"))
    try:
        opened = Workspace.open(workspace)
        store = ChangeSetStore(opened)
        stored = store.get(changeset_id)
        paths = tuple(sorted(stored.candidate_files))
        opened.version_store.require_clean_paths(paths)
        previous_files: dict[Path, str | None] = {}
        try:
            for path, content in stored.candidate_files.items():
                destination = opened.root / path
                previous_files[destination] = (
                    destination.read_text(encoding="utf-8") if destination.is_file() else None
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8")
            commit = opened.version_store.commit_paths(
                paths,
                f"knowledge: apply {changeset_id}",
            )
        except Exception:
            for destination, previous in previous_files.items():
                if previous is None:
                    destination.unlink(missing_ok=True)
                else:
                    destination.write_text(previous, encoding="utf-8")
            with suppress(MemoryForgeError):
                opened.version_store.reset_paths(paths)
            raise
        archive_warning = None
        try:
            store.archive_applied(stored, commit=commit)
        except MemoryForgeError as exc:
            archive_warning = str(exc)
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
            {
                "changeset_id": changeset_id,
                "status": "APPLIED",
                "commit": commit,
                "files": list(paths),
                "warning": archive_warning,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


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
    try:
        if as_of is not None:
            raise FeatureUnavailableError("--as-of is not supported yet")
        opened = Workspace.open(workspace)
        result = answer_question(opened.root, question)
    except (
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


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
