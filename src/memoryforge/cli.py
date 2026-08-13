from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from memoryforge import __version__
from memoryforge.agent import run_agent
from memoryforge.agent_evaluation import run_agent_evaluation
from memoryforge.automation_policy import (
    BUILTIN_PROFILES,
    AutomationPolicy,
    decide,
    effective_profile,
    effective_trust,
    load_policy,
    policy_sha256,
    save_policy,
)
from memoryforge.automation_validation import (
    BLOCK_BASE_COMMIT_CHANGED,
    BLOCK_STALE_SOURCE_VERSION,
    assess_operation,
    change_set_risk,
)
from memoryforge.botmux_adapter import BotmuxHookError, handle_botmux_hook
from memoryforge.changesets import ChangeSetStore, StoredChangeSet
from memoryforge.code_index import build_code_index
from memoryforge.code_wiki_compiler import compile_code_wiki
from memoryforge.codex_adapter import CodexImportError, import_codex_rollout
from memoryforge.compiler import (
    Compilation,
    compilation_payload,
    compile_pending_sources,
    compile_repository_topics,
)
from memoryforge.errors import (
    ChangeSetStoreError,
    FeatureUnavailableError,
    MemoryForgeError,
    WorkspaceError,
)
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
from memoryforge.importer import MAX_SOURCE_BYTES, SourceValidationError, import_local_file
from memoryforge.lifecycle import (
    apply_changeset,
    approve_changeset,
    reject_changeset,
    review_changeset,
)
from memoryforge.linting import lint_workspace
from memoryforge.local_portal import serve_local_portal
from memoryforge.models import Sensitivity, SourceTrust
from memoryforge.module_planner import build_architecture_graph, build_module_plan
from memoryforge.obsidian import OUTPUT_RELATIVE, build_obsidian
from memoryforge.platform_lock import inspect_posix_namespace_lock_root
from memoryforge.portal_jobs import run_automation
from memoryforge.provider import OpenAICompatibleProvider, ProviderConfig
from memoryforge.query import answer_question
from memoryforge.refresh import refresh_workspace
from memoryforge.sessions import SessionStore
from memoryforge.showcase import build_showcase
from memoryforge.web_adapter import WebPageError, import_html_file, import_web_page
from memoryforge.wiki_facts import (
    is_conversation_process_note,
    parse_page_citations,
    parse_page_facts,
)
from memoryforge.workspace import (
    Workspace,
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    _connect_readonly,
    candidate_page_sources,
    list_git_checkouts,
    list_git_code_modules,
    rebuild_applied_projection,
    register_git_checkout,
    register_git_code_module,
    search_sources,
    sync_git_checkout,
)

app = typer.Typer(
    name="memoryforge",
    no_args_is_help=True,
    add_completion=False,
    help="Git-backed immutable local knowledge storage and search.",
)
showcase_app = typer.Typer(no_args_is_help=True, help="Build a read-only static Showcase.")
app.add_typer(showcase_app, name="showcase")

policy_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect and edit the automation policy (shadow mode).",
)
app.add_typer(policy_app, name="policy")

WorkspaceOption = Annotated[
    Path,
    typer.Option("--workspace", "-w", help="Initialized MemoryForge workspace."),
]

_RECALL_DECISION_MARKERS = (
    "决定",
    "采用",
    "选择",
    "已完成",
    "完成了",
    "优化完成",
    "decided",
    "implemented",
)
_RECALL_OPEN_ITEM_MARKERS = (
    "下一步",
    "待办",
    "未完成",
    "尚未",
    "未推送",
    "需要继续",
    "todo",
    "next step",
    "follow-up",
    "not pushed",
    "remaining",
)
_CODEX_RECALL_BEGIN = "<!-- BEGIN MEMORYFORGE RECALL -->"
_CODEX_RECALL_END = "<!-- END MEMORYFORGE RECALL -->"


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


@showcase_app.command("serve")
def showcase_serve(
    workspace: WorkspaceOption = Path("."),
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Loopback-only local port."),
    ] = 8765,
) -> None:
    try:
        serve_local_portal(workspace, port=port)
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


