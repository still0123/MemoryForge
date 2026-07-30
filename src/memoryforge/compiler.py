"""Deterministic local compilation from current sources to readable Wiki pages."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal, cast

from memoryforge.changesets import StoredChangeSet
from memoryforge.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    PageChange,
    Sensitivity,
    TopicGroup,
    validate_llm_body,
    validate_llm_summary,
    validate_llm_title,
)
from memoryforge.provider import OpenAICompatibleProvider
from memoryforge.workspace import Workspace, list_git_checkouts

PageType = Literal["entity", "concept", "synthesis"]
_PAGE_TYPES: tuple[PageType, ...] = ("entity", "concept", "synthesis")
_CATEGORY_PAGE_TYPES: dict[str, PageType] = {
    "summary": "entity",
    "design": "concept",
    "notes": "concept",
    "refs": "concept",
    "postmortem": "synthesis",
}
_ENTITY_WORDS = (
    "repository",
    "repo",
    "service",
    "module",
    "protocol",
    "仓库",
    "服务",
    "模块",
    "协议",
)
_SYNTHESIS_WORDS = (
    "decision",
    "tradeoff",
    "comparison",
    "postmortem",
    "retro",
    "adr",
    "决策",
    "取舍",
    "对比",
    "复盘",
)
_FRONTMATTER = re.compile(r"\A---\n(?P<fields>.*?)\n---\n", re.DOTALL)
_INDEX_ENTRY = re.compile(
    r"- \[(?P<title>(?:\\.|[^\]])+)\]"
    r"\((?P<path>[^)]+)\) — (?P<summary>.+)"
)


@dataclass(frozen=True)
class CurrentSource:
    source_id: str
    source_version: int
    title: str
    category: str
    tags: tuple[str, ...]
    updated: str
    snapshot_path: str
    sensitivity: Sensitivity
    repository_id: str | None
    repository_name: str | None
    relative_path: str | None


@dataclass(frozen=True)
class PageSummary:
    path: str
    title: str
    page_type: PageType
    summary: str


@dataclass(frozen=True)
class Compilation:
    changeset: ChangeSet
    candidate_files: dict[str, str]


@dataclass(frozen=True)
class RoutedPage:
    """An existing page whose complete source group must stay together."""

    path: str
    source_ids: tuple[str, ...]


def compile_pending_sources(
    workspace: Workspace,
    *,
    source_ids: tuple[str, ...] = (),
    provider: OpenAICompatibleProvider | None = None,
) -> Compilation | None:
    """Compile pending local sources into a reviewable Wiki ChangeSet."""
    selected = set(source_ids)
    _require_known_sources(workspace, selected)
    pending = _load_pending_sources(workspace, selected)
    if not pending:
        return _compile_missing_repository_overviews(workspace) if not selected else None

    routed_pages = _routed_pages_for_pending_sources(workspace, pending)
    routed_source_ids = {
        source_id
        for routed_page in routed_pages
        for source_id in routed_page.source_ids
    }
    compilation_sources = _load_current_sources(
        workspace,
        {source.source_id for source in pending} | routed_source_ids,
    )
    loaded_source_ids = {source.source_id for source in compilation_sources}
    expected_source_ids = {source.source_id for source in pending} | routed_source_ids
    if loaded_source_ids != expected_source_ids:
        raise ValueError("recorded Wiki page ownership has no current source version")

    if provider is not None:
        local_only = [
            source.source_id
            for source in compilation_sources
            if source.sensitivity is Sensitivity.LOCAL_ONLY
        ]
        if local_only:
            raise ValueError(
                "LLM compilation cannot include local_only sources: "
                + ", ".join(local_only)
            )
        return _compile_with_provider(
            workspace,
            compilation_sources,
            provider,
            routed_pages=routed_pages,
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
            *(
                f"{topic.title}:{','.join(sorted(topic.source_ids))}"
                for topic in topics
            ),
        ]
    )
    return Compilation(
        changeset=ChangeSet(
            changeset_id="chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            base_commit=base_commit,
            status=ChangeSetStatus.PROPOSED,
            operations=(
                ChangeOperation(type=ChangeOperationType.UPDATE_PAGE, path=overview_path),
                ChangeOperation(type=ChangeOperationType.UPDATE_PAGE, path=index_path),
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
        source_id
        for routed_page in routed_pages
        for source_id in routed_page.source_ids
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
        operations.append(ChangeOperation(type=operation_type, path=path))
        if len(page_sources) == 1:
            source = page_sources[0]
            content = _read_source_text(workspace, source)
            if "code" in source.tags:
                candidate_files[path] = _render_code_page(source, content)
            else:
                quote, start = _first_meaningful_paragraph(content)
                candidate_files[path] = _render_page(
                    source,
                    quote,
                    f"chars:{start}-{start + len(quote)}",
                )
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
    page_identities.extend(sorted(candidate_files))
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
        ChangeOperation(type=ChangeOperationType.CREATE_PAGE, path=path)
        for path in sorted(overviews)
    ]
    operations.append(ChangeOperation(type=ChangeOperationType.UPDATE_PAGE, path=index_path))
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


def _compile_with_provider(
    workspace: Workspace,
    pending: list[CurrentSource],
    provider: OpenAICompatibleProvider,
    *,
    routed_pages: tuple[RoutedPage, ...] = (),
) -> Compilation:
    source_texts = {source.source_id: _read_source_text(workspace, source) for source in pending}
    messages = _llm_messages(pending, source_texts)
    changes = provider.compile_pages(messages)
    pending_by_id = {source.source_id: source for source in pending}
    _validate_llm_changes(
        workspace,
        changes,
        pending_by_id,
        source_texts,
        routed_pages=routed_pages,
    )

    operations: list[ChangeOperation] = []
    candidate_files: dict[str, str] = {}
    used_source_ids: list[str] = []
    for change in changes:
        page_sources = [pending_by_id[source_id] for source_id in change.source_ids]
        page_path = _target_page_path(change, routed_pages)
        if page_path in candidate_files:
            raise ValueError(f"provider returned duplicate page ownership: {page_path}")
        candidate_files[page_path] = _render_llm_page(
            change,
            page_sources,
            source_texts,
        )
        operation_type = (
            ChangeOperationType.UPDATE_PAGE
            if (workspace.root / page_path).is_file()
            else ChangeOperationType.CREATE_PAGE
        )
        operations.append(ChangeOperation(type=operation_type, path=page_path))
        used_source_ids.extend(change.source_ids)

    if not candidate_files:
        raise ValueError("provider returned no PageChange proposals")

    used_source_ids = list(dict.fromkeys(used_source_ids))
    used_sources = [pending_by_id[source_id] for source_id in used_source_ids]
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
        )
    )
    candidate_files[index_path] = _render_index(workspace, candidate_files)

    base_commit = workspace.current_commit()
    identity = "\n".join(
        [
            base_commit,
            "llm",
            *(f"{source.source_id}:{source.source_version}" for source in used_sources),
            *(
                f"{_target_page_path(change, routed_pages)}:{change.title}"
                for change in changes
            ),
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


def _llm_messages(
    pending: list[CurrentSource],
    source_texts: dict[str, str],
) -> tuple[dict[str, str], ...]:
    source_blocks = []
    for source in pending:
        source_blocks.append(
            f"SOURCE_ID: {source.source_id}\n"
            f"TITLE: {source.title}\n"
            f"CATEGORY: {source.category}\n"
            f"CONTENT:\n{source_texts[source.source_id]}"
        )
    return (
        {
            "role": "system",
            "content": (
                "You propose concise Markdown Wiki page changes. Return JSON with "
                "{\"changes\":[...]} matching the PageChange contract. Use only the "
                "provided pending source IDs. Paths must be wiki/pages/<filename>.md. "
                "Do not return frontmatter in body; the local compiler adds it. "
                "Every citation locator must be a character range in its source. "
                "Return no INDEX or raw file changes."
            ),
        },
        {
            "role": "user",
            "content": (
                "PENDING SOURCES:\n"
                + "\n\n---\n\n".join(source_blocks)
            ),
        },
    )


def _validate_llm_changes(
    workspace: Workspace,
    changes: tuple[PageChange, ...],
    pending: dict[str, CurrentSource],
    source_texts: dict[str, str],
    *,
    routed_pages: tuple[RoutedPage, ...] = (),
) -> None:
    pending_ids = set(pending)
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
        if not set(change.source_ids) <= pending_ids:
            raise ValueError(f"provider referenced a non-pending source: {change.path}")
        duplicate_source_ids = covered_source_ids & set(change.source_ids)
        if duplicate_source_ids:
            listed = ", ".join(sorted(duplicate_source_ids))
            raise ValueError(f"provider assigned a source to multiple Wiki pages: {listed}")
        covered_source_ids.update(change.source_ids)
        if not {citation.source_id for citation in change.citations} <= pending_ids:
            raise ValueError(
                f"provider returned a citation for a non-pending source: {change.path}"
            )
        for citation in change.citations:
            source_text = source_texts[citation.source_id]
            start, end = _locator_range(citation.locator)
            if end > len(source_text):
                raise ValueError(f"provider returned an out-of-range citation: {citation.locator}")
        _validate_page_ownership(
            workspace,
            change,
            _target_page_path(change, routed_pages),
        )
    for routed_page in routed_pages:
        if not any(
            set(change.source_ids) == set(routed_page.source_ids) for change in changes
        ):
            raise ValueError(
                "provider must update each routed Wiki page with exactly its existing "
                f"sources: {routed_page.path}"
            )
    if covered_source_ids != pending_ids:
        omitted = ", ".join(sorted(pending_ids - covered_source_ids))
        raise ValueError(f"provider omitted pending sources: {omitted}")


def _validate_page_ownership(
    workspace: Workspace,
    change: PageChange,
    canonical_path: str | None = None,
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
        raise ValueError(
            f"existing Wiki page has invalid sources ownership: {path}"
        ) from exc
    if not isinstance(decoded, list) or not all(
        isinstance(source_id, str) for source_id in decoded
    ):
        raise ValueError(f"existing Wiki page has invalid sources ownership: {path}")
    if set(decoded) != set(change.source_ids):
        raise ValueError(
            f"provider cannot update Wiki page owned by different sources: {path}"
        )


def _locator_range(locator: str) -> tuple[int, int]:
    match = re.fullmatch(r"chars:(\d+)-(\d+)", locator)
    if match is None:
        raise ValueError(f"invalid citation locator: {locator}")
    return int(match.group(1)), int(match.group(2))


def _render_llm_page(
    change: PageChange,
    sources: list[CurrentSource],
    source_texts: dict[str, str],
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
        change.body.rstrip(),
        "",
        "## Verified facts",
        "",
    ]
    for index, citation in enumerate(change.citations):
        footnote = f"source-{index + 1}-{citation.source_id[:8]}"
        quote = _citation_excerpt(citation.locator, citation.source_id, source_texts)
        lines.append(f"- {quote} [^{footnote}]")
    lines.extend([
        "",
        "## Sources",
        "",
    ])
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
    return _load_current_sources(workspace, source_ids, pending_only=True)


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
                    str(row["repository_id"])
                    if row["repository_id"] is not None
                    else None
                ),
                repository_name=(
                    str(row["repository_name"])
                    if row["repository_name"] is not None
                    else None
                ),
                relative_path=(
                    str(row["relative_path"])
                    if row["relative_path"] is not None
                    else None
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
            raise ValueError(
                "source belongs to multiple Wiki pages: " + source.source_id
            )
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


def _tags_from_json(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(tag for tag in decoded if isinstance(tag, str))


def _read_source_text(workspace: Workspace, source: CurrentSource) -> str:
    return (workspace.root / source.snapshot_path).read_text(encoding="utf-8")


def _first_meaningful_paragraph(content: str) -> tuple[str, int]:
    for match in re.finditer(
        r"(?:\A|\n[ \t]*\n)(?P<paragraph>.*?)(?=\n[ \t]*\n|\Z)",
        content,
        re.DOTALL,
    ):
        paragraph = match.group("paragraph")
        leading = len(paragraph) - len(paragraph.lstrip())
        quote = paragraph.strip()
        if quote and not quote.startswith("#"):
            return quote, match.start("paragraph") + leading
    for line in content.splitlines():
        candidate = line.lstrip("#").strip()
        if candidate:
            return candidate, content.index(candidate)
    raise ValueError("source contains no meaningful text")


def _page_type(source: CurrentSource) -> PageType:
    normalized_title = source.title.lower()
    if any(word in normalized_title for word in _SYNTHESIS_WORDS):
        return "synthesis"
    if any(word in normalized_title for word in _ENTITY_WORDS):
        return "entity"
    return _CATEGORY_PAGE_TYPES.get(source.category, "concept")


def _wiki_path(source: CurrentSource) -> str:
    return f"wiki/pages/{source.source_id}.md"


def _canonical_page_path(source_ids: tuple[str, ...]) -> str:
    """Return the stable physical page path for a source ownership set."""
    ordered = tuple(sorted(source_ids))
    if len(ordered) == 1:
        filename = f"{ordered[0]}.md"
    else:
        prefixes = "-".join(source_id[:8] for source_id in ordered)
        filename = f"merged-{prefixes}.md"
    return f"wiki/pages/{filename}"


def _target_page_path(
    change: PageChange,
    routed_pages: tuple[RoutedPage, ...],
) -> str:
    for routed_page in routed_pages:
        if set(change.source_ids) == set(routed_page.source_ids):
            return routed_page.path
    return _canonical_page_path(change.source_ids)


def _render_page(source: CurrentSource, quote: str, locator: str) -> str:
    footnote = f"source-{source.source_id[:8]}"
    displayed_quote = " ".join(quote.splitlines())
    tags = tuple(dict.fromkeys((source.category, *source.tags)))
    return (
        "---\n"
        f"title: {json.dumps(source.title, ensure_ascii=False)}\n"
        f"type: {_page_type(source)}\n"
        f"summary: {json.dumps(displayed_quote, ensure_ascii=False)}\n"
        f"tags: {json.dumps(tags, ensure_ascii=False)}\n"
        f"sources: {json.dumps((source.source_id,), ensure_ascii=False)}\n"
        f"source_version: {source.source_version}\n"
        f"updated: {source.updated}\n"
        "---\n\n"
        f"# {source.title}\n\n"
        "## Verified facts\n\n"
        f"- {displayed_quote} [^{footnote}]\n\n"
        f"[^{footnote}]: source `{source.source_id}` · revision `{source.source_version}` · "
        f"`{locator}`\n"
    )


def _render_code_page(source: CurrentSource, content: str) -> str:
    """Render a small, citable outline without pretending to fully understand code."""
    language = "Go" if "go" in source.tags else "Python"
    facts = _code_facts(content, language)
    symbols = [quote for quote, _ in facts[1:]]
    summary = f"{language} code"
    if facts:
        summary += f": {facts[0][0]}"
    if symbols:
        summary += "; exports " + ", ".join(symbols[:6])
    tags = tuple(dict.fromkeys((source.category, *source.tags)))
    lines = [
        "---",
        f"title: {json.dumps(source.title, ensure_ascii=False)}",
        "type: concept",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"sources: {json.dumps((source.source_id,), ensure_ascii=False)}",
        f"source_version: {source.source_version}",
        f"updated: {source.updated}",
        "---",
        "",
        f"# {source.title}",
        "",
        "## Code outline",
        "",
        f"- Language: {language}",
        f"- File: `{source.relative_path or source.title}`",
        "",
        "## Verified facts",
        "",
    ]
    for index, (quote, _) in enumerate(facts, start=1):
        lines.append(f"- {quote} [^source-{index}]")
    lines.append("")
    for index, (quote, start) in enumerate(facts, start=1):
        lines.append(
            f"[^source-{index}]: source `{source.source_id}` · revision "
            f"`{source.source_version}` · `chars:{start}-{start + len(quote)}`"
        )
    return "\n".join(lines) + "\n"


def _code_facts(content: str, language: str) -> list[tuple[str, int]]:
    patterns = (
        (r"^package\s+[A-Za-z_]\w*\s*$", r"^(?:type\s+[A-Z]\w*|func\s+(?:\([^)]*\)\s*)?[A-Z]\w*)")
        if language == "Go"
        else (r"^(?:class|def)\s+[A-Za-z]\w*",)
    )
    facts: list[tuple[str, int]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, content, re.MULTILINE):
            quote = match.group().strip()
            if quote:
                leading = len(match.group()) - len(match.group().lstrip())
                facts.append((quote, match.start() + leading))
    if facts:
        return facts[:8]
    for match in re.finditer(r"^.+$", content, re.MULTILINE):
        quote = match.group().strip()
        if quote:
            return [(quote, match.start() + len(match.group()) - len(match.group().lstrip()))]
    raise ValueError("source contains no meaningful code")


def _render_deterministic_group_page(
    workspace: Workspace,
    sources: list[CurrentSource],
) -> str:
    """Use source excerpts as the local fallback for a previously merged Wiki page."""
    excerpts: list[tuple[CurrentSource, str, str]] = []
    for source in sources:
        quote, start = _first_meaningful_paragraph(_read_source_text(workspace, source))
        excerpts.append(
            (
                source,
                " ".join(quote.splitlines()),
                f"chars:{start}-{start + len(quote)}",
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
        source.repository_id
        for source in changed_sources
        if source.repository_id is not None
    }
    if not changed_repositories:
        return {}

    candidate_paths = {
        source.source_id: page_path
        for page_path, sources in page_groups
        for source in sources
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
    raw_topics = _frontmatter_fields(overview_path.read_text(encoding="utf-8")).get(
        "topic_groups"
    )
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
                "Return JSON matching {\"topics\":[{\"title\":...,\"summary\":...,"
                "\"source_ids\":[...]}]}. Every provided SOURCE_ID must appear in exactly one "
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


def _render_index(workspace: Workspace, candidate_files: dict[str, str]) -> str:
    index = workspace.wiki_dir / "INDEX.md"
    existing = _index_summaries(index.read_text(encoding="utf-8") if index.is_file() else "")
    changed = [
        summary
        for path, content in candidate_files.items()
        if path != "wiki/INDEX.md"
        if (summary := _parse_page_summary(path, content)) is not None
    ]
    changed_paths = {page.path for page in changed}
    pages = sorted(
        [page for page in existing if page.path not in changed_paths] + changed,
        key=lambda page: (page.page_type, page.title.lower(), page.path),
    )
    sections = {
        "entity": "Entities",
        "concept": "Concepts",
        "synthesis": "Synthesis",
    }
    lines = [
        "# Knowledge Index",
        "",
        "Use this page to find the relevant Wiki page before reading it.",
    ]
    for page_type in _PAGE_TYPES:
        typed_pages = [page for page in pages if page.page_type == page_type]
        if not typed_pages:
            continue
        lines.extend(["", f"## {sections[page_type]}", ""])
        for page in typed_pages:
            link = page.path.removeprefix("wiki/")
            lines.append(f"- [{_escape_link_text(page.title)}]({link}) — {page.summary}")
    return "\n".join(lines) + "\n"


def _index_summaries(content: str) -> list[PageSummary]:
    section_types = {
        "Entities": "entity",
        "Concepts": "concept",
        "Synthesis": "synthesis",
    }
    page_type: PageType | None = None
    pages: list[PageSummary] = []
    for line in content.splitlines():
        if line.startswith("## "):
            named_type = section_types.get(line.removeprefix("## "))
            page_type = cast(PageType, named_type) if named_type is not None else None
            continue
        match = _INDEX_ENTRY.fullmatch(line)
        if match is None or page_type is None:
            continue
        pages.append(
            PageSummary(
                path=f"wiki/{match.group('path')}",
                title=_unescape_link_text(match.group("title")),
                page_type=page_type,
                summary=match.group("summary"),
            )
        )
    return pages


def _parse_page_summary(path: str, content: str) -> PageSummary | None:
    match = _FRONTMATTER.match(content)
    if match is None:
        return None
    fields = _frontmatter_fields(content)
    page_type = fields.get("type")
    if page_type not in _PAGE_TYPES:
        return None
    title = _frontmatter_text(fields.get("title", ""))
    summary = _frontmatter_text(fields.get("summary", ""))
    if not title or not summary:
        return None
    return PageSummary(path=path, title=title, page_type=cast(PageType, page_type), summary=summary)


def _frontmatter_fields(content: str) -> dict[str, str]:
    match = _FRONTMATTER.match(content)
    if match is None:
        return {}
    fields: dict[str, str] = {}
    for line in match.group("fields").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def _escape_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _unescape_link_text(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def _frontmatter_text(value: str) -> str:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decoded if isinstance(decoded, str) else value
