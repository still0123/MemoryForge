"""Deterministic local compilation from current sources to readable Wiki pages."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from memoryforge.compiler.index_rendering import (
    PageSummary,
    _escape_link_text,
    _frontmatter_fields,
    _parse_page_summary,
    render_index,
)
from memoryforge.compiler.source_rendering import (
    CurrentSource,
    _canonical_page_path,
    _feishu_facts,
    _meaningful_paragraphs,
    _read_source_text,
    _render_code_page,
    _render_conversation_page,
    _render_page,
    _wiki_path,
)
from memoryforge.compiler.source_rendering import (
    SourceFact as SourceFact,
)
from memoryforge.compiler.source_rendering import (
    _conversation_facts as _conversation_facts,
)
from memoryforge.core.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeOrigin,
    ChangeSet,
    ChangeSetStatus,
    CompilationPlan,
    PageChange,
    PageCitation,
    PlannedPage,
    Sensitivity,
    TopicGroup,
    validate_llm_body,
    validate_llm_summary,
    validate_llm_title,
)
from memoryforge.query.contracts import EvidencePayload
from memoryforge.query.provider import OpenAICompatibleProvider
from memoryforge.storage.changesets import StoredChangeSet
from memoryforge.storage.projection import candidate_page_sources
from memoryforge.storage.workspace import Workspace, list_git_checkouts

_WORDS = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_CJK = re.compile(r"^[\u4e00-\u9fff]+$")
_RELATED_PAGE_LINK = re.compile(r"^- \[[^\]]+\]\((?P<path>[^)]+\.md)\)$", re.MULTILINE)


@dataclass(frozen=True)
class Compilation:
    changeset: ChangeSet
    candidate_files: dict[str, str]


@dataclass(frozen=True)
class RoutedPage:
    """An existing page whose complete source group must stay together."""

    path: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class StalePage:
    path: str
    stale_source_ids: tuple[str, ...]
    current_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class PageCard:
    """A small existing-Wiki card that a compiler can extend, not rewrite blindly."""

    path: str
    title: str
    summary: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class AgentUpdateCandidate:
    path: str
    summary: PageSummary
    source_ids: tuple[str, ...]
    content: str
    sources: tuple[CurrentSource, ...]


class PageCompilerProvider(Protocol):
    def compile_pages(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> tuple[PageChange, ...]: ...


def current_conversation_source_ids(workspace: Workspace) -> tuple[str, ...]:
    """Return every current conversation source without loading source content."""
    return tuple(
        source.source_id
        for source in _load_current_sources(workspace, set())
        if "conversation" in source.tags
    )


def compile_pending_sources(
    workspace: Workspace,
    *,
    source_ids: tuple[str, ...] = (),
    provider: PageCompilerProvider | None = None,
    allow_local: bool = False,
    reorganize_existing: bool = False,
) -> Compilation | None:
    """Compile pending local sources into a reviewable Wiki ChangeSet."""
    if reorganize_existing and provider is None:
        raise ValueError("reorganizing existing sources requires a compiler provider")
    selected = set(source_ids)
    _require_known_sources(workspace, selected)
    pending = _load_pending_sources(workspace, selected)
    stale_pages = _stale_pages(workspace, selected)
    if stale_pages:
        return _compile_stale_pages(workspace, stale_pages)
    if selected:
        selected_sources = _load_current_sources(workspace, selected)
        if selected_sources and all("conversation" in source.tags for source in selected_sources):
            pending = list(selected_sources)
    if not pending:
        return _compile_missing_repository_overviews(workspace) if not selected else None

    replaced_page_paths: tuple[str, ...] = ()
    routed_pages: tuple[RoutedPage, ...]
    if reorganize_existing:
        replaced_page_paths = _replaceable_page_paths(workspace, pending)
        routed_pages = ()
    else:
        routed_pages = _routed_pages_for_pending_sources(workspace, pending)
    routed_source_ids = {
        source_id for routed_page in routed_pages for source_id in routed_page.source_ids
    }
    compilation_source_ids = {source.source_id for source in pending} | routed_source_ids
    candidate_pages: tuple[PageCard, ...] = ()
    if provider is not None and not reorganize_existing:
        candidate_pages = _candidate_pages_for_pending_sources(
            workspace,
            pending,
            routed_pages,
            allow_local=allow_local,
        )
        compilation_source_ids.update(
            source_id for page in candidate_pages for source_id in page.source_ids
        )
    compilation_sources = _load_current_sources(workspace, compilation_source_ids)
    loaded_source_ids = {source.source_id for source in compilation_sources}
    expected_source_ids = compilation_source_ids
    if loaded_source_ids != expected_source_ids:
        raise ValueError("recorded Wiki page ownership has no current source version")

    if provider is not None:
        local_only = [
            source.source_id
            for source in compilation_sources
            if source.sensitivity is Sensitivity.LOCAL_ONLY
        ]
        if local_only and not allow_local:
            raise ValueError(
                "LLM compilation cannot include local_only sources: " + ", ".join(local_only)
            )
        return _compile_with_provider(
            workspace,
            pending,
            compilation_sources,
            provider,
            routed_pages=routed_pages,
            candidate_pages=candidate_pages,
            replaced_page_paths=replaced_page_paths,
        )

    return _compile_deterministically(workspace, compilation_sources, routed_pages)


def compile_repository_topics(
    workspace: Workspace,
    repository_id: str,
    provider: OpenAICompatibleProvider,
) -> Compilation:
    """Use a model to organize one public repository's existing Wiki pages."""
    repository = next(
        (
            item
            for item in list_git_checkouts(workspace.root)
            if item.repository_id == repository_id
        ),
        None,
    )
    if repository is None:
        raise ValueError(f"unknown Git repository: {repository_id}")
    if repository.sensitivity is Sensitivity.LOCAL_ONLY:
        raise ValueError("topic organization requires a Git checkout registered with --public")

    sources = [
        source
        for source in _load_current_sources(workspace, set())
        if source.repository_id == repository_id
    ]
    if not sources:
        raise ValueError("repository has no current documentation")
    if any(source.sensitivity is Sensitivity.LOCAL_ONLY for source in sources):
        raise ValueError("topic organization cannot include local_only sources")

    links = _repository_links(workspace, sources, {})
    if len(links) != len(sources):
        raise ValueError("apply the repository's source pages before organizing topics")
    topics = provider.organize_topics(_topic_messages(workspace, links))
    _validate_topic_groups(topics, {source.source_id for source in sources})

    overview_path = _repository_overview_path(repository_id)
    index_path = "wiki/INDEX.md"
    candidate_files = {
        overview_path: _render_repository_overview(repository_id, links, topics),
    }
    candidate_files[index_path] = _render_index(workspace, candidate_files)
    base_commit = workspace.current_commit()
    identity = "\n".join(
        [
            base_commit,
            "topics",
            repository_id,
            *(f"{topic.title}:{','.join(sorted(topic.source_ids))}" for topic in topics),
        ]
    )
    return Compilation(
        changeset=ChangeSet(
            changeset_id="chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            base_commit=base_commit,
            status=ChangeSetStatus.PROPOSED,
            operations=(
                ChangeOperation(
                    type=ChangeOperationType.UPDATE_PAGE,
                    path=overview_path,
                    origin=ChangeOrigin.LLM_COMPILATION,
                ),
                ChangeOperation(
                    type=ChangeOperationType.UPDATE_PAGE,
                    path=index_path,
                    origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
                ),
            ),
        ),
        candidate_files=candidate_files,
    )


