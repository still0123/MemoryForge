from __future__ import annotations

import difflib
import json
import sqlite3
import time
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import typer

from memoryforge import __version__
from memoryforge.agent import run_agent
from memoryforge.changesets import ChangeSetStore
from memoryforge.code_index import build_code_index
from memoryforge.code_wiki_compiler import compile_code_wiki
from memoryforge.compiler import (
    compilation_payload,
    compile_pending_sources,
    compile_repository_topics,
)
from memoryforge.errors import FeatureUnavailableError, MemoryForgeError, WorkspaceError
from memoryforge.evaluation import run_evaluation
from memoryforge.feishu_adapter import FeishuDocumentError, import_feishu_document
from memoryforge.feishu_bot import FeishuBotError, reply_to_feishu_text
from memoryforge.feishu_service import FeishuServiceError, serve_feishu_bot
from memoryforge.folder_adapter import sync_folder
from memoryforge.git_adapter import GitRepositoryError
from memoryforge.github_thread_adapter import (
    delete_github_thread,
    import_github_thread,
    import_github_thread_json,
)
from memoryforge.importer import SourceValidationError, import_local_file
from memoryforge.linting import lint_workspace
from memoryforge.models import ChangeOperationType, Sensitivity
from memoryforge.module_planner import build_module_plan
from memoryforge.provider import OpenAICompatibleProvider, ProviderConfig
from memoryforge.query import answer_question
from memoryforge.refresh import refresh_workspace
from memoryforge.sessions import SessionStore
from memoryforge.showcase import build_showcase
from memoryforge.web_adapter import WebPageError, import_html_file, import_web_page
from memoryforge.wiki_facts import IndexedWikiFact, parse_page_facts
from memoryforge.workspace import (
    Workspace,
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    candidate_page_sources,
    list_git_checkouts,
    register_git_checkout,
    register_git_code_module,
    search_sources,
    sync_git_checkout,
    validate_changeset_page_sources,
)

app = typer.Typer(
    name="memoryforge",
    no_args_is_help=True,
    add_completion=False,
    help="Git-backed immutable local knowledge storage and search.",
)
showcase_app = typer.Typer(no_args_is_help=True, help="Build a read-only static Showcase.")
app.add_typer(showcase_app, name="showcase")

WorkspaceOption = Annotated[
    Path,
    typer.Option("--workspace", "-w", help="Initialized MemoryForge workspace."),
]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed MemoryForge version.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    del version


