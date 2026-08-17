"""Protocol-agnostic Agent Access functions (progressive-recall spec).

These are the shared business functions that both the CLI and the MCP stdio
Server call. They return structured business statuses (§14) so every AI Host
gets the same bounded, applied, traceable knowledge access:

* ``resolve_repository_scope`` — bind a project path to one registered Git
  checkout, failing closed with :class:`UnmappedProjectError` when the project
  is not registered;
* ``query_context`` — L2 bounded context (≤ 3 pages, ≤ 6 citations, ≤ 8,000
  output characters) through the shared ``answer_question`` engine with the
  server-fixed repository and sensitivity filter applied before Support
  scoring;
* ``read_applied_evidence`` — L3 one-citation excerpt (≤ 2,000 characters)
  verified against applied Wiki Facts;
* ``recall_context`` — recent applied conversation memory, extracted from the
  CLI ``recall`` command so CLI and MCP share one implementation;
* ``propose_grounded_update`` — stage exactly one page's PROPOSED ChangeSet
  from an evidence-backed conclusion (origin ``AGENT_PROPOSAL``); it never
  reviews, approves, applies, or rewrites the stable Wiki;
* ``list_changesets`` / ``review_changeset`` — read-only ChangeSet previews
  that never record a human review receipt.

No new query engine is introduced here; ``answer_question`` remains the single
retrieval/support implementation, and page rendering reuses the compiler's
existing PageChange pipeline.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Literal, cast

from memoryforge.automation.automation_validation import classify_risk
from memoryforge.compiler.compiler import (
    _add_relations_page,
    _load_current_sources,
    _render_llm_page,
)
from memoryforge.compiler.index_rendering import _parse_page_summary
from memoryforge.compiler.index_rendering import render_index as _render_index
from memoryforge.compiler.source_rendering import _read_source_text
from memoryforge.compiler.wiki_facts import (
    CitationPayload,
    is_conversation_process_note,
    parse_page_citations,
)
from memoryforge.core.errors import MemoryForgeError, UnmappedProjectError
from memoryforge.core.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeOrigin,
    ChangeSet,
    ChangeSetStatus,
    GitRepositoryRecord,
    PageChange,
    PageCitation,
    RiskLevel,
    Sensitivity,
)
from memoryforge.query.context_map import (
    MAP_MAX_CHARACTERS,
    build_context_map,
    visible_context_page_paths,
)
from memoryforge.query.contracts import SupportPayload
from memoryforge.query.query import (
    DEFAULT_QUERY_MAX_CITATIONS,
    DEFAULT_QUERY_MAX_PAGES,
    answer_question,
)
from memoryforge.query.route_rules import is_global_question
from memoryforge.query.support import answer_is_supported
from memoryforge.storage.changesets import ChangeSetStore, StoredChangeSet
from memoryforge.storage.database import connect_readonly as _connect_readonly
from memoryforge.storage.errors import WorkspaceIntegrityError, WorkspaceSecurityError
from memoryforge.storage.workspace import (
    Workspace,
    _git_repository_record,
    is_applied_source_version,
    is_public_source_version,
    list_git_checkouts,
    read_source_excerpt,
)

# L2/L3 hard budgets (§7.1); counted in characters, never in tokens.
_CONTEXT_MAX_OUTPUT_CHARACTERS = 8000
_EVIDENCE_MAX_CHARACTERS = 2000
_STARTUP_CONTEXT_MAX_CHARACTERS = 4000

# Phase 4 bounded review preview (§12.2).
_REVIEW_MAX_DIFF_CHARACTERS = 4000
_REVIEW_MAX_CITATIONS = 6

_OPEN_FAILURES = (
    MemoryForgeError,
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    OSError,
    sqlite3.Error,
)

_TITLE_LINE = re.compile(r"^title: (?P<title>.+)$", re.MULTILINE)

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

_RECALL_WARNING = "Conversation memories are unverified; check citations before relying on them."

QueryIntent = Literal["current_code", "project_memory", "mixed"]

_CURRENT_CODE_MARKERS = (
    "调用",
    "依赖",
    "导入",
    "代码",
    "报错",
    "错误",
    "堆栈",
    "哪一行",
    "文件位置",
    "call",
    "calls",
    "dependency",
    "stack trace",
)
_PROJECT_MEMORY_MARKERS = (
    "为什么",
    "历史",
    "之前",
    "曾经",
    "当时",
    "决策",
    "决定",
    "原因",
    "背景",
    "演进",
    "踩坑",
    "约定",
    "why",
    "history",
    "decision",
    "previously",
    "rationale",
)


def classify_query_intent(question: str) -> QueryIntent:
    """Choose the cheapest trustworthy source for a project question."""
    lowered = question.casefold()
    current_code = any(marker in lowered for marker in _CURRENT_CODE_MARKERS)
    project_memory = any(marker in lowered for marker in _PROJECT_MEMORY_MARKERS)
    if current_code and project_memory:
        return "mixed"
    if current_code:
        return "current_code"
    return "project_memory"


def _answer_strategy(question: str, evidence_status: str) -> dict[str, object]:
    intent = classify_query_intent(question)
    if evidence_status == "grounded":
        action = "answer_from_memory"
        verification_required = False
    elif intent in ("current_code", "mixed"):
        action = (
            "verify_unsupported_aspects_only"
            if evidence_status == "partial"
            else "inspect_current_code"
        )
        verification_required = True
    else:
        action = "report_evidence_boundary"
        verification_required = False
    return {
        "query_intent": intent,
        "recommended_action": action,
        "source_verification_required": verification_required,
    }


def _response_mode(evidence_status: str) -> str:
    if evidence_status == "grounded":
        return "answer_from_project_evidence"
    if evidence_status == "partial":
        return "answer_with_evidence_boundary"
    return "general_guidance_only"


def _verification_status(citations: list[dict[str, object]]) -> str:
    """Separate reviewed project evidence from explicitly unverified history."""
    if not citations:
        return "no_evidence"
    unverified_markers = ("assistant conclusions", "conversation notes (unverified)")
    unverified = [
        any(marker in str(citation.get("section", "")).casefold() for marker in unverified_markers)
        for citation in citations
    ]
    if all(unverified):
        return "unverified_history"
    if any(unverified):
        return "mixed_evidence"
    return "reviewed_project_evidence"


def resolve_repository_scope(workspace: Path, project_path: Path) -> GitRepositoryRecord:
    """Bind ``project_path`` to the registered Git checkout it lives under.

    Rules (§8): the project path must exist and resolve to a real directory;
    it must equal a ``checkout_path`` or sit inside one; multiple ancestor
    matches pick the longest path; no match raises
    :class:`UnmappedProjectError` (never a degraded whole-Workspace query).
    """
    try:
        project_root = project_path.resolve(strict=True)
    except OSError:
        raise ValueError("project path must be an existing directory") from None
    if not project_root.is_dir():
        raise ValueError("project path must be an existing directory")
    checkouts = list_git_checkouts(workspace)
    candidates = [
        record for record in checkouts if _path_within(project_root, Path(record.checkout_path))
    ]
    if not candidates:
        raise UnmappedProjectError(
            f"project path {project_root} is not inside any registered Git checkout"
        )
    return max(
        candidates,
        key=lambda record: len(Path(record.checkout_path).resolve(strict=False).parts),
    )


def _named_repository_scope(workspace: Path, question: str) -> GitRepositoryRecord | None:
    """Strictly scope a Workspace query only when one full repository name is explicit."""
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        rows = connection.execute(
            "SELECT * FROM git_repositories ORDER BY registered_at, repository_id"
        ).fetchall()
    records = tuple(_git_repository_record(row) for row in rows)
    matches = [
        repository
        for repository in records
        if re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(repository.name)}(?![A-Za-z0-9_-])",
            question,
            re.IGNORECASE,
        )
    ]
    return matches[0] if len(matches) == 1 else None


def server_name(workspace: Path, project_root: Path) -> str:
    """Return the stable, unique MCP server name for one binding (§8).

    ``memoryforge-<project-slug>-<first8(sha256(canonical_workspace + "\\0"
    + canonical_project_root))>``. The same Workspace + project always yields
    the same name; a different binding yields a different name, so Host
    configurations can never silently overwrite each other.
    """
    canonical_workspace = str(workspace.resolve(strict=False))
    canonical_project = str(project_root.resolve(strict=False))
    digest = hashlib.sha256(f"{canonical_workspace}\0{canonical_project}".encode()).hexdigest()[:8]
    slug = re.sub(r"[^a-z0-9]+", "-", Path(canonical_project).name.lower()).strip("-")
    if not slug:
        slug = "project"
    return f"memoryforge-{slug}-{digest}"


def query_context(
    workspace: Path,
    project_root: Path,
    question: str,
    *,
    allow_local: bool = False,
    max_pages: int = DEFAULT_QUERY_MAX_PAGES,
    max_citations: int = DEFAULT_QUERY_MAX_CITATIONS,
    page_paths: list[str] | None = None,
) -> dict[str, object]:
    """Return bounded L2 context for one question from the bound project.

    ``project_root`` and the resulting ``repository_id`` are fixed by the
    connecting Host (never by the caller); ``allow_local`` is the server-fixed
    authorization. The sensitivity gate runs before Support scoring and answer
    assembly, so ``Sensitivity.LOCAL_ONLY`` facts can never influence page
    selection, Support or the answer hint unless explicitly authorized.
    """
    max_pages = _clamp_int(max_pages, 1, DEFAULT_QUERY_MAX_PAGES)
    max_citations = _clamp_int(max_citations, 1, DEFAULT_QUERY_MAX_CITATIONS)
    try:
        scope = resolve_repository_scope(workspace, project_root)
        opened = Workspace.open_readonly(workspace)
    except (UnmappedProjectError, ValueError):
        return {"status": "unmapped_project"}
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}
    return _query_context(
        opened,
        question,
        repository_id=scope.repository_id,
        preferred_repository_id=None,
        scope=scope,
        allow_local=allow_local,
        max_pages=max_pages,
        max_citations=max_citations,
        page_paths=page_paths,
    )


def query_workspace_context(
    workspace: Path,
    question: str,
    *,
    preferred_project_root: Path | None = None,
    allow_local: bool = False,
    max_pages: int = DEFAULT_QUERY_MAX_PAGES,
    max_citations: int = DEFAULT_QUERY_MAX_CITATIONS,
    page_paths: list[str] | None = None,
) -> dict[str, object]:
    """Return bounded context from the whole applied Workspace.

    ``preferred_project_root`` is optional ranking context only. It boosts
    pages from that registered checkout but never excludes other applied
    repositories, so a new conversation and cross-repository question work
    without a current project.
    """
    max_pages = _clamp_int(max_pages, 1, DEFAULT_QUERY_MAX_PAGES)
    max_citations = _clamp_int(max_citations, 1, DEFAULT_QUERY_MAX_CITATIONS)
    try:
        opened = Workspace.open_readonly(workspace)
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}
    named_scope = _named_repository_scope(opened.root, question)
    preferred_scope: GitRepositoryRecord | None = None
    if preferred_project_root is not None:
        try:
            preferred_scope = resolve_repository_scope(opened.root, preferred_project_root)
        except (UnmappedProjectError, ValueError):
            pass
        except _OPEN_FAILURES:
            return {"status": "workspace_unavailable"}
    return _query_context(
        opened,
        question,
        repository_id=named_scope.repository_id if named_scope is not None else None,
        preferred_repository_id=(
            preferred_scope.repository_id
            if named_scope is None and preferred_scope is not None
            else None
        ),
        scope=named_scope,
        allow_local=allow_local,
        max_pages=max_pages,
        max_citations=max_citations,
        preferred_scope=preferred_scope if named_scope is None else None,
        page_paths=page_paths,
    )


def _query_context(
    opened: Workspace,
    question: str,
    *,
    repository_id: str | None,
    preferred_repository_id: str | None,
    scope: GitRepositoryRecord | None,
    allow_local: bool,
    max_pages: int,
    max_citations: int,
    preferred_scope: GitRepositoryRecord | None = None,
    page_paths: list[str] | None = None,
) -> dict[str, object]:
    try:
        workspace_commit = opened.current_commit()
        requested_page_paths: tuple[str, ...] | None = None
        if page_paths is not None:
            requested_page_paths = tuple(dict.fromkeys(page_paths))
            if not requested_page_paths:
                return {"status": "invalid_page_paths"}
            visible_paths = visible_context_page_paths(
                opened.root,
                repository_id=repository_id,
                allow_local=allow_local,
            )
            if any(path not in visible_paths for path in requested_page_paths):
                return {"status": "invalid_page_paths"}
        if requested_page_paths is None and is_global_question(question):
            navigation = build_context_map(
                opened.root,
                repository_id=repository_id,
                allow_local=allow_local,
            )
            map_payload: dict[str, object] = {
                "status": "ok",
                "mode": "map",
                "navigation_only": True,
                "workspace_commit": workspace_commit,
                "map": navigation["entries"],
                "guidance": (
                    "Choose relevant page_path values, then call memoryforge_context "
                    "again with page_paths. Map entries are navigation only, not evidence."
                ),
                "budget": {
                    "max_output_characters": MAP_MAX_CHARACTERS,
                    "map_characters": navigation["characters"],
                    "output_characters": 0,
                    "truncated": navigation["truncated"],
                },
            }
            _set_context_scope(
                map_payload,
                scope=scope,
                preferred_scope=preferred_scope,
            )
            entries = cast(list[dict[str, object]], map_payload["map"])
            budget = cast(dict[str, object], map_payload["budget"])
            budget["map_characters"] = len(json.dumps(entries, ensure_ascii=False))

            def serialized_map_size() -> int:
                size = len(json.dumps(map_payload, ensure_ascii=False))
                for _ in range(3):
                    budget["output_characters"] = size
                    updated = len(json.dumps(map_payload, ensure_ascii=False))
                    if updated == size:
                        break
                    size = updated
                return size

            output_characters = serialized_map_size()
            while output_characters > MAP_MAX_CHARACTERS and entries:
                entries.pop()
                budget["map_characters"] = len(json.dumps(entries, ensure_ascii=False))
                budget["truncated"] = True
                output_characters = serialized_map_size()
            if output_characters > MAP_MAX_CHARACTERS:
                raise ValueError("map response envelope exceeds its hard character budget")
            return map_payload
        result = answer_question(
            opened.root,
            question,
            provider=None,
            verify=False,
            max_pages=max_pages,
            max_citations=max_citations,
            allow_local=allow_local,
            public_only=not allow_local,
            repository_id=repository_id,
            preferred_repository_id=preferred_repository_id,
            page_paths=requested_page_paths,
        )
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}
    evidence_status = str(result.get("evidence_status", "no_local_evidence"))
    answer_hint = "" if evidence_status == "no_local_evidence" else str(result["answer"])
    answer_strategy = _answer_strategy(question, evidence_status)
    wiki_pages = list(result["wiki_pages"])
    wiki_page_details = [_page_entry(opened.root, page_path) for page_path in wiki_pages]
    citation_metadata = _citation_metadata(opened.root, result["citations"])
    citations = [
        {
            "source_id": citation["source_id"],
            "source_version": citation["source_version"],
            "locator": citation["locator"],
            "quote": citation["quote"],
            "wiki_page": citation_metadata.get(
                (citation["source_id"], citation["source_version"], citation["locator"]),
                ("", f"source {citation['source_id'][:12]}"),
            )[0],
            "section": str(citation.get("section_path", "")),
            "display_source": citation_metadata.get(
                (citation["source_id"], citation["source_version"], citation["locator"]),
                ("", f"source {citation['source_id'][:12]}"),
            )[1],
        }
        for citation in result["citations"]
    ]
    support: SupportPayload | dict[str, object] = result.get("support") or {}
    supported_claims = list(result.get("supported_claims", []))
    unsupported_aspects = list(result.get("unsupported_aspects", []))
    payload: dict[str, object] = {
        "status": "ok",
        "evidence_status": evidence_status,
        "verification_status": _verification_status(citations),
        "response_mode": _response_mode(evidence_status),
        "workspace_commit": workspace_commit,
        "project_answer": answer_hint,
        "answer_hint": answer_hint,
        "supported_claims": supported_claims,
        "unsupported_aspects": unsupported_aspects,
        "answer_strategy": answer_strategy,
        "wiki_pages": wiki_pages,
        "wiki_page_details": wiki_page_details,
        "citations": citations,
        "support": support,
        "budget": {
            "max_pages": max_pages,
            "max_citations": max_citations,
            "max_output_characters": _CONTEXT_MAX_OUTPUT_CHARACTERS,
            "output_characters": 0,
            "truncated": False,
        },
    }
    _set_context_scope(payload, scope=scope, preferred_scope=preferred_scope)

    budget = cast(dict[str, object], payload["budget"])

    def serialized_size() -> int:
        size = len(json.dumps(payload, ensure_ascii=False))
        for _ in range(3):
            budget["output_characters"] = size
            updated = len(json.dumps(payload, ensure_ascii=False))
            if updated == size:
                break
            size = updated
        return size

    output_characters = serialized_size()
    if output_characters > _CONTEXT_MAX_OUTPUT_CHARACTERS:
        budget["truncated"] = True
    for _ in range(32):
        if output_characters <= _CONTEXT_MAX_OUTPUT_CHARACTERS:
            break
        if supported_claims:
            supported_claims.clear()
        elif support:
            support = {}
            payload["support"] = support
        elif answer_hint:
            excess = output_characters - _CONTEXT_MAX_OUTPUT_CHARACTERS
            answer_limit = max(0, len(answer_hint) - max(1, (excess + 1) // 2))
            answer_hint = _truncate_text(answer_hint, answer_limit) if answer_limit else ""
            payload["project_answer"] = answer_hint
            payload["answer_hint"] = answer_hint
        elif citations:
            citations.pop()
            cited_pages = {citation["wiki_page"] for citation in citations}
            wiki_pages[:] = [page for page in wiki_pages if page in cited_pages]
            wiki_page_details[:] = [
                page for page in wiki_page_details if page["path"] in cited_pages
            ]
            payload["verification_status"] = _verification_status(citations)
        elif wiki_pages:
            removed = wiki_pages.pop()
            wiki_page_details[:] = [page for page in wiki_page_details if page["path"] != removed]
        elif unsupported_aspects:
            unsupported_aspects.clear()
        else:
            raise ValueError("context response envelope exceeds its hard character budget")
        output_characters = serialized_size()
    else:
        raise ValueError(
            "context response could not be reduced to its hard character budget: "
            f"size={output_characters}, answer={len(answer_hint)}, "
            f"claims={len(supported_claims)}, citations={len(citations)}, "
            f"pages={len(wiki_pages)}, unsupported={len(unsupported_aspects)}"
        )
    budget["output_characters"] = output_characters
    return payload


def _set_context_scope(
    payload: dict[str, object],
    *,
    scope: GitRepositoryRecord | None,
    preferred_scope: GitRepositoryRecord | None,
) -> None:
    if scope is not None:
        payload["repository"] = {
            "repository_id": scope.repository_id,
            "name": scope.name,
        }
        return
    payload["scope"] = {
        "mode": "workspace",
        "preferred_repository": (
            {
                "repository_id": preferred_scope.repository_id,
                "name": preferred_scope.name,
            }
            if preferred_scope is not None
            else None
        ),
    }


def read_applied_evidence(
    workspace: Path,
    project_root: Path,
    *,
    source_id: str,
    source_version: int,
    locator: str,
    allow_local: bool = False,
    max_characters: int = 2000,
) -> dict[str, object]:
    """Read one applied Citation excerpt (§7.4, L3) with a 2,000-char cap.

    Verification order: the Citation must exist as a Wiki Fact, the Source
    Version must be applied, and the same sensitivity gate as the connection
    must pass. Failures return ``citation_not_found``, ``not_applied`` or
    ``local_scope_denied`` — never absolute blob paths.
    """
    max_characters = _clamp_int(max_characters, 1, _EVIDENCE_MAX_CHARACTERS)
    try:
        resolve_repository_scope(workspace, project_root)
    except (UnmappedProjectError, ValueError):
        return {"status": "unmapped_project"}
    return _read_applied_evidence(
        workspace,
        source_id=source_id,
        source_version=source_version,
        locator=locator,
        allow_local=allow_local,
        max_characters=max_characters,
    )


def read_workspace_evidence(
    workspace: Path,
    *,
    source_id: str,
    source_version: int,
    locator: str,
    allow_local: bool = False,
    max_characters: int = 2000,
) -> dict[str, object]:
    """Read one applied citation from any visible Workspace source."""
    return _read_applied_evidence(
        workspace,
        source_id=source_id,
        source_version=source_version,
        locator=locator,
        allow_local=allow_local,
        max_characters=_clamp_int(max_characters, 1, _EVIDENCE_MAX_CHARACTERS),
    )


def _read_applied_evidence(
    workspace: Path,
    *,
    source_id: str,
    source_version: int,
    locator: str,
    allow_local: bool,
    max_characters: int,
) -> dict[str, object]:
    try:
        if not _is_wiki_fact(workspace, source_id, source_version, locator):
            return {"status": "citation_not_found"}
        if not is_applied_source_version(
            workspace,
            source_id=source_id,
            source_version=source_version,
        ):
            return {"status": "not_applied"}
        if not allow_local and not is_public_source_version(
            workspace,
            source_id=source_id,
            source_version=source_version,
        ):
            return {"status": "local_scope_denied"}
        text = read_source_excerpt(
            workspace,
            source_id=source_id,
            source_version=source_version,
            locator=locator,
        )
    except _OPEN_FAILURES:
        return {"status": "citation_not_found"}
    truncated = len(text) > max_characters
    if truncated:
        text = text[:max_characters]
    return {
        "status": "read",
        "display_source": _display_source_label(workspace, source_id, source_version),
        "locator": locator,
        "text": text,
        "characters": len(text),
        "truncated": truncated,
    }


def recall_context(
    workspace: Path,
    *,
    limit: int = 3,
    repository_id: str | None = None,
    public_only: bool = False,
    startup_context_limit: int | None = None,
) -> dict[str, object]:
    """Return compact, cited startup context from applied conversation memories.

    Extracted from the CLI ``recall`` command; the CLI passes no extra filters
    so its JSON output is unchanged. MCP callers may restrict the result to
    one bound repository and/or public sources and cap ``startup_context``.
    """
    limit = _clamp_int(limit, 1, 20)
    opened = Workspace.open_readonly(workspace)
    filters = ["versions.tags_json LIKE '%\"conversation\"%'"]
    parameters: list[object] = []
    if repository_id is not None:
        filters.append("facts.repository_id = ?")
        parameters.append(repository_id)
    if public_only:
        filters.append("versions.sensitivity = ?")
        parameters.append(Sensitivity.PUBLIC.value)
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
            WHERE {filters}
            ORDER BY
                versions.observed_at DESC,
                facts.page_path,
                facts.rowid
            """.format(filters=" AND ".join(filters)),
            tuple(parameters),
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
    startup_context = _render_recall_context(summary, decisions, open_items, notes)
    if startup_context_limit is not None and len(startup_context) > startup_context_limit:
        cutoff = startup_context[:startup_context_limit]
        boundary = cutoff.rfind("\n")
        prefix = cutoff[:boundary] if boundary >= 0 else cutoff[:-1]
        startup_context = f"{prefix}… (truncated)"
    return {
        "status": "recalled" if notes else "empty",
        "warning": _RECALL_WARNING,
        "summary": summary,
        "recent_memories": notes,
        "decisions": decisions,
        "open_items": open_items,
        "startup_context": startup_context,
    }


