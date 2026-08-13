"""Protocol-agnostic Agent Access functions (Phase 1 of the progressive-recall spec).

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
  CLI ``recall`` command so CLI and MCP share one implementation.

No new query engine is introduced here; ``answer_question`` remains the single
retrieval/support implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import suppress
from pathlib import Path

from memoryforge.errors import MemoryForgeError, UnmappedProjectError
from memoryforge.models import GitRepositoryRecord, Sensitivity
from memoryforge.query import SupportPayload, answer_question
from memoryforge.wiki_facts import (
    CitationPayload,
    is_conversation_process_note,
    parse_page_citations,
)
from memoryforge.workspace import (
    Workspace,
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    _connect_readonly,
    is_applied_source_version,
    is_public_source_version,
    list_git_checkouts,
    read_source_excerpt,
)

# L2/L3 hard budgets (§7.1); counted in characters, never in tokens.
_CONTEXT_MAX_OUTPUT_CHARACTERS = 8000
_EVIDENCE_MAX_CHARACTERS = 2000
_STARTUP_CONTEXT_MAX_CHARACTERS = 4000

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


def server_name(workspace: Path, project_root: Path) -> str:
    """Return the stable, unique MCP server name for one binding (§8).

    ``memoryforge-<project-slug>-<first8(sha256(canonical_workspace + "\\0"
    + canonical_project_root))>``. The same Workspace + project always yields
    the same name; a different binding yields a different name, so Host
    configurations can never silently overwrite each other.
    """
    canonical_workspace = str(workspace.resolve(strict=False))
    canonical_project = str(project_root.resolve(strict=False))
    digest = hashlib.sha256(
        f"{canonical_workspace}\0{canonical_project}".encode()
    ).hexdigest()[:8]
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
    max_pages: int = 3,
    max_citations: int = 6,
) -> dict[str, object]:
    """Return bounded L2 context for one question from the bound project.

    ``project_root`` and the resulting ``repository_id`` are fixed by the
    connecting Host (never by the caller); ``allow_local`` is the server-fixed
    authorization. The sensitivity gate runs before Support scoring and answer
    assembly, so ``Sensitivity.LOCAL_ONLY`` facts can never influence page
    selection, Support or the answer hint unless explicitly authorized.
    """
    max_pages = _clamp_int(max_pages, 1, 3)
    max_citations = _clamp_int(max_citations, 1, 6)
    try:
        scope = resolve_repository_scope(workspace, project_root)
        opened = Workspace.open_readonly(workspace)
        workspace_commit = opened.current_commit()
    except (UnmappedProjectError, ValueError):
        return {"status": "unmapped_project"}
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}
    try:
        result = answer_question(
            opened.root,
            question,
            provider=None,
            verify=False,
            max_pages=max_pages,
            max_citations=max_citations,
            allow_local=allow_local,
            public_only=not allow_local,
            repository_id=scope.repository_id,
        )
    except _OPEN_FAILURES:
        return {"status": "workspace_unavailable"}
    answer_hint = "" if result["status"] == "unknown" else str(result["answer"])
    wiki_pages = [_page_entry(opened.root, page_path) for page_path in result["wiki_pages"]]
    page_paths = _citation_page_paths(opened.root, result["citations"])
    citations = [
        {
            "source_id": citation["source_id"],
            "source_version": citation["source_version"],
            "locator": citation["locator"],
            "quote": citation["quote"],
            "wiki_page": page_paths.get(
                (citation["source_id"], citation["source_version"], citation["locator"]),
                "",
            ),
            "section": str(citation.get("section_path", "")),
            "display_source": _display_source_label(
                opened.root,
                citation["source_id"],
                citation["source_version"],
            ),
        }
        for citation in result["citations"]
    ]
    support: SupportPayload | dict[str, object] = result.get("support") or {}
    content = {
        "answer_hint": answer_hint,
        "wiki_pages": wiki_pages,
        "citations": citations,
        "support": support,
    }
    output_characters = len(json.dumps(content, ensure_ascii=False))
    truncated = False
    if output_characters > _CONTEXT_MAX_OUTPUT_CHARACTERS:
        truncated = True
        while citations:
            citations.pop()
            wiki_pages = [
                page
                for page in wiki_pages
                if page["path"] in {citation["wiki_page"] for citation in citations}
            ]
            content = {
                "answer_hint": answer_hint,
                "wiki_pages": wiki_pages,
                "citations": citations,
                "support": support,
            }
            if len(json.dumps(content, ensure_ascii=False)) <= _CONTEXT_MAX_OUTPUT_CHARACTERS:
                break
        else:
            answer_hint = _truncate_text(
                answer_hint,
                _CONTEXT_MAX_OUTPUT_CHARACTERS
                - len(
                    json.dumps(
                        {
                            "answer_hint": "",
                            "wiki_pages": wiki_pages,
                            "citations": citations,
                            "support": support,
                        },
                        ensure_ascii=False,
                    )
                ),
            )
            content = {
                "answer_hint": answer_hint,
                "wiki_pages": wiki_pages,
                "citations": citations,
                "support": support,
            }
        output_characters = len(json.dumps(content, ensure_ascii=False))
    return {
        "status": "answered" if result["status"] == "answered" else "unknown",
        "workspace_commit": workspace_commit,
        "repository": {
            "repository_id": scope.repository_id,
            "name": scope.name,
        },
        "answer_hint": answer_hint,
        "wiki_pages": wiki_pages,
        "citations": citations,
        "support": support,
        "budget": {
            "max_pages": max_pages,
            "max_citations": max_citations,
            "max_output_characters": _CONTEXT_MAX_OUTPUT_CHARACTERS,
            "output_characters": output_characters,
            "truncated": truncated,
        },
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
    except (UnmappedProjectError, ValueError):
        return {"status": "unmapped_project"}
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


def _citation_page_paths(
    workspace: Path,
    citations: list[CitationPayload],
) -> dict[tuple[str, int, str], str]:
    """Map each Citation back to the Wiki page that carries it (one query)."""
    if not citations:
        return {}
    opened = Workspace.open_readonly(workspace)
    page_paths: dict[tuple[str, int, str], str] = {}
    with _connect_readonly(opened.index_path) as connection:
        for citation in citations:
            row = connection.execute(
                """
                SELECT page_path FROM wiki_facts
                WHERE source_id = ? AND source_version = ? AND locator = ?
                LIMIT 1
                """,
                (
                    citation["source_id"],
                    citation["source_version"],
                    citation["locator"],
                ),
            ).fetchone()
            if row is not None:
                page_paths[
                    (citation["source_id"], citation["source_version"], citation["locator"])
                ] = str(row[0])
    return page_paths


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