def _compile_deterministically(
    workspace: Workspace,
    sources: list[CurrentSource],
    routed_pages: tuple[RoutedPage, ...],
) -> Compilation:
    """Render local excerpt pages while preserving existing source page groups."""
    operations: list[ChangeOperation] = []
    candidate_files: dict[str, str] = {}
    source_by_id = {source.source_id: source for source in sources}
    routed_source_ids = {
        source_id for routed_page in routed_pages for source_id in routed_page.source_ids
    }
    page_groups: list[tuple[str, list[CurrentSource]]] = [
        (
            routed_page.path,
            [source_by_id[source_id] for source_id in routed_page.source_ids],
        )
        for routed_page in routed_pages
    ]
    page_groups.extend(
        (_wiki_path(source), [source])
        for source in sources
        if source.source_id not in routed_source_ids
    )

    for path, page_sources in page_groups:
        stable_path = workspace.root / path
        operation_type = (
            ChangeOperationType.UPDATE_PAGE
            if stable_path.is_file()
            else ChangeOperationType.CREATE_PAGE
        )
        operations.append(
            ChangeOperation(
                type=operation_type,
                path=path,
                origin=ChangeOrigin.DETERMINISTIC_IMPORT,
            )
        )
        if len(page_sources) == 1:
            source = page_sources[0]
            content = _read_source_text(workspace, source)
            if "code" in source.tags:
                candidate_files[path] = _render_code_page(source, content)
            elif "conversation" in source.tags:
                candidate_files[path] = _render_conversation_page(source, content)
            elif "feishu" in source.tags:
                candidate_files[path] = _render_page(
                    source,
                    _feishu_facts(content),
                    preserve_markdown=True,
                )
            else:
                candidate_files[path] = _render_page(source, _meaningful_paragraphs(content))
        else:
            candidate_files[path] = _render_deterministic_group_page(
                workspace,
                page_sources,
            )

    repository_overviews = _repository_overview_pages(workspace, sources, page_groups)
    for path, content in repository_overviews.items():
        operations.append(
            ChangeOperation(
                type=(
                    ChangeOperationType.UPDATE_PAGE
                    if (workspace.root / path).is_file()
                    else ChangeOperationType.CREATE_PAGE
                ),
                path=path,
                origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
            )
        )
        candidate_files[path] = content

    index_path = "wiki/INDEX.md"
    index_target = workspace.root / index_path
    operations.append(
        ChangeOperation(
            type=(
                ChangeOperationType.UPDATE_PAGE
                if index_target.is_file()
                else ChangeOperationType.CREATE_PAGE
            ),
            path=index_path,
            origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
        )
    )
    candidate_files[index_path] = _render_index(workspace, candidate_files)

    base_commit = workspace.current_commit()
    page_identities: list[str] = []
    for path, page_sources in page_groups:
        source_versions = ",".join(
            f"{source.source_id}:{source.source_version}" for source in page_sources
        )
        page_identities.append(f"{path}:{source_versions}")
    page_identities.extend(_candidate_file_identities(candidate_files))
    identity = "\n".join(
        [
            base_commit,
            *page_identities,
        ]
    )
    changeset_id = "chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    return Compilation(
        changeset=ChangeSet(
            changeset_id=changeset_id,
            base_commit=base_commit,
            source_ids=tuple(source.source_id for source in sources),
            source_versions={source.source_id: source.source_version for source in sources},
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
        ),
        candidate_files=candidate_files,
    )


def _compile_missing_repository_overviews(workspace: Workspace) -> Compilation | None:
    """Backfill navigation pages when an existing Git Wiki gains this feature."""
    all_sources = _load_current_sources(workspace, set())
    overviews = {
        path: content
        for path, content in _repository_overview_pages(workspace, all_sources, []).items()
        if not (workspace.root / path).is_file()
    }
    if not overviews:
        return None

    index_path = "wiki/INDEX.md"
    candidate_files = dict(overviews)
    candidate_files[index_path] = _render_index(workspace, candidate_files)
    operations = [
        ChangeOperation(
            type=ChangeOperationType.CREATE_PAGE,
            path=path,
            origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
        )
        for path in sorted(overviews)
    ]
    operations.append(
        ChangeOperation(
            type=ChangeOperationType.UPDATE_PAGE,
            path=index_path,
            origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
        )
    )
    base_commit = workspace.current_commit()
    identity = "\n".join([base_commit, "repository-overviews", *sorted(overviews)])
    return Compilation(
        changeset=ChangeSet(
            changeset_id="chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            base_commit=base_commit,
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
        ),
        candidate_files=candidate_files,
    )