def propose_grounded_update(
    workspace: Path,
    project_root: Path,
    *,
    question: str,
    conclusion: str,
    citations: list[CitationPayload],
    target_page: str,
    allow_local: bool = False,
) -> dict[str, object]:
    """Stage exactly one page's PROPOSED ChangeSet from a grounded conclusion.

    Rules (§12.1): at least one Citation; every Citation must be an applied,
    current Wiki Fact under the bound project; ``answer_is_supported`` must
    pass; ``target_page`` must be the applied page the Citations belong to;
    origin is fixed to ``AGENT_PROPOSAL``; no review, approve, or apply is
    ever called, and no ChangeSet is created without sufficient evidence.
    """
    if not citations or not conclusion.strip():
        return {"status": "insufficient_evidence"}
    if not question.strip():
        return {"status": "insufficient_evidence"}
    try:
        scope = resolve_repository_scope(workspace, project_root)
        opened = Workspace.open(workspace)
    except (UnmappedProjectError, ValueError):
        return {"status": "unmapped_project"}
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}
    try:
        loaded = _load_citation_quotes(opened.root, citations)
        if loaded is None:
            return {"status": "citation_not_found"}
        quotes, page_paths = loaded
        if not all(
            is_applied_source_version(
                opened.root,
                source_id=citation["source_id"],
                source_version=citation["source_version"],
            )
            for citation in citations
        ):
            return {"status": "not_applied"}
        if not allow_local and not all(
            is_public_source_version(
                opened.root,
                source_id=citation["source_id"],
                source_version=citation["source_version"],
            )
            for citation in citations
        ):
            return {"status": "local_scope_denied"}
        if not answer_is_supported(conclusion, quotes):
            return {"status": "insufficient_evidence"}
        if set(page_paths) != {target_page}:
            return {"status": "target_page_not_found"}
        page = opened.root / target_page
        if not page.is_file() or page.is_symlink():
            return {"status": "target_page_not_found"}
        existing_content = page.read_text(encoding="utf-8")
        summary = _parse_page_summary(target_page, existing_content)
        if summary is None:
            return {"status": "target_page_not_found"}
        source_ids = tuple(dict.fromkeys(citation["source_id"] for citation in citations))
        sources_by_id = {
            source.source_id: source for source in _load_current_sources(opened, set(source_ids))
        }
        if set(sources_by_id) != set(source_ids):
            return {"status": "citation_not_found"}
        for citation in citations:
            source = sources_by_id[citation["source_id"]]
            if source.source_version != citation["source_version"]:
                return {"status": "stale_citation"}
        change = PageChange(
            path=target_page,
            title=summary.title,
            page_type=summary.page_type,
            summary=summary.summary,
            body=conclusion.strip(),
            source_ids=source_ids,
            citations=tuple(
                PageCitation(
                    source_id=citation["source_id"],
                    locator=citation["locator"],
                )
                for citation in citations
            ),
        )
        sources = tuple(sources_by_id[source_id] for source_id in source_ids)
        source_texts = {source.source_id: _read_source_text(opened, source) for source in sources}
        candidate_files = {target_page: _render_llm_page(change, list(sources), source_texts)}
        if candidate_files[target_page] == existing_content:
            return {"status": "unchanged"}
        operations = [
            ChangeOperation(
                type=ChangeOperationType.UPDATE_PAGE,
                path=target_page,
                details={"origin": "agent-proposal", "question": question},
                origin=ChangeOrigin.AGENT_PROPOSAL,
            )
        ]
        index_path = "wiki/INDEX.md"
        candidate_files[index_path] = _render_index(opened, candidate_files)
        operations.append(
            ChangeOperation(
                type=ChangeOperationType.UPDATE_PAGE,
                path=index_path,
                origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
            )
        )
        _add_relations_page(opened, candidate_files, operations)
        base_commit = opened.current_commit()
        source_versions = {
            source_id: sources_by_id[source_id].source_version for source_id in source_ids
        }
        identity = "\n".join(
            [
                base_commit,
                "agent-proposal",
                question,
                target_page,
                *(f"{source_id}:{version}" for source_id, version in source_versions.items()),
                change.body,
            ]
        )
        changeset_id = "chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        changeset = ChangeSet(
            changeset_id=changeset_id,
            base_commit=base_commit,
            source_ids=source_ids,
            source_versions=source_versions,
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
        )
        risk = classify_risk(
            origin=ChangeOrigin.AGENT_PROPOSAL,
            operation_type=ChangeOperationType.UPDATE_PAGE,
            source_count=len(source_ids),
        )[0]
        stored = ChangeSetStore(opened).create(changeset, candidate_files)
        return {
            "status": "proposed",
            "changeset_id": stored.changeset.changeset_id,
            "risk": risk.value,
            "next_action": "review",
            "repository": {
                "repository_id": scope.repository_id,
                "name": scope.name,
            },
            "target_page": target_page,
            "source_versions": source_versions,
        }
    except UnmappedProjectError:
        return {"status": "unmapped_project"}
    except ValueError:
        # e.g. an empty citation excerpt or an invalid PageChange body.
        return {"status": "insufficient_evidence"}
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}