@app.command()
def start(
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Initialized MemoryForge workspace."),
    ],
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Loopback-only local port."),
    ] = 8765,
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Do not open the browser automatically."),
    ] = False,
    llm: Annotated[
        bool,
        typer.Option("--llm", help="Answer portal questions with the configured model."),
    ] = False,
    allow_local_llm: Annotated[
        bool,
        typer.Option(
            "--allow-local-llm",
            help="Allow the configured model to receive matched local-only evidence.",
        ),
    ] = False,
) -> None:
    """Start the local knowledge portal."""
    try:
        if allow_local_llm and not llm:
            raise ValueError("--allow-local-llm requires --llm")
        provider = OpenAICompatibleProvider(ProviderConfig.from_environment()) if llm else None
        serve_local_portal(
            workspace,
            port=port,
            open_browser=not no_open,
            provider=provider,
            allow_local_llm=allow_local_llm,
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


@app.command("obsidian-build")
def obsidian_build(workspace: WorkspaceOption = Path(".")) -> None:
    """Build local Obsidian-readable Markdown navigation views."""
    try:
        result = build_obsidian(workspace)
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


@app.command("status")
def status(workspace: WorkspaceOption = Path(".")) -> None:
    """Show one compact Workspace status JSON."""
    try:
        result = _workspace_status(workspace)
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


@app.command("source-list")
def source_list(workspace: WorkspaceOption = Path(".")) -> None:
    """List all stored SourceVersions with current and applied state."""
    try:
        opened = Workspace.open_readonly(workspace)
        with _connect_readonly(opened.index_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    sources.source_id,
                    sources.source_uri,
                    sources.source_path,
                    versions.id AS version_id,
                    versions.title,
                    versions.category,
                    versions.sensitivity,
                    versions.observed_at,
                    versions.is_current,
                    CASE WHEN applied.source_version_id IS NULL THEN 0 ELSE 1 END AS is_applied,
                    repositories.repository_id,
                    repositories.name AS repository_name
                FROM source_versions AS versions
                JOIN sources ON sources.id = versions.source_id
                LEFT JOIN applied_source_versions AS applied
                  ON applied.source_id = sources.source_id
                 AND applied.source_version_id = versions.id
                LEFT JOIN git_source_revisions AS revisions
                  ON revisions.source_version_id = versions.id
                LEFT JOIN git_repositories AS repositories
                  ON repositories.repository_id = revisions.repository_id
                ORDER BY sources.source_uri, versions.observed_at DESC
                """
            ).fetchall()
        result = [_source_summary(row) for row in rows]
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
    public: Annotated[
        bool,
        typer.Option("--public", help="Allow this source in future remote model requests."),
    ] = False,
) -> None:
    try:
        result = import_local_file(
            workspace,
            path,
            category=category,
            source_root=Path.cwd(),
            tags=tuple(tag or ()),
            sensitivity=Sensitivity.PUBLIC if public else Sensitivity.LOCAL_ONLY,
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


@app.command("botmux-hook")
def botmux_hook(
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Read one Botmux lifecycle hook JSON from stdin and update local memory."""
    try:
        raw = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise BotmuxHookError("Botmux hook exceeds the 5 MiB size limit")
        result = handle_botmux_hook(workspace, json.loads(raw))
    except (
        BotmuxHookError,
        MemoryForgeError,
        SourceValidationError,
        json.JSONDecodeError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
    ) as exc:
        _exit_with_safe_error(exc)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("codex-import")
def codex_import(
    path: Annotated[Path, typer.Argument(help="Local Codex rollout JSONL file.")],
    workspace: WorkspaceOption = Path("."),
    title: Annotated[
        str | None,
        typer.Option("--title", help="Stable Wiki title for this conversation."),
    ] = None,
) -> None:
    """Import one Codex conversation as a local-only memory draft."""
    try:
        result = import_codex_rollout(workspace, path, title=title)
    except (
        CodexImportError,
        MemoryForgeError,
        WorkspaceIntegrityError,
        WorkspaceSecurityError,
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
                        "deleted": item.deleted,
                    }
                    for item in result.feishu
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("automation-run", hidden=True)
def automation_run(workspace: WorkspaceOption = Path(".")) -> None:
    """Run one configured automatic update pass."""
    try:
        result = run_automation(workspace)
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
            changesets: list[dict[str, object]] = []

            def append_compilation(
                compilation: Compilation,
                changesets: list[dict[str, object]],
            ) -> None:
                store = ChangeSetStore(opened)
                try:
                    store.get(compilation.changeset.changeset_id)
                except ChangeSetStoreError:
                    changesets.append(
                        compilation_payload(
                            store.create(
                                compilation.changeset,
                                compilation.candidate_files,
                            )
                        )
                    )

            if first_pass or refreshed.changed:
                compilation = compile_pending_sources(
                    opened,
                    provider=provider,
                    allow_local=allow_local_llm,
                )
                if compilation is not None:
                    append_compilation(compilation, changesets)
                code_repository_ids = (
                    {
                        result.repository_id
                        for result in refreshed.git
                        if any(
                            document.status in {"created", "updated"}
                            and document.relative_path.endswith((".go", ".py", ".ts", ".tsx"))
                            for document in result.documents
                        )
                    }
                    if not first_pass
                    else {
                        repository.repository_id
                        for repository in list_git_checkouts(opened.root)
                        if list_git_code_modules(opened, repository.repository_id)
                    }
                )
                for repository in list_git_checkouts(opened.root):
                    if repository.repository_id not in code_repository_ids:
                        continue
                    snapshot = build_code_index(opened, repository.repository_id)
                    plan = build_module_plan(snapshot)
                    graph = build_architecture_graph(snapshot, plan)
                    code_compilation = compile_code_wiki(
                        opened,
                        snapshot,
                        plan,
                        graph,
                        provider=provider,
                        allow_local=allow_local_llm,
                    )
                    if code_compilation is None:
                        continue
                    append_compilation(code_compilation, changesets)

            if first_pass or refreshed.changed or changesets:
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
                            "deleted": item.deleted,
                        }
                        for item in refreshed.feishu
                    ],
                }
                if changesets:
                    payload["status"] = "proposed"
                    payload["changeset"] = changesets[0]
                    if len(changesets) > 1:
                        payload["changesets"] = changesets
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
    if code_wiki is not None and source:
        _exit_with_safe_error(ValueError("--code-wiki cannot be combined with --source"))
    if code_wiki is not None and llm and not allow_local_llm:
        _exit_with_safe_error(ValueError("--code-wiki --llm requires --allow-local-llm"))
    try:
        opened = Workspace.open(workspace)
        if code_wiki is not None:
            snapshot = build_code_index(opened, code_wiki)
            plan = build_module_plan(snapshot)
            provider = OpenAICompatibleProvider(ProviderConfig.from_environment()) if llm else None
            compilation = compile_code_wiki(
                opened,
                snapshot,
                plan,
                provider=provider,
                allow_local=allow_local_llm,
            )
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