def _stale_pages(workspace: Workspace, selected: set[str]) -> tuple[StalePage, ...]:
    """Find applied pages that still own a source without a current version."""
    connection = sqlite3.connect(workspace.index_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT ps.page_path, ps.source_id, current_version.id AS current_version
            FROM page_sources AS ps
            JOIN sources AS source ON source.source_id = ps.source_id
            LEFT JOIN source_versions AS current_version
              ON current_version.source_id = source.id AND current_version.is_current = 1
            WHERE ps.page_path NOT LIKE 'wiki/pages/code/%'
            ORDER BY ps.page_path, ps.source_id
            """
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, tuple[list[str], list[str]]] = {}
    for row in rows:
        page_path = str(row["page_path"])
        stale_ids, current_ids = grouped.setdefault(page_path, ([], []))
        if row["current_version"] is None:
            stale_ids.append(str(row["source_id"]))
        else:
            current_ids.append(str(row["source_id"]))

    pages: list[StalePage] = []
    for path in sorted(grouped):
        stale_ids, current_ids = grouped[path]
        if stale_ids and (not selected or set(stale_ids) & selected):
            pages.append(
                StalePage(
                    path=path,
                    stale_source_ids=tuple(stale_ids),
                    current_source_ids=tuple(current_ids),
                )
            )
    return tuple(pages)


def _compile_stale_pages(
    workspace: Workspace,
    stale_pages: tuple[StalePage, ...],
) -> Compilation:
    """Propose archive/rebuild operations for pages with deleted sources."""
    current_sources = _load_current_sources(workspace, set())
    sources_by_id = {source.source_id: source for source in current_sources}
    candidate_files: dict[str, str] = {}
    operations: list[ChangeOperation] = []
    removed_paths: set[str] = set()
    active_source_ids: list[str] = []
    page_groups: list[tuple[str, list[CurrentSource]]] = []

    for stale_page in stale_pages:
        if not stale_page.current_source_ids:
            operations.append(
                ChangeOperation(
                    type=ChangeOperationType.ARCHIVE_PAGE,
                    path=stale_page.path,
                    origin=ChangeOrigin.DETERMINISTIC_CLEANUP,
                )
            )
            removed_paths.add(stale_page.path)
            continue

        page_sources = [sources_by_id[source_id] for source_id in stale_page.current_source_ids]
        page_groups.append((stale_page.path, page_sources))
        active_source_ids.extend(source.source_id for source in page_sources)
        if len(page_sources) == 1:
            source = page_sources[0]
            content = _read_source_text(workspace, source)
            if "code" in source.tags:
                candidate_files[stale_page.path] = _render_code_page(source, content)
            elif "conversation" in source.tags:
                candidate_files[stale_page.path] = _render_conversation_page(source, content)
            elif "feishu" in source.tags:
                candidate_files[stale_page.path] = _render_page(
                    source,
                    _feishu_facts(content),
                    preserve_markdown=True,
                )
            else:
                candidate_files[stale_page.path] = _render_page(
                    source,
                    _meaningful_paragraphs(content),
                )
        else:
            candidate_files[stale_page.path] = _render_deterministic_group_page(
                workspace,
                page_sources,
            )
        operations.append(
            ChangeOperation(
                type=ChangeOperationType.UPDATE_PAGE,
                path=stale_page.path,
                origin=ChangeOrigin.DETERMINISTIC_IMPORT,
            )
        )

    repository_ids = _repository_ids_for_sources(
        workspace,
        {source_id for page in stale_pages for source_id in page.stale_source_ids},
    )
    candidate_paths = {
        source.source_id: page_path
        for page_path, page_sources in page_groups
        for source in page_sources
    }
    for repository_id in sorted(repository_ids):
        repository_sources = [
            source for source in current_sources if source.repository_id == repository_id
        ]
        links = _repository_links(workspace, repository_sources, candidate_paths)
        overview_path = _repository_overview_path(repository_id)
        if links:
            candidate_files[overview_path] = _render_repository_overview(
                repository_id,
                links,
                _existing_repository_topics(workspace, repository_id, links),
            )
            operations.append(
                ChangeOperation(
                    type=(
                        ChangeOperationType.UPDATE_PAGE
                        if (workspace.root / overview_path).is_file()
                        else ChangeOperationType.CREATE_PAGE
                    ),
                    path=overview_path,
                    origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
                )
            )
        elif (workspace.root / overview_path).is_file():
            removed_paths.add(overview_path)
            operations.append(
                ChangeOperation(
                    type=ChangeOperationType.ARCHIVE_PAGE,
                    path=overview_path,
                    origin=ChangeOrigin.DETERMINISTIC_CLEANUP,
                )
            )

    index_path = "wiki/INDEX.md"
    candidate_files[index_path] = _render_index(
        workspace,
        candidate_files,
        removed_paths=removed_paths,
    )
    operations.append(
        ChangeOperation(
            type=(
                ChangeOperationType.UPDATE_PAGE
                if (workspace.root / index_path).is_file()
                else ChangeOperationType.CREATE_PAGE
            ),
            path=index_path,
            origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
        )
    )
    _add_relations_page(
        workspace,
        candidate_files,
        operations,
        removed_paths=removed_paths,
    )

    active_source_ids = list(dict.fromkeys(active_source_ids))
    base_commit = workspace.current_commit()
    identity = "\n".join(
        [
            base_commit,
            "stale-pages",
            *(
                f"{page.path}:{','.join(page.stale_source_ids)}"
                f"->{','.join(page.current_source_ids)}"
                for page in stale_pages
            ),
        ]
    )
    return Compilation(
        changeset=ChangeSet(
            changeset_id="chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            base_commit=base_commit,
            source_ids=tuple(active_source_ids),
            source_versions={
                source_id: sources_by_id[source_id].source_version
                for source_id in active_source_ids
            },
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
        ),
        candidate_files=candidate_files,
    )


def _repository_ids_for_sources(workspace: Workspace, source_ids: set[str]) -> tuple[str, ...]:
    if not source_ids:
        return ()
    placeholders = ", ".join("?" for _ in source_ids)
    connection = sqlite3.connect(workspace.index_path)
    try:
        rows = connection.execute(
            f"""
            SELECT DISTINCT revisions.repository_id
            FROM git_source_revisions AS revisions
            JOIN source_versions AS versions ON versions.id = revisions.source_version_id
            JOIN sources AS sources ON sources.id = versions.source_id
            WHERE sources.source_id IN ({placeholders})
            """,
            tuple(source_ids),
        ).fetchall()
    finally:
        connection.close()
    return tuple(sorted(str(row[0]) for row in rows))


def _compile_with_provider(
    workspace: Workspace,
    pending: list[CurrentSource],
    available_sources: list[CurrentSource],
    provider: PageCompilerProvider,
    *,
    routed_pages: tuple[RoutedPage, ...] = (),
    candidate_pages: tuple[PageCard, ...] = (),
    replaced_page_paths: tuple[str, ...] = (),
) -> Compilation:
    source_texts = {
        source.source_id: _read_source_text(workspace, source) for source in available_sources
    }
    prompt_context = workspace.prompt_context()
    reorganize_existing = bool(replaced_page_paths)
    plan_provider = getattr(provider, "plan_pages", None)
    plan: CompilationPlan | None = None
    if plan_provider is not None:
        plan = plan_provider(
            _planning_messages(
                pending,
                source_texts,
                candidate_pages,
                routed_source_ids={
                    source_id for page in routed_pages for source_id in page.source_ids
                },
                prompt_context=prompt_context,
                reorganize_existing=reorganize_existing,
            )
        )
        _validate_compilation_plan(
            plan,
            pending_ids={source.source_id for source in pending},
            available_source_ids={source.source_id for source in available_sources},
        )
    messages = _llm_messages(
        pending,
        source_texts,
        candidate_pages,
        routed_source_ids={source_id for page in routed_pages for source_id in page.source_ids},
        plan=plan,
        prompt_context=prompt_context,
        reorganize_existing=reorganize_existing,
    )
    changes = _normalize_llm_citations(provider.compile_pages(messages), source_texts)
    sources_by_id = {source.source_id: source for source in available_sources}
    _validate_llm_changes(
        workspace,
        changes,
        pending_ids={source.source_id for source in pending},
        available_source_ids=set(sources_by_id),
        source_texts=source_texts,
        routed_pages=routed_pages,
        candidate_pages=candidate_pages,
        replaced_page_paths=replaced_page_paths,
    )

    operations: list[ChangeOperation] = []
    candidate_files: dict[str, str] = {}
    used_source_ids: list[str] = []
    for change in changes:
        page_sources = [sources_by_id[source_id] for source_id in change.source_ids]
        page_path = _target_page_path(
            change,
            routed_pages,
            candidate_pages,
            pending_ids={source.source_id for source in pending},
        )
        if page_path in candidate_files:
            raise ValueError(f"provider returned duplicate page ownership: {page_path}")
        candidate_files[page_path] = _render_llm_page(
            change,
            page_sources,
            source_texts,
            related_pages=tuple(page for page in candidate_pages if page.path != page_path),
        )
        operation_type = (
            ChangeOperationType.UPDATE_PAGE
            if (workspace.root / page_path).is_file()
            else ChangeOperationType.CREATE_PAGE
        )
        details = {}
        if plan is not None:
            details["compilation_plan"] = _plan_for_path(
                plan,
                page_path,
                source_ids=change.source_ids,
            ).model_dump(mode="json")
        operations.append(
            ChangeOperation(
                type=operation_type,
                path=page_path,
                details=details,
                origin=ChangeOrigin.LLM_COMPILATION,
            )
        )
        used_source_ids.extend(change.source_ids)

    if not candidate_files:
        raise ValueError("provider returned no PageChange proposals")

    removed_paths = set(replaced_page_paths) - set(candidate_files)
    operations.extend(
        ChangeOperation(
            type=ChangeOperationType.ARCHIVE_PAGE,
            path=path,
            origin=ChangeOrigin.DETERMINISTIC_CLEANUP,
        )
        for path in sorted(removed_paths)
    )

    used_source_ids = list(dict.fromkeys(used_source_ids))
    used_sources = [sources_by_id[source_id] for source_id in used_source_ids]
    index_path = "wiki/INDEX.md"
    index_target = workspace.root / index_path
    operations.append(
        ChangeOperation(
            type=(
                ChangeOperationType.UPDATE_PAGE
                if index_target.is_file()
                else ChangeOperationType.CREATE_PAGE
            ),
            path=index_path,
            origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
        )
    )
    candidate_files[index_path] = _render_index(
        workspace,
        candidate_files,
        removed_paths=removed_paths,
    )
    _add_relations_page(
        workspace,
        candidate_files,
        operations,
        removed_paths=removed_paths,
    )

    base_commit = workspace.current_commit()
    pending_ids = {source.source_id for source in pending}
    page_identities = [
        f"{_target_page_path(change, routed_pages, candidate_pages, pending_ids=pending_ids)}:"
        f"{change.title}"
        for change in changes
    ]
    identity = "\n".join(
        [
            base_commit,
            "llm",
            *(f"{source.source_id}:{source.source_version}" for source in used_sources),
            *page_identities,
            *sorted(removed_paths),
            *_candidate_file_identities(candidate_files),
        ]
    )
    changeset_id = "chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    return Compilation(
        changeset=ChangeSet(
            changeset_id=changeset_id,
            base_commit=base_commit,
            source_ids=tuple(used_source_ids),
            source_versions={source.source_id: source.source_version for source in used_sources},
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
        ),
        candidate_files=candidate_files,
    )


def _candidate_file_identities(candidate_files: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        f"{path}:{hashlib.sha256(content.encode()).hexdigest()}"
        for path, content in sorted(candidate_files.items())
    )


def _llm_messages(
    pending: list[CurrentSource],
    source_texts: dict[str, str],
    candidate_pages: tuple[PageCard, ...],
    *,
    routed_source_ids: set[str],
    plan: CompilationPlan | None = None,
    prompt_context: str = "",
    reorganize_existing: bool = False,
) -> tuple[dict[str, str], ...]:
    source_blocks = []
    for source in pending:
        source_blocks.append(
            f"SOURCE_ID: {source.source_id}\n"
            f"TITLE: {source.title}\n"
            f"CATEGORY: {source.category}\n"
            f"CONTENT:\n{source_texts[source.source_id]}"
        )
    pending_ids = {source.source_id for source in pending}
    routed_context = "\n\n---\n\n".join(
        f"SOURCE_ID: {source_id}\nCONTENT:\n{source_text}"
        for source_id, source_text in source_texts.items()
        if source_id in routed_source_ids and source_id not in pending_ids
    )
    cards = "\n\n".join(
        "\n".join(
            [
                f"PATH: {page.path}",
                f"TITLE: {page.title}",
                f"SUMMARY: {page.summary}",
                f"SOURCES: {','.join(page.source_ids)}",
            ]
        )
        for page in candidate_pages
    )
    workspace_context = "\n\nWORKSPACE CONTRACT:\n" + prompt_context if prompt_context else ""
    plan_context = (
        "\n\nCOMPILATION PLAN:\n" + json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
        if plan is not None
        else ""
    )
    reorganization_context = (
        " This is an explicit full conversation reorganization. Group semantically related "
        "sessions into coherent topic pages, keep unrelated topics separate, and preserve exact "
        "task counts, decisions, and named entities from the sources."
        if reorganize_existing
        else ""
    )
    return (
        {
            "role": "system",
            "content": (
                "You propose concise Markdown Wiki page changes. Return exactly "
                '{"changes":[{"path":"wiki/pages/<slug>.md","title":"...",'
                '"page_type":"concept","summary":"one line","body":"...",'
                '"source_ids":["<source_id>"],"citations":[{"source_id":"<source_id>",'
                '"locator":"chars:0-10"}]}]}. Do not include action in changes; action '
                "belongs to the plan only. Every pending source must have one citation with "
                "an exact character locator covering a complete factual sentence, never only a "
                "Markdown heading or source title, and never ending mid-sentence. Every pending "
                "source "
                "must appear in exactly one change. You may extend one existing page card by "
                "including all of its listed source IDs plus relevant pending sources; do not "
                "move sources between existing pages. Paths must be wiki/pages/<filename>.md. "
                "Do not return frontmatter in body; the local compiler adds it. "
                "Every citation locator must be a character range in its source. "
                "Return no INDEX or raw file changes." + reorganization_context + workspace_context
            ),
        },
        {
            "role": "user",
            "content": "PENDING SOURCES:\n"
            + "\n\n---\n\n".join(source_blocks)
            + (
                "\n\nEXISTING SOURCE CONTEXT (only use it when preserving an existing "
                "source group):\n" + routed_context
                if routed_context
                else ""
            )
            + ("\n\nEXISTING PAGE CARDS:\n" + cards if cards else "")
            + plan_context,
        },
    )


def _planning_messages(
    pending: list[CurrentSource],
    source_texts: dict[str, str],
    candidate_pages: tuple[PageCard, ...],
    *,
    routed_source_ids: set[str],
    prompt_context: str,
    reorganize_existing: bool = False,
) -> tuple[dict[str, str], ...]:
    base = _llm_messages(
        pending,
        source_texts,
        candidate_pages,
        routed_source_ids=routed_source_ids,
        prompt_context=prompt_context,
        reorganize_existing=reorganize_existing,
    )
    return (
        {
            "role": "system",
            "content": (
                'Return exactly {"plan":{"pages":[{"path":"wiki/pages/<slug>.md",'
                '"action":"create","source_ids":["<source_id>"],"reason":"...",'
                '"related_pages":[]}],"conflicts":[]}}. '
                "List every pending source exactly once, choose create or update, and explain "
                "the routing in one short reason. Do not write Markdown or hidden reasoning."
                + (
                    " Group related conversation sessions by concrete topic; do not merge them "
                    "only because they share a broad repository or product name."
                    if reorganize_existing
                    else ""
                )
                + ("\n\nWORKSPACE CONTRACT:\n" + prompt_context if prompt_context else "")
            ),
        },
        base[1],
    )


def _validate_compilation_plan(
    plan: CompilationPlan,
    *,
    pending_ids: set[str],
    available_source_ids: set[str],
) -> None:
    planned_ids = [source_id for page in plan.pages for source_id in page.source_ids]
    if set(planned_ids) != pending_ids:
        missing = sorted(pending_ids - set(planned_ids))
        extra = sorted(set(planned_ids) - pending_ids)
        message = "CompilationPlan source coverage mismatch"
        if missing:
            message += "; missing: " + ", ".join(missing)
        if extra:
            message += "; unsupported: " + ", ".join(extra)
        raise ValueError(message)
    if len(planned_ids) != len(set(planned_ids)):
        raise ValueError("CompilationPlan must assign each pending source exactly once")
    if not set(planned_ids) <= available_source_ids:
        raise ValueError("CompilationPlan references an unavailable source")


def _plan_for_path(
    plan: CompilationPlan,
    path: str,
    *,
    source_ids: tuple[str, ...],
) -> PlannedPage:
    for page in plan.pages:
        if page.path == path:
            return page
    source_set = set(source_ids)
    for page in plan.pages:
        if set(page.source_ids) == source_set:
            return page
    raise ValueError(f"CompilationPlan has no entry for generated page: {path}")


def _validate_llm_changes(
    workspace: Workspace,
    changes: tuple[PageChange, ...],
    *,
    pending_ids: set[str],
    available_source_ids: set[str],
    source_texts: dict[str, str],
    routed_pages: tuple[RoutedPage, ...] = (),
    candidate_pages: tuple[PageCard, ...] = (),
    replaced_page_paths: tuple[str, ...] = (),
) -> None:
    covered_source_ids: set[str] = set()
    for change in changes:
        if change.path == "wiki/INDEX.md" or not change.path.startswith("wiki/pages/"):
            raise ValueError(f"provider returned an unsupported Wiki path: {change.path}")
        try:
            validate_llm_title(change.title)
        except ValueError as exc:
            raise ValueError(f"provider returned invalid page title: {change.path}") from exc
        try:
            validate_llm_summary(change.summary)
        except ValueError as exc:
            raise ValueError(f"provider returned invalid page summary: {change.path}") from exc
        try:
            validate_llm_body(change.body)
        except ValueError as exc:
            raise ValueError(
                f"provider returned reserved page body content: {change.path}"
            ) from exc
        change_source_ids = set(change.source_ids)
        if not change_source_ids <= available_source_ids:
            raise ValueError(f"provider referenced an unavailable source: {change.path}")
        duplicate_source_ids = covered_source_ids & set(change.source_ids)
        if duplicate_source_ids:
            listed = ", ".join(sorted(duplicate_source_ids))
            raise ValueError(f"provider assigned a source to multiple Wiki pages: {listed}")
        covered_source_ids.update(change.source_ids)
        if not {citation.source_id for citation in change.citations} <= available_source_ids:
            raise ValueError(
                f"provider returned a citation for an unavailable source: {change.path}"
            )
        for citation in change.citations:
            source_text = source_texts[citation.source_id]
            start, end = _locator_range(citation.locator)
            if end > len(source_text):
                raise ValueError(f"provider returned an out-of-range citation: {citation.locator}")
            cited_text = source_text[start:end].strip()
            if "\n" not in cited_text and cited_text.startswith("#"):
                raise ValueError("provider citation must quote a source fact, not a heading")
            if end < len(source_text) and source_text[end - 1] not in ".!?。！？；;\n":
                raise ValueError("provider citation must end at a source sentence boundary")
        target_path = _target_page_path(
            change,
            routed_pages,
            candidate_pages,
            pending_ids=pending_ids,
        )
        if target_path in replaced_page_paths:
            expected_existing_sources = workspace.source_ids_for_page(target_path)
            if set(expected_existing_sources) != change_source_ids:
                raise ValueError("provider cannot reuse a Wiki path owned by different sources")
        else:
            matching_routed_pages = [
                page for page in routed_pages if change_source_ids & set(page.source_ids)
            ]
            if matching_routed_pages:
                if len(matching_routed_pages) != 1 or change_source_ids != set(
                    matching_routed_pages[0].source_ids
                ):
                    raise ValueError("provider cannot change an existing Wiki page ownership group")
                expected_existing_sources = matching_routed_pages[0].source_ids
            else:
                existing_source_ids = change_source_ids - pending_ids
                matching_cards = [
                    page for page in candidate_pages if set(page.source_ids) == existing_source_ids
                ]
                if existing_source_ids and len(matching_cards) != 1:
                    raise ValueError("provider can only extend a supplied existing Wiki page")
                expected_existing_sources = matching_cards[0].source_ids if matching_cards else ()
        _validate_page_ownership(
            workspace,
            change,
            target_path,
            expected_existing_sources=expected_existing_sources,
        )
    for routed_page in routed_pages:
        if not any(set(change.source_ids) == set(routed_page.source_ids) for change in changes):
            raise ValueError(
                "provider must update each routed Wiki page with exactly its existing "
                f"sources: {routed_page.path}"
            )
    if covered_source_ids & pending_ids != pending_ids:
        omitted = ", ".join(sorted(pending_ids - covered_source_ids))
        raise ValueError(f"provider omitted pending sources: {omitted}")


def _validate_page_ownership(
    workspace: Workspace,
    change: PageChange,
    canonical_path: str | None = None,
    *,
    expected_existing_sources: tuple[str, ...] = (),
) -> None:
    """Reject an LLM update when the existing page belongs to other sources."""
    path = canonical_path or change.path
    page = workspace.root / path
    if not page.is_file():
        return
    fields = _frontmatter_fields(page.read_text(encoding="utf-8"))
    raw_sources = fields.get("sources")
    if raw_sources is None:
        raise ValueError(f"existing Wiki page has no sources ownership: {path}")
    try:
        decoded = json.loads(raw_sources)
    except json.JSONDecodeError as exc:
        raise ValueError(f"existing Wiki page has invalid sources ownership: {path}") from exc
    if not isinstance(decoded, list) or not all(
        isinstance(source_id, str) for source_id in decoded
    ):
        raise ValueError(f"existing Wiki page has invalid sources ownership: {path}")
    if set(decoded) != set(expected_existing_sources):
        raise ValueError(f"provider cannot update Wiki page owned by different sources: {path}")


def _locator_range(locator: str) -> tuple[int, int]:
    match = re.fullmatch(r"chars:(\d+)-(\d+)", locator)
    if match is None:
        raise ValueError(f"invalid citation locator: {locator}")
    return int(match.group(1)), int(match.group(2))


def _normalize_llm_citations(
    changes: tuple[PageChange, ...],
    source_texts: dict[str, str],
) -> tuple[PageChange, ...]:
    """Keep model-proposed citations on complete source facts before validation."""
    normalized_changes = []
    for change in changes:
        citations = []
        for raw_citation in change.citations:
            citation = PageCitation.model_validate(raw_citation)
            locator = citation.locator
            if citation.source_id in source_texts:
                locator = _normalized_citation_locator(locator, source_texts[citation.source_id])
            citations.append(citation.model_copy(update={"locator": locator}))
        normalized_changes.append(change.model_copy(update={"citations": tuple(citations)}))
    return tuple(normalized_changes)


def _normalized_citation_locator(locator: str, source_text: str) -> str:
    start, end = _locator_range(locator)
    if source_text[start:end].lstrip().startswith("#"):
        first_paragraph = source_text.find("\n\n", start)
        if first_paragraph != -1:
            start = first_paragraph + 2
    end = max(start, end)
    while end < len(source_text) and (end == start or source_text[end - 1] not in ".!?。！？；;\n"):
        end += 1
    return f"chars:{start}-{end}"


def _render_llm_page(
    change: PageChange,
    sources: list[CurrentSource],
    source_texts: dict[str, str],
    *,
    related_pages: tuple[PageCard, ...] = (),
) -> str:
    source_ids = tuple(source.source_id for source in sources)
    tags = tuple(dict.fromkeys(source.category for source in sources))
    source_versions = {source.source_id: source.source_version for source in sources}
    lines = [
        "---",
        f"title: {json.dumps(change.title, ensure_ascii=False)}",
        f"type: {change.page_type}",
        f"summary: {json.dumps(change.summary, ensure_ascii=False)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"sources: {json.dumps(source_ids, ensure_ascii=False)}",
        f"source_versions: {json.dumps(source_versions, ensure_ascii=False)}",
        "---",
        "",
        f"# {change.title}",
        "",
        "## Model summary (unverified)",
        "",
        change.body.rstrip(),
        "",
    ]
    if related_pages:
        lines.extend(["## Related pages", ""])
        for page in related_pages:
            lines.append(f"- [{_escape_link_text(page.title)}]({page.path.rsplit('/', 1)[-1]})")
        lines.append("")
    lines.extend(
        [
            "## Verified facts",
            "",
        ]
    )
    for index, citation in enumerate(change.citations):
        footnote = f"source-{index + 1}-{citation.source_id[:8]}"
        quote = _citation_excerpt(citation.locator, citation.source_id, source_texts)
        lines.append(f"- {quote} [^{footnote}]")
    lines.extend(
        [
            "",
            "## Sources",
            "",
        ]
    )
    for index, citation in enumerate(change.citations):
        quote = _citation_excerpt(citation.locator, citation.source_id, source_texts)
        footnote = f"source-{index + 1}-{citation.source_id[:8]}"
        lines.append(f"- {quote} [^{footnote}]")
        lines.append(
            f"[^{footnote}]: source `{citation.source_id}` · revision "
            f"`{source_versions[citation.source_id]}` · `{citation.locator}`"
        )
    return "\n".join(lines) + "\n"


def _citation_excerpt(
    locator: str,
    source_id: str,
    source_texts: dict[str, str],
) -> str:
    """Render verified facts from the locally validated source slice only."""
    start, end = _locator_range(locator)
    quote = " ".join(source_texts[source_id][start:end].splitlines()).strip()
    if not quote:
        raise ValueError(f"provider returned an empty citation: {locator}")
    return quote


def compilation_payload(stored: StoredChangeSet) -> dict[str, object]:
    files = sorted(path for path in stored.candidate_files if path != "wiki/INDEX.md")
    if "wiki/INDEX.md" in stored.candidate_files:
        files.append("wiki/INDEX.md")
    return {
        "changeset_id": stored.changeset.changeset_id,
        "status": stored.changeset.status.value,
        "files": files,
    }


def propose_agent_update(
    workspace: Workspace,
    *,
    question: str,
    answer: str,
    evidence: Sequence[EvidencePayload],
    wiki_pages: Sequence[str],
    provider: OpenAICompatibleProvider,
    allow_local: bool = False,
) -> Compilation | None:
    """Turn one evidence-backed Agent answer into a reviewable page proposal."""
    if not answer.strip() or answer.strip() == "不知道" or not evidence or not wiki_pages:
        return None

    evidence_versions = {item["source_id"]: item["source_version"] for item in evidence}
    candidates: list[AgentUpdateCandidate] = []
    for path in dict.fromkeys(wiki_pages):
        if not path.startswith("wiki/pages/"):
            continue
        page = workspace.root / path
        if not page.is_file() or page.is_symlink():
            continue
        content = page.read_text(encoding="utf-8")
        summary = _parse_page_summary(path, content)
        page_sources = candidate_page_sources({path: content}).get(path)
        if summary is None or not page_sources:
            continue
        if not set(page_sources) <= set(evidence_versions):
            continue
        loaded = _load_current_sources(workspace, set(page_sources))
        sources_by_id = {source.source_id: source for source in loaded}
        if set(sources_by_id) != set(page_sources):
            continue
        sources = tuple(sources_by_id[source_id] for source_id in page_sources)
        if any(source.source_version != evidence_versions[source.source_id] for source in sources):
            continue
        if (
            any(source.sensitivity is Sensitivity.LOCAL_ONLY for source in sources)
            and not allow_local
        ):
            continue
        candidates.append(
            AgentUpdateCandidate(
                path=path,
                summary=summary,
                source_ids=page_sources,
                content=content,
                sources=sources,
            )
        )

    if not candidates:
        return None
    candidate = min(
        candidates,
        key=lambda item: (
            {"synthesis": 0, "concept": 1, "entity": 2}[item.summary.page_type],
            item.path,
        ),
    )
    source_texts = {
        source.source_id: _read_source_text(workspace, source) for source in candidate.sources
    }
    change = provider.propose_update(
        _agent_update_messages(
            question,
            answer,
            evidence,
            candidate,
            prompt_context=workspace.prompt_context(),
        )
    )
    if change is None:
        return None
    _validate_agent_update(change, candidate, evidence, source_texts)

    candidate_files = {
        candidate.path: _render_llm_page(
            change,
            list(candidate.sources),
            source_texts,
        )
    }
    if candidate_files[candidate.path] == candidate.content:
        return None
    operations = [
        ChangeOperation(
            type=ChangeOperationType.UPDATE_PAGE,
            path=candidate.path,
            details={"origin": "agent", "question": question},
            origin=ChangeOrigin.AGENT_PROPOSAL,
        )
    ]
    index_path = "wiki/INDEX.md"
    candidate_files[index_path] = _render_index(workspace, candidate_files)
    operations.append(
        ChangeOperation(
            type=ChangeOperationType.UPDATE_PAGE,
            path=index_path,
            origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
        )
    )
    _add_relations_page(workspace, candidate_files, operations)

    base_commit = workspace.current_commit()
    identity = "\n".join(
        [
            base_commit,
            "agent-update",
            question,
            candidate.path,
            *(f"{source.source_id}:{source.source_version}" for source in candidate.sources),
            change.body,
        ]
    )
    changeset_id = "chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    return Compilation(
        changeset=ChangeSet(
            changeset_id=changeset_id,
            base_commit=base_commit,
            source_ids=candidate.source_ids,
            source_versions={
                source.source_id: source.source_version for source in candidate.sources
            },
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
        ),
        candidate_files=candidate_files,
    )


def _agent_update_messages(
    question: str,
    answer: str,
    evidence: Sequence[EvidencePayload],
    candidate: AgentUpdateCandidate,
    *,
    prompt_context: str,
) -> tuple[dict[str, str], ...]:
    evidence_payload = [
        {
            "source_id": item["source_id"],
            "locator": item["locator"],
            "text": item["text"][:2000],
        }
        for item in evidence
    ]
    return (
        {
            "role": "system",
            "content": (
                "You propose one concise Wiki page update from an answered question. Return "
                'JSON as {"change": null} or {"change": PageChange}. Update exactly the '
                f"candidate path {candidate.path}. Keep page type {candidate.summary.page_type}. "
                "Use only the supplied evidence, and cite every source ID in the change. "
                "Do not return frontmatter, Verified facts, Sources, or footnote markers in body. "
                "If the answer is not worth retaining, return null."
                + ("\n\nWORKSPACE CONTRACT:\n" + prompt_context if prompt_context else "")
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "answer": answer,
                    "evidence": evidence_payload,
                    "candidate_page": {
                        "path": candidate.path,
                        "title": candidate.summary.title,
                        "type": candidate.summary.page_type,
                        "summary": candidate.summary.summary,
                        "content": candidate.content[:8000],
                    },
                },
                ensure_ascii=False,
            ),
        },
    )


def _validate_agent_update(
    change: PageChange,
    candidate: AgentUpdateCandidate,
    evidence: Sequence[EvidencePayload],
    source_texts: dict[str, str],
) -> None:
    if change.path != candidate.path:
        raise ValueError("agent update must keep the selected Wiki page path")
    if change.page_type != candidate.summary.page_type:
        raise ValueError("agent update must keep the selected Wiki page type")
    if set(change.source_ids) != set(candidate.source_ids):
        raise ValueError("agent update must keep the selected Wiki page sources")
    evidence_keys = {(item["source_id"], item["locator"]) for item in evidence}
    for citation in change.citations:
        if (citation.source_id, citation.locator) not in evidence_keys:
            raise ValueError("agent update citation must come from read evidence")
        start, end = _locator_range(citation.locator)
        if end > len(source_texts[citation.source_id]):
            raise ValueError("agent update citation is outside the current source")


def _require_known_sources(workspace: Workspace, source_ids: set[str]) -> None:
    if not source_ids:
        return
    placeholders = ", ".join("?" for _ in source_ids)
    connection = sqlite3.connect(workspace.index_path)
    try:
        rows = connection.execute(
            f"SELECT source_id FROM sources WHERE source_id IN ({placeholders})",
            tuple(source_ids),
        ).fetchall()
    finally:
        connection.close()
    known_source_ids = {str(row[0]) for row in rows}
    unknown_source_ids = source_ids - known_source_ids
    if unknown_source_ids:
        listed = ", ".join(sorted(unknown_source_ids))
        raise ValueError(f"unknown source id: {listed}")


def _load_pending_sources(workspace: Workspace, source_ids: set[str]) -> list[CurrentSource]:
    pending = _load_current_sources(workspace, source_ids, pending_only=True)
    code_wiki_repositories = _deterministic_code_wiki_repositories(workspace)
    return [
        source
        for source in pending
        if not (
            source.repository_id in code_wiki_repositories
            and any(tag in {"code", "code-module"} for tag in source.tags)
        )
    ]


def _deterministic_code_wiki_repositories(workspace: Workspace) -> set[str]:
    connection = sqlite3.connect(workspace.index_path)
    try:
        rows = connection.execute(
            """
            SELECT DISTINCT revisions.repository_id
            FROM page_sources AS pages
            JOIN sources AS sources ON sources.source_id = pages.source_id
            JOIN source_versions AS versions ON versions.source_id = sources.id
            JOIN git_source_revisions AS revisions
              ON revisions.source_version_id = versions.id
            WHERE pages.page_path LIKE 'wiki/pages/code/%'
              AND versions.is_current = 1
            """
        ).fetchall()
    finally:
        connection.close()
    return {str(row[0]) for row in rows}


def _load_current_sources(
    workspace: Workspace,
    source_ids: set[str],
    *,
    pending_only: bool = False,
) -> list[CurrentSource]:
    where_clause = """
        WHERE v.is_current = 1
    """
    if pending_only:
        where_clause += " AND (a.source_version_id IS NULL OR a.source_version_id != v.id)"
    parameters: tuple[str, ...] = ()
    if source_ids:
        placeholders = ", ".join("?" for _ in source_ids)
        where_clause += f" AND s.source_id IN ({placeholders})"
        parameters = tuple(source_ids)
    connection = sqlite3.connect(workspace.index_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"""
            SELECT
                s.source_id,
                v.id AS source_version,
                v.title,
                v.category,
                v.tags_json,
                v.observed_at,
                v.sensitivity,
                b.snapshot_path,
                revisions.repository_id,
                repositories.name AS repository_name,
                revisions.relative_path
            FROM source_versions AS v
            JOIN sources AS s ON s.id = v.source_id
            JOIN blobs AS b ON b.id = v.blob_id
            LEFT JOIN applied_source_versions AS a ON a.source_id = s.source_id
            LEFT JOIN git_source_revisions AS revisions ON revisions.source_version_id = v.id
            LEFT JOIN git_repositories AS repositories
              ON repositories.repository_id = revisions.repository_id
            {where_clause}
            ORDER BY s.source_id
            """,
            parameters,
        ).fetchall()
    finally:
        connection.close()

    sources: list[CurrentSource] = []
    for row in rows:
        sources.append(
            CurrentSource(
                source_id=str(row["source_id"]),
                source_version=int(row["source_version"]),
                title=str(row["title"]),
                category=str(row["category"]),
                tags=_tags_from_json(str(row["tags_json"])),
                updated=str(row["observed_at"])[:10],
                snapshot_path=str(row["snapshot_path"]),
                sensitivity=Sensitivity(str(row["sensitivity"])),
                repository_id=(
                    str(row["repository_id"]) if row["repository_id"] is not None else None
                ),
                repository_name=(
                    str(row["repository_name"]) if row["repository_name"] is not None else None
                ),
                relative_path=(
                    str(row["relative_path"]) if row["relative_path"] is not None else None
                ),
            )
        )
    return sources


def _routed_pages_for_pending_sources(
    workspace: Workspace,
    pending: list[CurrentSource],
) -> tuple[RoutedPage, ...]:
    """Resolve every pending source back to its one existing page group, if any."""
    routed_pages: dict[str, RoutedPage] = {}
    for source in pending:
        page_paths = workspace.page_paths_for_source(source.source_id)
        if not page_paths:
            continue
        if len(page_paths) != 1:
            raise ValueError("source belongs to multiple Wiki pages: " + source.source_id)
        page_path = page_paths[0]
        source_ids = workspace.source_ids_for_page(page_path)
        if source.source_id not in source_ids:
            raise ValueError("recorded Wiki page ownership is inconsistent")
        routed_pages[page_path] = RoutedPage(path=page_path, source_ids=source_ids)

    for routed_page in routed_pages.values():
        for source_id in routed_page.source_ids:
            page_paths = workspace.page_paths_for_source(source_id)
            if page_paths != (routed_page.path,):
                raise ValueError("source belongs to multiple Wiki pages: " + source_id)
    return tuple(routed_pages[path] for path in sorted(routed_pages))


def _replaceable_page_paths(
    workspace: Workspace,
    sources: list[CurrentSource],
) -> tuple[str, ...]:
    """Return pages wholly owned by an explicitly selected source set."""
    selected = {source.source_id for source in sources}
    paths = {
        path for source in sources for path in workspace.page_paths_for_source(source.source_id)
    }
    for path in paths:
        if not set(workspace.source_ids_for_page(path)) <= selected:
            raise ValueError("cannot reorganize a Wiki page containing unselected sources")
    return tuple(sorted(paths))


def _add_relations_page(
    workspace: Workspace,
    candidate_files: dict[str, str],
    operations: list[ChangeOperation],
    *,
    removed_paths: set[str] | None = None,
) -> None:
    """Maintain one small backlink map for the related-page links we generate."""
    removed = removed_paths or set()
    pages_root = workspace.wiki_dir / "pages"
    page_contents = {
        str(path.relative_to(workspace.root)): path.read_text(encoding="utf-8")
        for path in pages_root.glob("*.md")
        if path.is_file()
        and not path.is_symlink()
        and str(path.relative_to(workspace.root)) not in removed
    }
    page_contents.update(
        {
            path: content
            for path, content in candidate_files.items()
            if path.startswith("wiki/pages/") and path not in removed
        }
    )
    titles = {
        path: summary.title
        for path, content in page_contents.items()
        if (summary := _parse_page_summary(path, content)) is not None
    }
    pairs: set[tuple[str, str]] = set()
    for path, content in page_contents.items():
        for target in _related_page_paths(path, content):
            if target in titles:
                pairs.add((path, target) if path < target else (target, path))

    relations_path = "wiki/RELATIONS.md"
    existing = workspace.root / relations_path
    if not pairs and not existing.is_file():
        return
    lines = [
        "# Wiki relations",
        "",
        "Related-page links generated during compilation.",
    ]
    for left, right in sorted(pairs):
        lines.extend(
            [
                "",
                f"- [{_escape_link_text(titles[left])}]({left.removeprefix('wiki/')}) ↔ "
                f"[{_escape_link_text(titles[right])}]({right.removeprefix('wiki/')})",
            ]
        )
    candidate_files[relations_path] = "\n".join(lines) + "\n"
    operations.append(
        ChangeOperation(
            type=(
                ChangeOperationType.UPDATE_PAGE
                if existing.is_file()
                else ChangeOperationType.CREATE_PAGE
            ),
            path=relations_path,
            origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
        )
    )


def _related_page_paths(page_path: str, content: str) -> tuple[str, ...]:
    """Read same-directory Markdown links from a generated Wiki page."""
    targets: list[str] = []
    for match in _RELATED_PAGE_LINK.finditer(content):
        target = match.group("path")
        if "/" in target or target.startswith("."):
            continue
        resolved = f"wiki/pages/{target}"
        if resolved != page_path and resolved not in targets:
            targets.append(resolved)
    return tuple(targets)


def _candidate_pages_for_pending_sources(
    workspace: Workspace,
    pending: list[CurrentSource],
    routed_pages: tuple[RoutedPage, ...],
    *,
    allow_local: bool,
) -> tuple[PageCard, ...]:
    """Find a few existing pages a new source may extend.

    ponytail: title/summary token overlap is deliberately simple; add embeddings only if
    the public evaluation shows this local router misses real topic updates.
    """
    pending_terms = _terms(
        " ".join(" ".join((source.title, source.category, *source.tags)) for source in pending)
    )
    if not pending_terms:
        return ()
    routed_paths = {page.path for page in routed_pages}
    sources_by_id = {source.source_id: source for source in _load_current_sources(workspace, set())}
    scored: list[tuple[tuple[int, int], PageCard]] = []
    for path in sorted(workspace.wiki_dir.joinpath("pages").glob("*.md")):
        relative_path = str(path.relative_to(workspace.root))
        if relative_path in routed_paths or path.is_symlink():
            continue
        source_ids = workspace.source_ids_for_page(relative_path)
        page_sources = [sources_by_id.get(source_id) for source_id in source_ids]
        if not source_ids or any(source is None for source in page_sources):
            continue
        if not allow_local and any(
            source.sensitivity is Sensitivity.LOCAL_ONLY for source in page_sources if source
        ):
            continue
        summary = _parse_page_summary(relative_path, path.read_text(encoding="utf-8"))
        if summary is None:
            continue
        overlap = pending_terms & _terms(f"{summary.title} {summary.summary}")
        if overlap:
            scored.append(
                (
                    (len(overlap), sum(len(term) for term in overlap)),
                    PageCard(
                        path=relative_path,
                        title=summary.title,
                        summary=summary.summary,
                        source_ids=source_ids,
                    ),
                )
            )
    return tuple(
        card
        for _, card in sorted(
            scored,
            key=lambda item: (-item[0][0], -item[0][1], item[1].path),
        )[:6]
    )


def _tags_from_json(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(tag for tag in decoded if isinstance(tag, str))


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for match in _WORDS.finditer(text):
        token = match.group().lower()
        if _CJK.fullmatch(token):
            terms.add(token)
            terms.update(token[index : index + 2] for index in range(len(token) - 1))
        else:
            terms.add(token)
    return terms


def _target_page_path(
    change: PageChange,
    routed_pages: tuple[RoutedPage, ...],
    candidate_pages: tuple[PageCard, ...] = (),
    *,
    pending_ids: set[str] | None = None,
) -> str:
    for routed_page in routed_pages:
        if set(change.source_ids) == set(routed_page.source_ids):
            return routed_page.path
    existing_source_ids = set(change.source_ids) - (pending_ids or set())
    for page in candidate_pages:
        if set(page.source_ids) == existing_source_ids:
            return page.path
    return _canonical_page_path(change.source_ids)


def _render_deterministic_group_page(
    workspace: Workspace,
    sources: list[CurrentSource],
) -> str:
    """Use source excerpts as the local fallback for a previously merged Wiki page."""
    excerpts: list[tuple[CurrentSource, str, str]] = []
    for source in sources:
        fact = _meaningful_paragraphs(_read_source_text(workspace, source))[0]
        excerpts.append(
            (
                source,
                " ".join(line.strip() for line in fact.quote.splitlines()),
                f"chars:{fact.start}-{fact.start + len(fact.quote)}",
            )
        )

    title = " / ".join(source.title for source in sources)
    summary = " · ".join(quote for _, quote, _ in excerpts)
    tags = tuple(
        dict.fromkeys(tag for source in sources for tag in (source.category, *source.tags))
    )
    source_ids = tuple(source.source_id for source in sources)
    source_versions = {source.source_id: source.source_version for source in sources}
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        "type: concept",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"sources: {json.dumps(source_ids, ensure_ascii=False)}",
        f"source_versions: {json.dumps(source_versions, ensure_ascii=False)}",
        "---",
        "",
        f"# {title}",
        "",
        "## Verified facts",
        "",
    ]
    for index, (source, quote, _) in enumerate(excerpts, start=1):
        footnote = f"source-{index}-{source.source_id[:8]}"
        lines.append(f"- {quote} [^{footnote}]")
    lines.append("")
    for index, (source, _, locator) in enumerate(excerpts, start=1):
        footnote = f"source-{index}-{source.source_id[:8]}"
        lines.append(
            f"[^{footnote}]: source `{source.source_id}` · revision "
            f"`{source.source_version}` · `{locator}`"
        )
    return "\n".join(lines) + "\n"


def _repository_overview_pages(
    workspace: Workspace,
    changed_sources: list[CurrentSource],
    page_groups: list[tuple[str, list[CurrentSource]]],
) -> dict[str, str]:
    """Create a small navigation page for each Git repository touched by this run."""
    changed_repositories = {
        source.repository_id for source in changed_sources if source.repository_id is not None
    }
    if not changed_repositories:
        return {}

    candidate_paths = {
        source.source_id: page_path for page_path, sources in page_groups for source in sources
    }
    pages: dict[str, str] = {}
    all_sources = _load_current_sources(workspace, set())
    for repository_id in sorted(changed_repositories):
        repository_sources = [
            source for source in all_sources if source.repository_id == repository_id
        ]
        links = _repository_links(workspace, repository_sources, candidate_paths)
        if not links:
            continue
        pages[_repository_overview_path(repository_id)] = _render_repository_overview(
            repository_id,
            links,
            _existing_repository_topics(workspace, repository_id, links),
        )
    return pages


def _repository_overview_path(repository_id: str) -> str:
    return f"wiki/pages/repository-{repository_id[:12]}.md"


def _repository_links(
    workspace: Workspace,
    sources: list[CurrentSource],
    candidate_paths: dict[str, str],
) -> list[tuple[CurrentSource, str]]:
    links: list[tuple[CurrentSource, str]] = []
    for source in sources:
        page_path = candidate_paths.get(source.source_id)
        if page_path is None:
            existing_paths = workspace.page_paths_for_source(source.source_id)
            if len(existing_paths) != 1:
                continue
            page_path = existing_paths[0]
        links.append((source, page_path))
    return links


def _existing_repository_topics(
    workspace: Workspace,
    repository_id: str,
    links: list[tuple[CurrentSource, str]],
) -> tuple[TopicGroup, ...]:
    overview_path = workspace.root / _repository_overview_path(repository_id)
    if not overview_path.is_file():
        return ()
    raw_topics = _frontmatter_fields(overview_path.read_text(encoding="utf-8")).get("topic_groups")
    if raw_topics is None:
        return ()
    try:
        decoded = json.loads(raw_topics)
        topics = tuple(TopicGroup.model_validate(item) for item in decoded)
        _validate_topic_groups(topics, {source.source_id for source, _ in links})
    except (TypeError, ValueError):
        return ()
    return topics


def _render_repository_overview(
    repository_id: str,
    links: list[tuple[CurrentSource, str]],
    topics: tuple[TopicGroup, ...] = (),
) -> str:
    repository_name = next(
        source.repository_name for source, _ in links if source.repository_name is not None
    )
    title = f"{repository_name} 项目总览"
    summary = f"{repository_name} 已整理 {len(links)} 份文档；从这里进入具体模块和设计页面。"
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        "type: entity",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        'tags: ["repository", "generated"]',
        "generated: repository_overview",
        f"repository_id: {repository_id}",
    ]
    if topics:
        encoded_topics = json.dumps(
            [topic.model_dump(mode="json") for topic in topics],
            ensure_ascii=False,
        )
        lines.append(f"topic_groups: {encoded_topics}")
    lines.extend(["---", "", f"# {title}", ""])
    links_by_source_id = {source.source_id: (source, path) for source, path in links}
    if topics:
        lines.extend(["## 主题导航", ""])
        for topic in sorted(topics, key=lambda item: item.title.lower()):
            lines.extend([f"### {topic.title}", "", topic.summary, ""])
            for source_id in topic.source_ids:
                source, page_path = links_by_source_id[source_id]
                lines.append(
                    f"- [{_escape_link_text(source.title)}]({page_path.rsplit('/', 1)[-1]})"
                    f" — {source.relative_path or source.title}"
                )
            lines.append("")
        lines.extend(["## 全部页面", ""])
    else:
        lines.extend(["## 页面导航", ""])
    for source, page_path in sorted(
        links,
        key=lambda item: ((item[0].relative_path or "").lower(), item[0].title.lower()),
    ):
        lines.append(
            f"- [{_escape_link_text(source.title)}]({page_path.rsplit('/', 1)[-1]})"
            f" — {source.relative_path or source.title}"
        )
    return "\n".join(lines) + "\n"


def _topic_messages(
    workspace: Workspace,
    links: list[tuple[CurrentSource, str]],
) -> tuple[dict[str, str], ...]:
    cards = []
    for source, page_path in links:
        content = (workspace.root / page_path).read_text(encoding="utf-8")
        summary = _parse_page_summary(page_path, content)
        if summary is None:
            raise ValueError(f"compiled source page has no usable summary: {page_path}")
        cards.append(
            "\n".join(
                [
                    f"SOURCE_ID: {source.source_id}",
                    f"PATH: {source.relative_path or source.title}",
                    f"TITLE: {summary.title}",
                    f"SUMMARY: {summary.summary}",
                ]
            )
        )
    return (
        {
            "role": "system",
            "content": (
                "Group the provided Wiki page cards into concise semantic navigation topics. "
                'Return JSON matching {"topics":[{"title":...,"summary":...,'
                '"source_ids":[...]}]}. Every provided SOURCE_ID must appear in exactly one '
                "topic. Use 3-8 topics when possible. Topic titles and summaries must be one line. "
                "Only describe the grouping; do not invent facts beyond the cards."
            ),
        },
        {"role": "user", "content": "PAGE CARDS:\n" + "\n\n---\n\n".join(cards)},
    )


def _validate_topic_groups(topics: tuple[TopicGroup, ...], source_ids: set[str]) -> None:
    if not topics:
        raise ValueError("provider returned no topic groups")
    covered: set[str] = set()
    for topic in topics:
        unknown = set(topic.source_ids) - source_ids
        if unknown:
            raise ValueError(
                "provider returned topic with unknown source: " + ", ".join(sorted(unknown))
            )
        duplicates = covered & set(topic.source_ids)
        if duplicates:
            raise ValueError(
                "provider assigned a source to multiple topics: " + ", ".join(sorted(duplicates))
            )
        covered.update(topic.source_ids)
    if covered != source_ids:
        raise ValueError("provider omitted sources from topic groups")


def _render_index(
    workspace: Workspace,
    candidate_files: dict[str, str],
    *,
    removed_paths: set[str] | None = None,
) -> str:
    return render_index(workspace, candidate_files, removed_paths=removed_paths)