def list_changesets(workspace: Path) -> dict[str, object]:
    """List staged ChangeSets: ID, name, risk, status only (§12.2)."""
    try:
        opened = Workspace.open_readonly(workspace)
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}
    entries = []
    for stored in ChangeSetStore(opened).list_all():
        entries.append(
            {
                "changeset_id": stored.changeset.changeset_id,
                "name": _changeset_name(opened, stored),
                "risk": _changeset_risk(stored).value,
                "status": stored.changeset.status.value,
            }
        )
    return {"status": "ok", "changesets": entries}


def review_changeset(workspace: Path, changeset_id: str) -> dict[str, object]:
    """Return a bounded, read-only preview of one staged ChangeSet.

    The diff is capped per file and the citation summary is a short list of
    locators, so the payload stays small. No human review receipt is ever
    recorded — only the CLI and Portal write real receipts (§12.2).
    """
    try:
        opened = Workspace.open_readonly(workspace)
        stored = ChangeSetStore(opened).get(changeset_id)
    except (UnmappedProjectError, ValueError):
        return {"status": "unmapped_project"}
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}
    operations = {operation.path: operation.type for operation in stored.changeset.operations}
    page_paths = sorted(set(operations) | set(stored.candidate_files))
    pages = []
    for path in page_paths:
        before = opened.version_store.read_text_at(stored.changeset.base_commit, path) or ""
        after = stored.candidate_files.get(path, "")
        operation = operations.get(path)
        action = (
            "deleted"
            if operation is ChangeOperationType.ARCHIVE_PAGE
            else "created"
            if operation is ChangeOperationType.CREATE_PAGE
            else "updated"
        )
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=path,
                tofile=f"{path} (proposed)",
            )
        )
        truncated = len(diff) > _REVIEW_MAX_DIFF_CHARACTERS
        if truncated:
            diff = diff[:_REVIEW_MAX_DIFF_CHARACTERS] + "\n… (diff truncated)"
        pages.append(
            {
                "path": path,
                "action": action,
                "diff": diff,
                "diff_truncated": truncated,
                "citation_count": after.count("[^"),
                "citations": _citation_summary(after),
            }
        )
    return {
        "status": "ok",
        "changeset_id": stored.changeset.changeset_id,
        "name": _changeset_name(opened, stored),
        "risk": _changeset_risk(stored).value,
        "state": stored.changeset.status.value,
        "base_commit": stored.changeset.base_commit[:12],
        "pages": pages,
        "reviewed_by_mcp": False,
    }