@app.command("changeset-list")
def changeset_list(
    workspace: WorkspaceOption = Path("."),
    decision: Annotated[
        str | None,
        typer.Option(
            "--decision",
            help="Filter by simulated decision: auto_apply, review_required, blocked.",
        ),
    ] = None,
) -> None:
    """List pending ChangeSets that can be reviewed and applied."""
    if decision is not None and decision not in {"auto_apply", "review_required", "blocked"}:
        _exit_with_safe_error(ValueError(f"unknown decision: {decision}"))
    try:
        opened = Workspace.open_readonly(workspace)
        policy = load_policy(opened.root)
        result = []
        for stored in ChangeSetStore(opened).list_all():
            summary = _changeset_summary(stored)
            if decision is not None:
                simulated = _simulate_stored(opened, stored, policy)
                summary["decision"] = simulated
                if str(simulated.get("decision")) != decision:
                    continue
            result.append(summary)
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


@policy_app.command("show")
def policy_show(workspace: WorkspaceOption = Path(".")) -> None:
    """Print the effective automation policy and its canonical SHA256."""
    try:
        opened = Workspace.open_readonly(workspace)
        policy = load_policy(opened.root)
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
                "policy": json.loads(policy.model_dump_json()),
                "policy_sha256": policy_sha256(policy),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@policy_app.command("set")