@showcase_app.command("build")
def showcase_build(
    output: Annotated[
        Path,
        typer.Option("--output", help="Absent, empty, or MemoryForge-owned output directory."),
    ],
    workspace: WorkspaceOption = Path("."),
    evidence: Annotated[
        Path | None,
        typer.Option("--evidence", help="Optional public query and benchmark Evidence JSON."),
    ] = None,
    include_local: Annotated[
        bool,
        typer.Option(
            "--include-local",
            help="Explicitly include local_only metadata, pages, and ChangeSets.",
        ),
    ] = False,
) -> None:
    try:
        result = build_showcase(
            workspace,
            output,
            evidence=evidence,
            include_local=include_local,
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
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


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


@app.command("folder-import")
def folder_import(
    folder: Annotated[Path, typer.Argument(help="Local folder to import recursively.")],
    workspace: WorkspaceOption = Path("."),
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="SourceVersion category."),
    ] = "refs",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", "-t", help="Repeatable tag stored in immutable manifests."),
    ] = None,
    public: Annotated[
        bool,
        typer.Option("--public", help="Allow imported folder sources in remote model requests."),
    ] = False,
) -> None:
    """Import one deterministic local folder snapshot without following links."""
    try:
        result = sync_folder(
            workspace,
            folder,
            category=category,
            tags=tuple(tag or ()),
            sensitivity=Sensitivity.PUBLIC if public else Sensitivity.LOCAL_ONLY,
        )
    except (
        MemoryForgeError,
        SourceValidationError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command("github-thread-import")
def github_thread_import(
    url: Annotated[str, typer.Argument(help="One public GitHub Issue or Pull Request URL.")],
    workspace: WorkspaceOption = Path("."),
    save_json: Annotated[
        Path | None,
        typer.Option("--save-json", help="Write normalized JSON for offline replay."),
    ] = None,
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="SourceVersion category."),
    ] = "refs",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", "-t", help="Repeatable tag stored in the immutable manifest."),
    ] = None,
    local_only: Annotated[
        bool,
        typer.Option("--local-only", help="Keep the public thread out of remote model requests."),
    ] = False,
) -> None:
    """Fetch and import exactly one public GitHub thread."""
    try:
        result = import_github_thread(
            workspace,
            url,
            save_json=save_json,
            category=category,
            tags=tuple(tag or ()),
            sensitivity=Sensitivity.LOCAL_ONLY if local_only else Sensitivity.PUBLIC,
        )
    except (
        MemoryForgeError,
        SourceValidationError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command("github-thread-import-json")
def github_thread_import_json(
    path: Annotated[Path, typer.Argument(help="Saved normalized GitHub thread JSON.")],
    workspace: WorkspaceOption = Path("."),
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="SourceVersion category."),
    ] = "refs",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", "-t", help="Repeatable tag stored in the immutable manifest."),
    ] = None,
    local_only: Annotated[
        bool,
        typer.Option("--local-only", help="Keep the public thread out of remote model requests."),
    ] = False,
) -> None:
    """Replay one saved GitHub thread snapshot without network access."""
    try:
        result = import_github_thread_json(
            workspace,
            path,
            source_root=path.expanduser().parent,
            category=category,
            tags=tuple(tag or ()),
            sensitivity=Sensitivity.LOCAL_ONLY if local_only else Sensitivity.PUBLIC,
        )
    except (
        MemoryForgeError,
        SourceValidationError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command("github-thread-delete")
def github_thread_delete(
    url: Annotated[str, typer.Argument(help="Imported GitHub Issue or Pull Request URL.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Deactivate one GitHub thread while retaining immutable history."""
    try:
        result = delete_github_thread(workspace, url)
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
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command("git-add")
def git_add(
    checkout: Annotated[Path, typer.Argument(help="Existing local Git checkout.")],
    public: Annotated[
        bool,
        typer.Option(
            "--public",
            help="Allow this checkout's synced documents to be sent to an LLM.",
        ),
    ] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Register an existing local checkout without cloning or fetching."""
    try:
        result = register_git_checkout(
            workspace,
            checkout,
            sensitivity=Sensitivity.PUBLIC if public else Sensitivity.LOCAL_ONLY,
        )
    except (
        GitRepositoryError,
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command("git-list")
def git_list(workspace: WorkspaceOption = Path(".")) -> None:
    """List registered local Git checkouts."""
    try:
        results = list_git_checkouts(workspace)
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
            [result.model_dump(mode="json") for result in results],
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("git-sync")
def git_sync(
    repository_id: Annotated[str, typer.Argument(help="Registered Git repository ID.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Import committed documentation from one registered local checkout."""
    try:
        result = sync_git_checkout(workspace, repository_id)
    except (
        GitRepositoryError,
        MemoryForgeError,
        SourceValidationError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command("code-add")
def code_add(
    repository_id: Annotated[str, typer.Argument(help="Registered Git repository ID.")],
    path: Annotated[
        str,
        typer.Argument(help="Committed Go, Python, TypeScript, or TSX file/directory."),
    ],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Select one code module; the next sync imports supported source files."""
    try:
        selected = register_git_code_module(workspace, repository_id, path)
    except (
        GitRepositoryError,
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
            {"repository_id": repository_id, "path": selected},
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("web-import")
def web_import(
    url: Annotated[str, typer.Argument(help="One public HTTP(S) article URL.")],
    workspace: WorkspaceOption = Path("."),
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="SourceVersion category."),
    ] = "refs",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", "-t", help="Repeatable tag stored in the immutable manifest."),
    ] = None,
    local_only: Annotated[
        bool,
        typer.Option(help="Keep this page out of future remote model requests."),
    ] = False,
) -> None:
    """Import one readable public web page; no crawling or login is used."""
    try:
        result = import_web_page(
            workspace,
            url,
            category=category,
            tags=tuple(tag or ()),
            sensitivity=Sensitivity.LOCAL_ONLY if local_only else Sensitivity.PUBLIC,
        )
    except (
        WebPageError,
        MemoryForgeError,
        SourceValidationError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command("html-import")
def html_import(
    path: Annotated[Path, typer.Argument(help="Browser-saved .html or .htm file.")],
    url: Annotated[str, typer.Option("--url", help="Original public article URL.")],
    workspace: WorkspaceOption = Path("."),
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="SourceVersion category."),
    ] = "refs",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", "-t", help="Repeatable tag stored in the immutable manifest."),
    ] = None,
    local_only: Annotated[
        bool,
        typer.Option(help="Keep this page out of future remote model requests."),
    ] = False,
) -> None:
    """Convert one saved HTML page locally; no browser profile is read."""
    try:
        result = import_html_file(
            workspace,
            path,
            url=url,
            category=category,
            tags=tuple(tag or ()),
            sensitivity=Sensitivity.LOCAL_ONLY if local_only else Sensitivity.PUBLIC,
        )
    except (
        WebPageError,
        MemoryForgeError,
        SourceValidationError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command()
def refresh(workspace: WorkspaceOption = Path(".")) -> None:
    """Manually refresh all registered local Git and Feishu sources once."""
    try:
        opened = Workspace.open(workspace)
        result = refresh_workspace(opened.root)
    except (
        FeishuDocumentError,
        GitRepositoryError,
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
                "status": "changed" if result.changed else "unchanged",
                "git": [
                    {
                        "repository_id": item.repository_id,
                        "head_commit": item.head_commit,
                        "created": item.created,
                        "updated": item.updated,
                        "unchanged": item.unchanged,
                    }
                    for item in result.git
                ],
                "feishu": [
                    {
                        "document_id": item.document_id,
                        "created": item.created,
                        "updated": item.updated,
                        "unchanged": item.unchanged,
                    }
                    for item in result.feishu
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def watch(
    interval: Annotated[
        float,
        typer.Option("--interval", min=1, help="Seconds between refresh passes."),
    ] = 60,
    once: Annotated[
        bool,
        typer.Option("--once", help="Run one refresh-and-compile pass, then exit."),
    ] = False,
    llm: Annotated[
        bool,
        typer.Option("--llm", help="Use the configured model when compiling changes."),
    ] = False,
    allow_local_llm: Annotated[
        bool,
        typer.Option(
            "--allow-local-llm",
            help="Allow the configured model to receive local_only sources.",
        ),
    ] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Watch registered sources and stage reviewable Wiki updates."""
    try:
        opened = Workspace.open(workspace)
        provider = OpenAICompatibleProvider(ProviderConfig.from_environment()) if llm else None
        first_pass = True
        typer.echo(
            f"Watching registered sources every {interval:g}s. Press Ctrl+C to stop.",
            err=True,
        )
        while True:
            refreshed = refresh_workspace(opened.root)
            stored = None
            if first_pass or refreshed.changed:
                compilation = compile_pending_sources(
                    opened,
                    provider=provider,
                    allow_local=allow_local_llm,
                )
                if compilation is not None:
                    stored = ChangeSetStore(opened).create(
                        compilation.changeset,
                        compilation.candidate_files,
                    )

            if first_pass or refreshed.changed or stored is not None:
                payload: dict[str, object] = {
                    "status": "changed" if refreshed.changed else "unchanged",
                    "git": [
                        {
                            "repository_id": item.repository_id,
                            "head_commit": item.head_commit,
                            "created": item.created,
                            "updated": item.updated,
                            "unchanged": item.unchanged,
                        }
                        for item in refreshed.git
                    ],
                    "feishu": [
                        {
                            "document_id": item.document_id,
                            "created": item.created,
                            "updated": item.updated,
                            "unchanged": item.unchanged,
                        }
                        for item in refreshed.feishu
                    ],
                }
                if stored is not None:
                    payload["status"] = "proposed"
                    payload["changeset"] = compilation_payload(stored)
                typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
            if once:
                return
            first_pass = False
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("Watcher stopped.", err=True)
    except (
        FeishuDocumentError,
        GitRepositoryError,
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)


@app.command("topics")
def topics(
    repository_id: Annotated[str, typer.Argument(help="Public Git repository ID.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Use the configured model to organize one public repository's Wiki pages."""
    try:
        opened = Workspace.open(workspace)
        typer.echo("正在请求模型整理主题（通常需要几十秒）…", err=True)
        compilation = compile_repository_topics(
            opened,
            repository_id,
            OpenAICompatibleProvider(ProviderConfig.from_environment()),
        )
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


@app.command("feishu-import")
def feishu_import(
    document: Annotated[
        str,
        typer.Argument(help="Readable Feishu/Lark Docx or Wiki URL, or token."),
    ],
    workspace: WorkspaceOption = Path("."),
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="SourceVersion category."),
    ] = "notes",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", "-t", help="Repeatable source tag."),
    ] = None,
) -> None:
    """Import one current Feishu document through the user-authorized lark-cli."""
    try:
        results = import_feishu_document(
            workspace,
            document,
            category=category,
            tags=tuple(tag or ()),
        )
    except (
        FeishuDocumentError,
        MemoryForgeError,
        SourceValidationError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    status = next(
        (
            value
            for value in ("created", "updated")
            if any(result.status == value for result in results)
        ),
        "unchanged",
    )
    typer.echo(
        json.dumps(
            {
                "status": status,
                "sources": [result.model_dump(mode="json") for result in results],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("feishu-reply")
def feishu_reply(
    question: Annotated[str, typer.Argument(help="Text received by a Feishu bot.")],
    max_pages: Annotated[
        int,
        typer.Option("--max-pages", min=1, max=10, help="Maximum Wiki pages to search."),
    ] = 3,
    llm: Annotated[
        bool,
        typer.Option("--llm", help="Summarize matched Wiki evidence with the configured model."),
    ] = False,
    allow_local_llm: Annotated[
        bool,
        typer.Option(
            "--allow-local-llm",
            help="Allow the configured model to receive local_only evidence for this reply.",
        ),
    ] = False,
    session: Annotated[
        str | None,
        typer.Option("--session", help="Reuse this local conversation session."),
    ] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Print the reply payload that a Feishu bot can send back to one text message."""
    try:
        opened = (
            Workspace.open(workspace) if session is not None else Workspace.open_readonly(workspace)
        )
        provider = OpenAICompatibleProvider(ProviderConfig.from_environment()) if llm else None
        reply = reply_to_feishu_text(
            opened.root,
            question,
            max_pages=max_pages,
            provider=provider,
            allow_local=allow_local_llm,
            session_id=session,
        )
    except (
        FeishuBotError,
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(reply, ensure_ascii=False, indent=2))


@app.command("feishu-serve")
def feishu_serve(
    max_pages: Annotated[
        int,
        typer.Option("--max-pages", min=1, max=10, help="Maximum Wiki pages to search."),
    ] = 3,
    llm: Annotated[
        bool,
        typer.Option("--llm", help="Summarize matched Wiki evidence with the configured model."),
    ] = False,
    allow_local_llm: Annotated[
        bool,
        typer.Option(
            "--allow-local-llm",
            help="Allow the configured model to receive local_only evidence for replies.",
        ),
    ] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Listen for direct Feishu bot messages and reply from the local Wiki."""
    try:
        opened = Workspace.open(workspace)
        typer.echo("Listening for Feishu bot messages. Press Ctrl+C to stop.")
        provider = OpenAICompatibleProvider(ProviderConfig.from_environment()) if llm else None
        serve_feishu_bot(
            opened.root,
            max_pages=max_pages,
            provider=provider,
            allow_local=allow_local_llm,
        )
    except KeyboardInterrupt:
        typer.echo("Feishu listener stopped.")
    except (
        FeishuBotError,
        FeishuServiceError,
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Full-text search query.")],
    workspace: WorkspaceOption = Path("."),
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", min=1, max=100, help="Maximum result count."),
    ] = 10,
    repository_id: Annotated[
        str | None,
        typer.Option("--repository", help="Limit results to one registered Git repository."),
    ] = None,
) -> None:
    try:
        results = search_sources(
            workspace,
            query,
            limit=limit,
            repository_id=repository_id,
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
    llm: Annotated[
        bool,
        typer.Option("--llm", help="Use the configured OpenAI-compatible PageChange provider."),
    ] = False,
    allow_local_llm: Annotated[
        bool,
        typer.Option(
            "--allow-local-llm",
            help="Allow the configured model to receive local_only sources for this run.",
        ),
    ] = False,
    code_wiki: Annotated[
        str | None,
        typer.Option(
            "--code-wiki",
            help="Compile one registered Git repository into a reviewable code Wiki.",
        ),
    ] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    if not pending:
        _exit_with_safe_error(ValueError("ingest currently requires --pending"))
    if code_wiki is not None and (source or llm or allow_local_llm):
        _exit_with_safe_error(
            ValueError("--code-wiki cannot be combined with --source or LLM options")
        )
    try:
        opened = Workspace.open(workspace)
        if code_wiki is not None:
            snapshot = build_code_index(opened, code_wiki)
            plan = build_module_plan(snapshot)
            compilation = compile_code_wiki(opened, snapshot, plan)
        else:
            provider = OpenAICompatibleProvider(ProviderConfig.from_environment()) if llm else None
            compilation = compile_pending_sources(
                opened,
                source_ids=tuple(source or ()),
                provider=provider,
                allow_local=allow_local_llm,
            )
        if compilation is None:
            typer.echo(
                json.dumps(
                    (
                        {"status": "up_to_date", "repository_id": code_wiki}
                        if code_wiki is not None
                        else {"status": "no_pending", "pending": []}
                    ),
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
        with opened.exclusive_lock():
            store = ChangeSetStore(opened)
            stored = store.get(changeset_id)
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
            reviewed = store.record_review(stored)
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
                "reviewed_at": reviewed.reviewed_at.isoformat(),
                "candidate_files": stored.candidate_files,
                "unified_diff": diffs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def approve(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        opened = Workspace.open(workspace)
        with opened.exclusive_lock():
            store = ChangeSetStore(opened)
            stored = store.get(changeset_id)
            approval = store.approve(stored)
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
                "status": approval.status,
                "approved_at": approval.approved_at.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def apply(
    changeset_id: Annotated[str, typer.Argument()],
    approve: Annotated[
        bool,
        typer.Option(
            "--approve",
            help="Legacy shortcut that records review and approval before applying.",
        ),
    ] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        opened = Workspace.open(workspace)
        with opened.exclusive_lock():
            store = ChangeSetStore(opened)
            stored = store.get(changeset_id)
            if approve:
                store.record_review(stored, mode="inline_legacy")
                store.approve(stored)
            else:
                store.require_approved(stored)
            archive_paths = tuple(
                sorted(
                    operation.path
                    for operation in stored.changeset.operations
                    if operation.type is ChangeOperationType.ARCHIVE_PAGE
                )
            )
            if any(not path.startswith("wiki/pages/") for path in archive_paths):
                raise ValueError("ARCHIVE_PAGE operations must target wiki/pages/")
            existing_archive_paths = tuple(
                path for path in archive_paths if (opened.root / path).is_file()
            )
            paths = tuple(sorted(set(stored.candidate_files) | set(existing_archive_paths)))
            opened.version_store.require_clean_paths(paths)
            opened.require_current_source_versions(stored.changeset.source_versions)
            page_sources = candidate_page_sources(stored.candidate_files)
            validate_changeset_page_sources(page_sources, stored.changeset.source_ids)
            page_facts = {
                path: parse_page_facts(path, content)
                for path, content in stored.candidate_files.items()
                if path.startswith("wiki/pages/") and path.endswith(".md")
            }
            page_facts.update({path: () for path in archive_paths})
            previous_source_versions = opened.record_applied_source_versions(
                stored.changeset.source_versions
            )
            previous_page_sources: dict[str, tuple[str, ...]] = {}
            previous_page_facts: dict[str, tuple[IndexedWikiFact, ...]] = {}
            try:
                previous_page_sources = opened.replace_applied_page_sources(page_sources)
                previous_page_sources.update(opened.remove_applied_page_sources(archive_paths))
                previous_page_facts = opened.replace_applied_page_facts(page_facts)
            except Exception:
                opened.restore_applied_source_versions(previous_source_versions)
                opened.restore_applied_page_sources(previous_page_sources)
                opened.restore_applied_page_facts(previous_page_facts)
                raise
            previous_files: dict[Path, str | None] = {}
            try:
                for path in existing_archive_paths:
                    destination = opened.root / path
                    previous_files[destination] = destination.read_text(encoding="utf-8")
                    destination.unlink()
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
                opened.restore_applied_source_versions(previous_source_versions)
                opened.restore_applied_page_sources(previous_page_sources)
                opened.restore_applied_page_facts(previous_page_facts)
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
    try:
        opened = Workspace.open(workspace)
        with opened.exclusive_lock():
            stored = ChangeSetStore(opened).get(changeset_id)
            ChangeSetStore(opened).archive_rejected(stored)
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
    typer.echo(json.dumps({"changeset_id": changeset_id, "status": "REJECTED"}))


@app.command()
def ask(
    question: Annotated[str, typer.Argument()],
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    llm: Annotated[
        bool,
        typer.Option("--llm", help="Summarize matched public evidence with the configured model."),
    ] = False,
    allow_local_llm: Annotated[
        bool,
        typer.Option(
            "--allow-local-llm",
            help="Allow the configured model to receive local_only evidence for this question.",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Show the short Index, Wiki, and evidence read path."),
    ] = False,
    verify: Annotated[
        bool,
        typer.Option("--verify", help="Expand the selected cited source excerpt after answering."),
    ] = False,
    max_pages: Annotated[
        int,
        typer.Option("--max-pages", min=1, max=10, help="Maximum Wiki pages to expand."),
    ] = 3,
    repository_id: Annotated[
        str | None,
        typer.Option("--repository", help="Limit answers to one registered Git repository."),
    ] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        if as_of is not None:
            raise FeatureUnavailableError("--as-of is not supported yet")
        opened = Workspace.open_readonly(workspace)
        provider = OpenAICompatibleProvider(ProviderConfig.from_environment()) if llm else None
        if provider is not None:
            typer.echo("正在基于命中的公开 Wiki 证据生成回答…", err=True)
        result = answer_question(
            opened.root,
            question,
            debug=debug,
            verify=verify,
            max_pages=max_pages,
            provider=provider,
            allow_local=allow_local_llm,
            repository_id=repository_id,
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
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
def agent(
    question: Annotated[str, typer.Argument(help="Question for the Wiki-backed MiniClaude.")],
    max_steps: Annotated[
        int,
        typer.Option("--max-steps", min=1, max=8, help="Maximum model/tool turns."),
    ] = 4,
    max_pages: Annotated[
        int,
        typer.Option("--max-pages", min=1, max=3, help="Maximum Wiki pages for search."),
    ] = 3,
    allow_local_llm: Annotated[
        bool,
        typer.Option(
            "--allow-local-llm",
            help="Allow the configured model to read local_only evidence in this agent run.",
        ),
    ] = False,
    propose_update: Annotated[
        bool,
        typer.Option(
            "--propose-update",
            help="Create a reviewable Wiki ChangeSet from the answered evidence.",
        ),
    ] = False,
    repository_id: Annotated[
        str | None,
        typer.Option("--repository", help="Limit the Agent to one registered Git repository."),
    ] = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="Reuse this local conversation session."),
    ] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        opened = (
            Workspace.open(workspace) if session is not None else Workspace.open_readonly(workspace)
        )
        typer.echo("正在运行 Wiki-backed MiniClaude Agent…", err=True)
        result = run_agent(
            opened.root,
            question,
            provider=OpenAICompatibleProvider(ProviderConfig.from_environment()),
            max_steps=max_steps,
            max_pages=max_pages,
            allow_local=allow_local_llm,
            propose_update=propose_update,
            repository_id=repository_id,
            session_id=session,
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
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("agent-clear")
def agent_clear(
    session: Annotated[str, typer.Argument(help="Local Agent session to clear.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        opened = Workspace.open(workspace)
        SessionStore(opened.root, session).clear()
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
    typer.echo(json.dumps({"session_id": session, "status": "CLEARED"}, ensure_ascii=False))


@app.command()
def lint(
    fix_proposal: Annotated[bool, typer.Option("--fix-proposal")] = False,
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        if fix_proposal:
            raise FeatureUnavailableError("--fix-proposal is not supported; lint is read-only")
        result = lint_workspace(workspace)
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
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


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
    try:
        opened = Workspace.open_readonly(workspace)
        result = run_evaluation(opened.root, eval_config)
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
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


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