def _load_citation_quotes(
    workspace: Path,
    citations: list[CitationPayload],
) -> tuple[list[CitationPayload], set[str]] | None:
    """Fetch each Citation's applied quote and its owning Wiki page (one query)."""
    opened = Workspace.open_readonly(workspace)
    quotes: list[CitationPayload] = []
    page_paths: set[str] = set()
    with _connect_readonly(opened.index_path) as connection:
        for citation in citations:
            row = connection.execute(
                """
                SELECT quote, page_path FROM wiki_facts
                WHERE source_id = ? AND source_version = ? AND locator = ?
                LIMIT 1
                """,
                (
                    citation["source_id"],
                    citation["source_version"],
                    citation["locator"],
                ),
            ).fetchone()
            if row is None:
                return None
            quotes.append(
                {
                    "source_id": citation["source_id"],
                    "source_version": citation["source_version"],
                    "locator": citation["locator"],
                    "quote": str(row[0]),
                }
            )
            page_paths.add(str(row[1]))
    return quotes, page_paths


def _changeset_name(opened: Workspace, stored: StoredChangeSet) -> str:
    content_paths = [path for path in stored.candidate_files if path.startswith("wiki/pages/")]
    for path in content_paths:
        summary = _parse_page_summary(path, stored.candidate_files[path])
        if summary is not None:
            return summary.title
    return "知识结构更新"