def policy_set(
    profile: Annotated[str, typer.Argument(help="Built-in profile to switch to.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Switch the workspace to a built-in profile (manual/balanced/autonomous/custom)."""
    if profile not in BUILTIN_PROFILES:
        _exit_with_safe_error(
            ValueError(
                f"unknown profile: {profile} (choose from {', '.join(sorted(BUILTIN_PROFILES))})"
            )
        )
    try:
        opened = Workspace.open(workspace)
        with opened.exclusive_lock():
            updated = load_policy(opened.root).model_copy(update={"profile": profile})
            save_policy(opened.root, updated)
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
            {"profile": profile, "policy_sha256": policy_sha256(updated)},
            ensure_ascii=False,
            indent=2,
        )
    )


@policy_app.command("simulate")
def policy_simulate(
    changeset_id: Annotated[str | None, typer.Argument()] = None,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Simulate the current policy against staged proposals without writing."""
    try:
        opened = Workspace.open_readonly(workspace)
        policy = load_policy(opened.root)
        store = ChangeSetStore(opened)
        stored_list = [store.get(changeset_id)] if changeset_id else store.list_all()
        result = [_simulate_stored(opened, stored, policy) for stored in stored_list]
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
def review(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        result = review_changeset(workspace, changeset_id)
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
def approve(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        result = approve_changeset(workspace, changeset_id)
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
def apply(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        result = apply_changeset(
            workspace,
            changeset_id,
            obsidian_builder=build_obsidian,
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
def reject(
    changeset_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        result = reject_changeset(workspace, changeset_id)
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
    typer.echo(json.dumps(result))


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
def recall(
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=20, help="Maximum recent conversation notes."),
    ] = 3,
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Return compact, cited startup context from applied conversation memories."""
    try:
        opened = Workspace.open_readonly(workspace)
        with sqlite3.connect(
            opened.index_path.as_uri() + "?mode=ro",
            uri=True,
            timeout=30,
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                """
                SELECT
                    facts.page_path,
                    facts.source_id,
                    facts.source_version,
                    facts.locator,
                    facts.section_path,
                    facts.quote,
                    versions.title,
                    versions.observed_at
                FROM wiki_facts AS facts
                JOIN applied_source_versions AS applied
                  ON applied.source_id = facts.source_id
                 AND applied.source_version_id = facts.source_version
                JOIN sources ON sources.source_id = facts.source_id
                JOIN source_versions AS versions
                  ON versions.id = facts.source_version
                 AND versions.source_id = sources.id
                WHERE versions.tags_json LIKE '%"conversation"%'
                ORDER BY
                    versions.observed_at DESC,
                    facts.page_path,
                    facts.rowid
                """
            ).fetchall()
        summary_locators: dict[str, str] = {}
        for row in rows:
            page_path = str(row["page_path"])
            if page_path in summary_locators:
                continue
            page = opened.root / page_path
            if page.is_file() and not page.is_symlink():
                citations = parse_page_citations(page.read_text(encoding="utf-8"))
                if citations:
                    summary_locators[page_path] = citations[0]["locator"]
        rows = sorted(
            rows,
            key=lambda row: (
                str(row["observed_at"]),
                str(row["locator"]) == summary_locators.get(str(row["page_path"])),
            ),
            reverse=True,
        )
        summary = next(
            (str(row["quote"]).strip() for row in rows if not str(row["section_path"])),
            None,
        )
        notes: list[dict[str, object]] = []
        seen: set[str] = set()
        seen_pages: set[str] = set()
        seen_topics: set[str] = set()
        for row in rows:
            quote = str(row["quote"]).strip()
            section = str(row["section_path"])
            page_path = str(row["page_path"])
            topic = _recall_topic_key(str(row["title"]))
            if (
                not row["section_path"]
                or not quote
                or quote in seen
                or page_path in seen_pages
                or topic in seen_topics
                or "user message" in section.lower()
                or "user prompts (search only)" in section.lower()
                or quote.lstrip().startswith(("```", "func ", "def "))
                or any(marker in quote for marker in ("你偏好", "值得“记忆”", "你常做的是"))
                or is_conversation_process_note(quote)
                or len(quote) < 24
                or (len(quote) < 40 and quote.endswith((":", "：")))
            ):
                continue
            seen.add(quote)
            seen_pages.add(page_path)
            seen_topics.add(topic)
            notes.append(
                {
                    "title": str(row["title"]),
                    "role": section,
                    "text": quote,
                    "observed_at": str(row["observed_at"]),
                    "citation": {
                        "source_id": str(row["source_id"]),
                        "source_version": int(row["source_version"]),
                        "locator": str(row["locator"]),
                        "wiki_page": page_path,
                    },
                }
            )
            if len(notes) == limit:
                break
        if summary is None and notes:
            summary = str(notes[0]["text"])
        decisions = _recall_matching_notes(notes, _RECALL_DECISION_MARKERS)
        open_items = _recall_matching_notes(notes, _RECALL_OPEN_ITEM_MARKERS)
        result = {
            "status": "recalled" if notes else "empty",
            "warning": (
                "Conversation memories are unverified; check citations before relying on them."
            ),
            "summary": summary,
            "recent_memories": notes,
            "decisions": decisions,
            "open_items": open_items,
            "startup_context": _render_recall_context(summary, decisions, open_items, notes),
        }
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


@app.command("codex-setup")
def codex_setup(
    project: Annotated[Path, typer.Argument(help="Project whose AGENTS.md Codex reads.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    """Install one bounded new-task recall instruction in a project's AGENTS.md."""
    try:
        opened = Workspace.open_readonly(workspace)
        project_root = project.resolve(strict=True)
        if project_root.is_symlink() or not project_root.is_dir():
            raise ValueError("Codex project must be a real directory")
        agents_path = project_root / "AGENTS.md"
        if agents_path.is_symlink() or (agents_path.exists() and not agents_path.is_file()):
            raise ValueError("Codex project AGENTS.md must be a regular file")
        command = shlex.join(
            (
                sys.executable,
                "-m",
                "memoryforge",
                "recall",
                "--workspace",
                str(opened.root),
            )
        )
        block = (
            f"{_CODEX_RECALL_BEGIN}\n"
            "## MemoryForge recall\n\n"
            "At the start of every new task, run this read-only command before answering:\n\n"
            f"```bash\n{command}\n```\n\n"
            "Use only its `startup_context` as unverified history. Verify important claims "
            "from cited Wiki pages. If status is `empty`, continue without recalled context.\n"
            f"{_CODEX_RECALL_END}"
        )
        existing = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
        start = existing.find(_CODEX_RECALL_BEGIN)
        end = existing.find(_CODEX_RECALL_END)
        if (start < 0) != (end < 0) or (start >= 0 and end < start):
            raise ValueError("Codex project AGENTS.md contains an incomplete MemoryForge block")
        if start >= 0:
            updated = existing[:start] + block + existing[end + len(_CODEX_RECALL_END) :]
        else:
            updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block + "\n"
        agents_path.write_text(updated, encoding="utf-8")
        result = {
            "status": "configured",
            "project": str(project_root),
            "agents_file": str(agents_path),
            "workspace": str(opened.root),
        }
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


def _recall_matching_notes(
    notes: list[dict[str, object]], markers: tuple[str, ...]
) -> list[dict[str, object]]:
    matches = []
    for note in notes:
        text = str(note["text"]).lower()
        if any(marker in text for marker in markers):
            matches.append(note)
        if len(matches) == 3:
            break
    return matches


def _recall_topic_key(title: str) -> str:
    for identifier in re.findall(r"[A-Z][A-Za-z0-9]+", title):
        if identifier.casefold() == "codex":
            continue
        topic = re.sub(r"^(?:Create|Update|Delete|Get|Check|Build|Find)", "", identifier)
        if len(topic) >= 5:
            return topic.casefold()
    return title.casefold()


def _render_recall_context(
    summary: str | None,
    decisions: list[dict[str, object]],
    open_items: list[dict[str, object]],
    notes: list[dict[str, object]],
) -> str:
    if not notes:
        return "No applied conversation memory found."
    lines = [
        "Unverified recalled conversation memory. Verify important claims against citations.",
        f"Summary: {summary}",
    ]
    lines.append(
        "Recent sessions: " + " | ".join(f"{item['title']}: {item['text']}" for item in notes)
    )
    if decisions:
        lines.append("Recent decisions: " + " | ".join(str(item["text"]) for item in decisions))
    if open_items:
        lines.append("Open items: " + " | ".join(str(item["text"]) for item in open_items))
    pages = []
    for item in notes:
        citation = item["citation"]
        if isinstance(citation, dict) and citation["wiki_page"] not in pages:
            pages.append(citation["wiki_page"])
    lines.append("Evidence pages: " + ", ".join(str(page) for page in pages))
    return "\n".join(lines)


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


@app.command("agent-eval")
def agent_eval(
    eval_config: Annotated[Path, typer.Argument(help="Agent EvaluationSuite JSON.")],
    workspace: WorkspaceOption = Path("."),
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
            help="Allow the configured model to read local_only evidence in this evaluation.",
        ),
    ] = False,
) -> None:
    """Run one frozen suite through the real Wiki-backed Agent."""
    try:
        opened = Workspace.open_readonly(workspace)
        result = run_agent_evaluation(
            opened.root,
            eval_config,
            OpenAICompatibleProvider(ProviderConfig.from_environment()),
            max_steps=max_steps,
            max_pages=max_pages,
            allow_local=allow_local_llm,
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
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        opened = Workspace.open_readonly(workspace)
        records = opened.version_store.history(page=page, limit=limit)
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
    typer.echo(json.dumps(list(records), ensure_ascii=False, indent=2))


@app.command()
def rollback(
    commit_id: Annotated[str, typer.Argument()],
    workspace: WorkspaceOption = Path("."),
) -> None:
    try:
        opened = Workspace.open(workspace)
        with opened.exclusive_lock():
            previous_commit = opened.current_commit()
            if commit_id == previous_commit:
                raise ValueError("rollback target is already the current Commit")
            if not opened.version_store.is_ancestor(commit_id, previous_commit):
                raise ValueError("rollback target must be an ancestor of the current Commit")
            changed_paths = opened.version_store.changed_wiki_paths(
                commit_id,
                previous_commit,
            )
            if not changed_paths:
                raise ValueError("rollback target has the same Wiki tree")
            opened.version_store.require_clean_paths(("wiki",))
            try:
                opened.version_store.restore_wiki(commit_id)
                rebuild_applied_projection(opened)
                commit = opened.version_store.commit_paths(
                    ("wiki",),
                    f"knowledge: rollback to {commit_id[:12]}",
                )
            except Exception:
                opened.version_store.restore_wiki(previous_commit)
                rebuild_applied_projection(opened)
                raise
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
                "status": "ROLLED_BACK",
                "target_commit": commit_id,
                "previous_commit": previous_commit,
                "commit": commit,
                "files": list(changed_paths),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


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


@app.command()
def doctor(workspace: WorkspaceOption = Path(".")) -> None:
    """Run a read-only local environment and Workspace diagnostic."""
    result = _doctor_report(workspace)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


def _workspace_status(workspace: Path) -> dict[str, object]:
    opened = Workspace.open_readonly(workspace)
    commit = opened.current_commit()
    with _connect_readonly(opened.index_path) as connection:
        current_sources = int(
            connection.execute(
                "SELECT COUNT(DISTINCT source_id) FROM source_versions WHERE is_current = 1"
            ).fetchone()[0]
        )
        current_versions = int(
            connection.execute(
                "SELECT COUNT(*) FROM source_versions WHERE is_current = 1"
            ).fetchone()[0]
        )
        applied_sources = int(
            connection.execute("SELECT COUNT(*) FROM applied_source_versions").fetchone()[0]
        )
        applied_pages = int(
            connection.execute("SELECT COUNT(DISTINCT page_path) FROM page_sources").fetchone()[0]
        )
        repositories = [
            {
                "repository_id": str(row["repository_id"]),
                "name": str(row["name"]),
                "checkout_path": str(row["checkout_path"]),
                "remote_url": (str(row["remote_url"]) if row["remote_url"] is not None else None),
                "last_synced_commit": (
                    str(row["last_synced_commit"])
                    if row["last_synced_commit"] is not None
                    else None
                ),
            }
            for row in connection.execute(
                """
                SELECT repository_id, name, checkout_path, remote_url, last_synced_commit
                FROM git_repositories
                ORDER BY registered_at, repository_id
                """
            ).fetchall()
        ]
    pending = ChangeSetStore(opened).list_all()
    home = opened.root / OUTPUT_RELATIVE / "Home.md"
    generated = home.is_file()
    history = opened.version_store.history(limit=1)
    commit_time = datetime.fromisoformat(str(history[0]["committed_at"])) if history else None
    lagging = not generated or (
        commit_time is not None
        and datetime.fromtimestamp(home.stat().st_mtime, tz=UTC) < commit_time
    )
    return {
        "current_commit": commit,
        "sources": {
            "current": current_sources,
            "applied": applied_sources,
        },
        "versions": {"current": current_versions},
        "applied_pages": applied_pages,
        "git_repositories": repositories,
        "changesets": {
            "pending": len(pending),
            "items": [
                {
                    "changeset_id": stored.changeset.changeset_id,
                    "status": stored.changeset.status.value,
                }
                for stored in pending
            ],
        },
        "obsidian": {"generated": generated, "lagging": lagging},
    }


def _source_summary(row: sqlite3.Row) -> dict[str, object]:
    repository = None
    if row["repository_id"] is not None:
        repository = {
            "repository_id": str(row["repository_id"]),
            "name": str(row["repository_name"]),
        }
    return {
        "source_id": str(row["source_id"]),
        "source_uri": str(row["source_uri"]),
        "source_path": str(row["source_path"]),
        "version_id": int(row["version_id"]),
        "title": str(row["title"]),
        "category": str(row["category"]),
        "sensitivity": str(row["sensitivity"]),
        "is_current": bool(row["is_current"]),
        "is_applied": bool(row["is_applied"]),
        "observed_at": str(row["observed_at"]),
        "git_repository": repository,
    }


def _changeset_summary(stored: StoredChangeSet) -> dict[str, object]:
    payload = compilation_payload(stored)
    payload["base_commit"] = stored.changeset.base_commit
    payload["source_ids"] = list(stored.changeset.source_ids)
    return payload


def _simulate_stored(
    opened: Workspace,
    stored: StoredChangeSet,
    policy: AutomationPolicy,
) -> dict[str, object]:
    """Run the shadow decision engine over one staged ChangeSet (read-only)."""
    changeset = stored.changeset
    block_reasons: list[str] = []
    if changeset.base_commit != opened.current_commit():
        block_reasons.append(BLOCK_BASE_COMMIT_CHANGED)
    current_versions = _current_source_versions(opened, changeset.source_ids)
    if any(
        changeset.source_versions.get(source_id) != row["version_id"]
        for source_id, row in current_versions.items()
    ):
        block_reasons.append(BLOCK_STALE_SOURCE_VERSION)
    trust_defaults = {
        source_id: SourceTrust.UNTRUSTED if row["untrusted"] else SourceTrust.STANDARD
        for source_id, row in current_versions.items()
    }
    trust = effective_trust(policy, changeset.source_ids, trust_defaults)
    profile = effective_profile(policy, changeset.source_ids)
    page_sources = candidate_page_sources(stored.candidate_files)
    assessments = []
    for operation in changeset.operations:
        before = opened.version_store.read_text_at(changeset.base_commit, operation.path) or ""
        after = stored.candidate_files.get(operation.path, "")
        source_count = len(page_sources.get(operation.path, ())) or len(changeset.source_ids)
        assessments.append(
            assess_operation(
                operation,
                before=before,
                after=after,
                source_count=source_count,
                source_trust=trust,
            )
        )
    risk, reason_codes = change_set_risk(
        tuple(assessments),
        low_max_changed_pages=policy.limits.low_max_changed_pages,
        low_max_changed_lines=policy.limits.low_max_changed_lines,
    )
    evaluation = decide(
        policy,
        profile=profile,
        risk=risk,
        reason_codes=reason_codes,
        source_trust=trust,
        block_reasons=tuple(block_reasons),
    )
    return {
        "changeset_id": changeset.changeset_id,
        "decision": evaluation.decision.value,
        "risk": evaluation.risk.value,
        "reason_codes": list(evaluation.reason_codes),
        "profile": evaluation.policy_id,
        "policy_sha256": evaluation.policy_sha256,
        "source_trust": trust.value,
        "operations": [
            {
                "path": item.path,
                "origin": item.origin.value if item.origin is not None else None,
                "risk": item.risk.value,
                "reason_codes": list(item.reason_codes),
                "changed_lines": item.changed_lines,
            }
            for item in assessments
        ],
    }


def _current_source_versions(
    opened: Workspace,
    source_ids: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    """Map source_id to its current version id and untrusted-tag flag."""
    if not source_ids:
        return {}
    placeholders = ", ".join("?" for _ in source_ids)
    with _connect_readonly(opened.index_path) as connection:
        rows = connection.execute(
            f"""
            SELECT sources.source_id, versions.id, versions.tags_json
            FROM sources
            JOIN source_versions AS versions ON versions.source_id = sources.id
            WHERE sources.source_id IN ({placeholders}) AND versions.is_current = 1
            """,
            tuple(source_ids),
        ).fetchall()
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        tags = json.loads(str(row["tags_json"]))
        result[str(row["source_id"])] = {
            "version_id": int(row["id"]),
            "untrusted": "conversation" in tags or "platform:codex" in tags,
        }
    return result


def _doctor_report(workspace: Path) -> dict[str, object]:
    checks = [
        _doctor_item(
            "python",
            status="ok",
            message=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ),
        _doctor_item("platform", status="ok", message=platform.system()),
        _doctor_git(),
    ]
    workspace_item, opened = _doctor_workspace(workspace)
    checks.append(workspace_item)
    root = Path(workspace).expanduser()
    index_path = opened.index_path if opened is not None else root / ".memoryforge/index.sqlite"
    checks.append(_doctor_index(index_path))
    checks.append(_doctor_projection(opened))
    checks.append(_doctor_lock_directory(opened))
    checks.append(_doctor_model())
    checks.append(_doctor_feishu())
    remediation = [
        str(check["remediation"]) for check in checks if check.get("remediation") is not None
    ]
    return {
        "status": "error" if any(check["status"] == "error" for check in checks) else "ok",
        "checks": checks,
        "remediation": remediation,
    }


def _doctor_item(
    name: str,
    *,
    status: str,
    message: str | None = None,
    remediation: str | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {"name": name, "status": status}
    if message is not None:
        item["message"] = message
    if remediation is not None:
        item["remediation"] = remediation
    return item


def _doctor_git() -> dict[str, object]:
    executable = shutil.which("git")
    if executable is None:
        return _doctor_item(
            "git",
            status="error",
            remediation="Install Git and add it to PATH.",
        )
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _doctor_item(
            "git",
            status="error",
            message=str(exc),
            remediation="Make the installed Git executable runnable.",
        )
    if completed.returncode != 0:
        return _doctor_item(
            "git",
            status="error",
            message=completed.stderr.strip(),
            remediation="Reinstall Git or fix its executable permissions.",
        )
    return _doctor_item("git", status="ok", message=completed.stdout.strip())


def _doctor_workspace(workspace: Path) -> tuple[dict[str, object], Workspace | None]:
    try:
        opened = Workspace.open_readonly(workspace)
    except Exception as exc:
        return (
            _doctor_item(
                "workspace",
                status="error",
                message=str(exc),
                remediation="Run 'memoryforge init <workspace>' or fix the workspace path.",
            ),
            None,
        )
    return _doctor_item("workspace", status="ok", message=str(opened.root)), opened


def _doctor_index(index_path: Path) -> dict[str, object]:
    try:
        with _connect_readonly(index_path) as connection:
            quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
            if quick_check != ["ok"]:
                raise sqlite3.DatabaseError("SQLite quick_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise sqlite3.IntegrityError("SQLite foreign_key_check failed")
            copied = sqlite3.connect(":memory:")
            try:
                connection.backup(copied)
                copied.execute("INSERT INTO source_fts(source_fts) VALUES ('integrity-check')")
                copied.execute(
                    """
                    INSERT INTO wiki_fact_fts(wiki_fact_fts, rank)
                    VALUES ('integrity-check', 1)
                    """
                )
            finally:
                copied.close()
    except Exception as exc:
        return _doctor_item(
            "index",
            status="error",
            message=str(exc),
            remediation="Run 'memoryforge init <workspace>' or restore .memoryforge/index.sqlite.",
        )
    return _doctor_item("index", status="ok")


def _doctor_projection(workspace: Workspace | None) -> dict[str, object]:
    if workspace is None:
        return _doctor_item(
            "projection",
            status="error",
            remediation="Restore a valid Workspace before checking its Wiki projection.",
        )
    try:
        commit = workspace.current_commit()
        paths = workspace.version_store.list_wiki_paths_at(commit)
        contents = workspace.version_store.read_wiki_texts_at(commit, paths=paths) if paths else {}
        expected_sources = {
            (page_path, source_id)
            for page_path, source_ids in candidate_page_sources(contents).items()
            for source_id in source_ids
        }
        expected_facts = {
            (page_path, fact.fact_id)
            for page_path, content in contents.items()
            for fact in parse_page_facts(page_path, content)
        }
        with _connect_readonly(workspace.index_path) as connection:
            actual_sources = {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT page_path, source_id FROM page_sources"
                ).fetchall()
            }
            actual_facts = {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT page_path, fact_id FROM wiki_facts"
                ).fetchall()
            }
        workspace.version_store.require_clean_paths(("wiki",))
        if expected_sources != actual_sources or expected_facts != actual_facts:
            raise WorkspaceIntegrityError("Git Wiki and SQLite projection differ")
    except Exception as exc:
        return _doctor_item(
            "projection",
            status="error",
            message=str(exc),
            remediation=(
                "Recover any interrupted apply, then run rollback or restore the Workspace "
                "from a verified backup."
            ),
        )
    return _doctor_item("projection", status="ok", message=commit)


def _doctor_lock_directory(workspace: Workspace | None) -> dict[str, object]:
    try:
        if sys.platform == "win32":
            if workspace is None:
                raise FileNotFoundError("Workspace lock directory is unavailable")
            path = workspace.internal_dir
        else:
            path = inspect_posix_namespace_lock_root()
        if not path.exists():
            return _doctor_item(
                "lock_directory",
                status="ok",
                message="The private lock directory will be created on first write.",
            )
        if not path.is_dir():
            raise FileNotFoundError("lock directory is missing")
        if not os.access(path, os.W_OK | os.X_OK):
            raise PermissionError("lock directory is not writable")
    except Exception as exc:
        return _doctor_item(
            "lock_directory",
            status="error",
            message=str(exc),
            remediation=(
                "Create a private ~/.memoryforge-locks directory owned by the current user "
                "with mode 0700."
                if sys.platform != "win32"
                else "Make the Workspace .memoryforge directory writable by the current user."
            ),
        )
    return _doctor_item("lock_directory", status="ok")


def _doctor_model() -> dict[str, object]:
    try:
        ProviderConfig.from_environment()
    except Exception:
        status = "not_configured"
    else:
        status = "configured"
    return _doctor_item("model", status=status)


def _doctor_feishu() -> dict[str, object]:
    return _doctor_item(
        "feishu",
        status="configured" if shutil.which("lark-cli") is not None else "not_configured",
    )


def _exit_with_safe_error(exc: Exception) -> None:
    if isinstance(exc, FeatureUnavailableError):
        message = str(exc)
        code = 2
    elif isinstance(exc, WorkspaceIntegrityError):
        message = (
            "workspace integrity check failed. "
            "Run 'memoryforge doctor --workspace <workspace>' to diagnose."
        )
        code = 1
    elif isinstance(exc, (WorkspaceSecurityError, WorkspaceError)):
        message = (
            "workspace security or configuration check failed. "
            "Run 'memoryforge doctor --workspace <workspace>' to diagnose."
        )
        code = 1
    elif isinstance(exc, FileNotFoundError):
        message = (
            "workspace is not initialized. "
            "Run 'memoryforge doctor --workspace <workspace>' to diagnose."
        )
        code = 1
    elif isinstance(exc, (OSError, sqlite3.Error)):
        message = (
            "workspace operation failed safely. "
            "Run 'memoryforge doctor --workspace <workspace>' to diagnose."
        )
        code = 1
    else:
        message = str(exc)
        code = 1
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code) from None


def main() -> None:
    app()