_RISK_RANK = {
    RiskLevel.LOW: 0,
    RiskLevel.MODERATE: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _changeset_risk(stored: StoredChangeSet) -> RiskLevel:
    risks = (
        classify_risk(
            origin=operation.origin,
            operation_type=operation.type,
            source_count=len(stored.changeset.source_ids),
        )[0]
        for operation in stored.changeset.operations
    )
    return max(risks, key=lambda level: _RISK_RANK[level], default=RiskLevel.LOW)


def _citation_summary(after: str) -> list[dict[str, str]]:
    """Parse at most a bounded number of footnote locators from a page."""
    matches = list(
        re.finditer(
            r"source `([a-f0-9]{64})` · revision `(\d+)` · `(chars:\d+-\d+)`",
            after,
        )
    )[:_REVIEW_MAX_CITATIONS]
    return [
        {"source_id": match.group(1), "source_version": match.group(2), "locator": match.group(3)}
        for match in matches
    ]


def _citation_metadata(
    workspace: Path,
    citations: list[CitationPayload],
) -> dict[tuple[str, int, str], tuple[str, str]]:
    """Load Wiki page and friendly source labels for all Citations once."""
    if not citations:
        return {}
    opened = Workspace.open_readonly(workspace)
    metadata: dict[tuple[str, int, str], tuple[str, str]] = {}
    with _connect_readonly(opened.index_path) as connection:
        for citation in citations:
            row = connection.execute(
                """
                SELECT facts.page_path, versions.title, repositories.last_synced_commit
                FROM wiki_facts AS facts
                JOIN sources ON sources.source_id = facts.source_id
                JOIN source_versions AS versions
                  ON versions.id = facts.source_version
                 AND versions.source_id = sources.id
                LEFT JOIN git_repositories AS repositories
                  ON repositories.repository_id = facts.repository_id
                WHERE facts.source_id = ? AND facts.source_version = ? AND facts.locator = ?
                LIMIT 1
                """,
                (
                    citation["source_id"],
                    citation["source_version"],
                    citation["locator"],
                ),
            ).fetchone()
            if row is not None:
                title = str(row["title"])
                commit = row["last_synced_commit"]
                label = f"{title} @ {str(commit)[:7]}" if commit else title
                metadata[
                    (citation["source_id"], citation["source_version"], citation["locator"])
                ] = (str(row["page_path"]), label)
    return metadata


def _is_wiki_fact(
    workspace: Path,
    source_id: str,
    source_version: int,
    locator: str,
) -> bool:
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        row = connection.execute(
            """
            SELECT 1 FROM wiki_facts
            WHERE source_id = ? AND source_version = ? AND locator = ?
            LIMIT 1
            """,
            (source_id, source_version, locator),
        ).fetchone()
    return row is not None


def _display_source_label(
    workspace: Path,
    source_id: str,
    source_version: int,
) -> str:
    """Build a friendly source label; never suggest an internal hash to cite."""
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        row = connection.execute(
            """
            SELECT versions.title, repositories.last_synced_commit
            FROM wiki_facts AS facts
            JOIN sources ON sources.source_id = facts.source_id
            JOIN source_versions AS versions
              ON versions.id = facts.source_version
             AND versions.source_id = sources.id
            LEFT JOIN git_repositories AS repositories
              ON repositories.repository_id = facts.repository_id
            WHERE facts.source_id = ? AND facts.source_version = ?
            ORDER BY facts.id
            LIMIT 1
            """,
            (source_id, source_version),
        ).fetchone()
    if row is None:
        return f"source {source_id[:12]}"
    title = str(row["title"])
    commit = row["last_synced_commit"]
    if commit:
        return f"{title} @ {str(commit)[:7]}"
    return title


def _page_entry(workspace: Path, page_path: str) -> dict[str, str]:
    page = workspace / page_path
    title = page_path.rsplit("/", 1)[-1].removesuffix(".md")
    if page.is_file() and not page.is_symlink():
        match = _TITLE_LINE.search(page.read_text(encoding="utf-8")[:400])
        if match:
            with suppress(json.JSONDecodeError):
                parsed = json.loads(match.group("title"))
                if isinstance(parsed, str) and parsed:
                    title = parsed
    return {"path": page_path, "title": title}


def _path_within(project_root: Path, checkout: Path) -> bool:
    resolved_checkout = checkout.resolve(strict=False)
    return project_root == resolved_checkout or project_root.is_relative_to(resolved_checkout)


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _truncate_text(text: str, max_characters: int) -> str:
    if len(text) <= max_characters:
        return text
    if max_characters <= 1:
        return "…"
    return text[: max_characters - 1].rstrip() + "…"


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
