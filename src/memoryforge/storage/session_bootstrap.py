from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from memoryforge.storage.capture_inbox import _ensure_schema

Host = Literal["claude", "codex"]

_PREFIX = (
    "Unverified MemoryForge session handoff. Verify important claims against current "
    "code, tests, or cited Wiki before acting."
)

_SUMMARY_ORDER = """
    CASE WHEN facts.section_path = 'Assistant conclusions' THEN 0 ELSE 1 END,
    CASE WHEN length(facts.quote) BETWEEN 80 AND 1000 THEN 0 ELSE 1 END,
    length(facts.quote) DESC,
    facts.id DESC
"""

_TOKEN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "how",
    "is",
    "of",
    "or",
    "the",
    "to",
    "what",
    "was",
    "were",
    "why",
    "这个",
    "什么",
    "怎么",
    "如何",
    "为什么",
}
_EPISODE_GENERIC = _STOPWORDS | {
    "assistant",
    "chatgpt",
    "claude",
    "codex",
    "conversation",
    "decision",
    "enough",
    "evidence",
    "for",
    "in",
    "including",
    "lead",
    "long",
    "memoryforge",
    "reusable",
    "session",
    "summary",
    "this",
    "conversational",
    "outrank",
    "verified",
    "会话",
    "代码",
    "对话",
    "检查",
    "结论",
    "项目",
    "问题",
}
_EPISODE_TITLE_PREFIX = re.compile(
    r"^(?:codex|claude|chatgpt)\s*(?:会话|conversation)?\s*[:：-]\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConversationSession:
    source_id: str
    source_version: int
    title: str
    observed_at: str
    summary: str

    @property
    def session_ref(self) -> str:
        return f"session:{self.source_version}"


@dataclass(frozen=True)
class ConversationEpisode:
    episode_ref: str
    topic: str
    observed_at: str
    summary: str
    session_refs: tuple[str, ...]


@dataclass(frozen=True)
class SessionMemory:
    session_refs: tuple[str, ...]
    content: str
    character_count: int
    mode: Literal["overview", "focused"]
    matched_facts: int


@dataclass(frozen=True)
class StartupCapsule:
    capsule_id: str
    host: Host
    project_root: str
    source_ids: tuple[str, ...]
    content: str
    character_count: int


def list_conversation_sessions(
    connection: sqlite3.Connection,
    *,
    limit: int = 20,
    public_only: bool = False,
) -> tuple[ConversationSession, ...]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    visibility_clause = "AND versions.sensitivity = 'public'" if public_only else ""
    rows = connection.execute(
        f"""
        SELECT
            sources.source_id,
            versions.id,
            versions.title,
            versions.observed_at,
            COALESCE((
                SELECT facts.quote
                FROM wiki_facts AS facts
                WHERE facts.source_id = sources.source_id
                  AND facts.source_version = versions.id
                  AND lower(facts.section_path) NOT LIKE '%user prompt%'
                  AND facts.quote NOT LIKE '```%'
                  AND trim(facts.quote) NOT LIKE 'func %'
                  AND trim(facts.quote) NOT LIKE 'def %'
                  AND trim(facts.quote) NOT LIKE 'class %'
                ORDER BY {_SUMMARY_ORDER}
                LIMIT 1
            ), '') AS summary
        FROM source_versions AS versions
        JOIN sources ON sources.id = versions.source_id
        JOIN applied_source_versions AS applied
          ON applied.source_id = sources.source_id
         AND applied.source_version_id = versions.id
        WHERE versions.tags_json LIKE '%"conversation"%'
          {visibility_clause}
        ORDER BY versions.observed_at DESC, sources.source_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return tuple(
        ConversationSession(
            source_id=str(row[0]),
            source_version=int(row[1]),
            title=str(row[2]),
            observed_at=str(row[3]),
            summary=str(row[4]).strip(),
        )
        for row in rows
    )


def list_conversation_episodes(
    connection: sqlite3.Connection,
    *,
    limit: int = 10,
    query: str | None = None,
    public_only: bool = False,
) -> tuple[ConversationEpisode, ...]:
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    sessions = list_conversation_sessions(connection, limit=100, public_only=public_only)
    fact_text: dict[int, list[str]] = {session.source_version: [] for session in sessions}
    if sessions:
        placeholders = ", ".join("?" for _ in sessions)
        rows = connection.execute(
            f"""
            SELECT facts.source_version, facts.quote
            FROM wiki_facts AS facts
            WHERE facts.source_version IN ({placeholders})
              AND lower(facts.section_path) NOT LIKE '%user prompt%'
              AND facts.quote NOT LIKE '```%'
              AND trim(facts.quote) NOT LIKE 'func %'
              AND trim(facts.quote) NOT LIKE 'def %'
              AND trim(facts.quote) NOT LIKE 'class %'
            ORDER BY {_SUMMARY_ORDER}
            """,
            tuple(session.source_version for session in sessions),
        ).fetchall()
        for version_id, quote in rows:
            conclusions = fact_text[int(version_id)]
            if len(conclusions) < 20:
                conclusions.append(str(quote))
    session_terms = [
        (
            session,
            set(_episode_terms(session.title)),
            set(
                _episode_terms(
                    " ".join((session.title, session.summary, *fact_text[session.source_version]))
                )
            ),
        )
        for session in sessions
    ]
    frequencies: Counter[str] = Counter()
    for _session, title_terms, _search_terms in session_terms:
        frequencies.update(title_terms)
    rare_limit = max(2, len(sessions) // 20)
    rare_terms = {term for term, count in frequencies.items() if count <= rare_limit}
    clusters: list[tuple[list[ConversationSession], set[str], set[str]]] = []
    for session, title_terms, search_terms in session_terms:
        cluster = next(
            (
                candidate
                for candidate in clusters
                if _same_episode(title_terms, candidate[1], rare_terms)
            ),
            None,
        )
        if cluster is None:
            clusters.append(([session], title_terms, search_terms))
        else:
            cluster[0].append(session)
            cluster[2].update(search_terms)

    query_terms = _episode_terms(query.strip()) if query else frozenset()
    if query is not None and not query_terms:
        raise ValueError("query must contain searchable terms")
    if query_terms:
        clusters = [cluster for cluster in clusters if query_terms & cluster[2]]
        clusters.sort(key=lambda cluster: len(query_terms & cluster[2]), reverse=True)

    episodes: list[ConversationEpisode] = []
    for grouped_sessions, _title_terms, _search_terms in clusters[:limit]:
        session_refs = tuple(session.session_ref for session in grouped_sessions[:20])
        episodes.append(
            ConversationEpisode(
                episode_ref="episode:"
                + hashlib.sha256("\0".join(session_refs).encode()).hexdigest()[:12],
                topic=_EPISODE_TITLE_PREFIX.sub("", grouped_sessions[0].title),
                observed_at=grouped_sessions[0].observed_at,
                summary=next(
                    (session.summary for session in grouped_sessions if session.summary),
                    "",
                ),
                session_refs=session_refs,
            )
        )
    return tuple(episodes)


def load_conversation_sessions(
    connection: sqlite3.Connection,
    *,
    session_refs: Sequence[str],
    max_characters: int = 6000,
    public_only: bool = False,
    question: str | None = None,
) -> SessionMemory:
    if not session_refs:
        raise ValueError("at least one session_ref is required")
    if len(session_refs) > 20:
        raise ValueError("at most twenty sessions can be loaded")
    if len(set(session_refs)) != len(session_refs):
        raise ValueError("session_refs must be unique")
    if max_characters < len(_PREFIX) + 80:
        raise ValueError("max_characters is too small")
    if max_characters > 12000:
        raise ValueError("max_characters cannot exceed 12000")

    version_ids: list[int] = []
    for ref in session_refs:
        prefix, separator, raw_id = ref.partition(":")
        if prefix != "session" or separator != ":" or not raw_id.isdigit():
            raise ValueError(f"invalid session_ref: {ref}")
        version_ids.append(int(raw_id))

    placeholders = ", ".join("?" for _ in version_ids)
    visibility_clause = "AND versions.sensitivity = 'public'" if public_only else ""
    rows = connection.execute(
        f"""
        SELECT
            sources.source_id,
            versions.id,
            versions.title,
            versions.observed_at
        FROM source_versions AS versions
        JOIN sources ON sources.id = versions.source_id
        JOIN applied_source_versions AS applied
          ON applied.source_id = sources.source_id
         AND applied.source_version_id = versions.id
        WHERE versions.id IN ({placeholders})
          AND versions.tags_json LIKE '%"conversation"%'
          {visibility_clause}
        """,
        tuple(version_ids),
    ).fetchall()
    by_version = {int(row[1]): row for row in rows}
    missing = [
        ref
        for ref, version_id in zip(session_refs, version_ids, strict=True)
        if version_id not in by_version
    ]
    if missing:
        raise ValueError("session is not visible or applied: " + ", ".join(missing))

    normalized_question = question.strip() if question else ""
    question_tokens = _session_tokens(normalized_question)
    if question is not None and not normalized_question:
        raise ValueError("question must not be empty")
    if normalized_question and not question_tokens:
        raise ValueError("question must contain searchable terms")
    mode: Literal["overview", "focused"] = "focused" if normalized_question else "overview"
    per_session = max(80, (max_characters - len(_PREFIX) - 4) // len(session_refs))
    sections = [_PREFIX]
    matched_facts = 0
    for ref, version_id in zip(session_refs, version_ids, strict=True):
        row = by_version[version_id]
        quotes = connection.execute(
            f"""
            SELECT facts.quote
            FROM wiki_facts AS facts
            WHERE facts.source_id = ?
              AND facts.source_version = ?
              AND lower(facts.section_path) NOT LIKE '%user prompt%'
              AND facts.quote NOT LIKE '```%'
              AND trim(facts.quote) NOT LIKE 'func %'
              AND trim(facts.quote) NOT LIKE 'def %'
              AND trim(facts.quote) NOT LIKE 'class %'
            ORDER BY {_SUMMARY_ORDER}
            LIMIT ?
            """,
            (str(row[0]), version_id, 100 if mode == "focused" else 20),
        ).fetchall()
        ranked_quotes = _rank_session_quotes(
            (str(quote_row[0]).strip() for quote_row in quotes),
            question_tokens=question_tokens,
            ignored_tokens=_session_tokens(str(row[2])),
        )
        if mode == "focused" and not ranked_quotes:
            continue
        block = f"## {row[2]}\nSession: {ref}\nObserved: {row[3]}"
        included = 0
        for quote in ranked_quotes:
            item = f"\n- {quote}"
            remaining = per_session - len(block)
            if remaining <= 4:
                break
            block += item[:remaining]
            included += 1
        if included == 0:
            continue
        matched_facts += included
        sections.append(block[:per_session].rstrip())

    content = "\n\n".join(sections)[:max_characters].rstrip() if matched_facts else ""
    return SessionMemory(
        session_refs=tuple(session_refs),
        content=content,
        character_count=len(content),
        mode=mode,
        matched_facts=matched_facts,
    )


def _session_tokens(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for match in _TOKEN.findall(text.lower()):
        if match in _STOPWORDS:
            continue
        if all("\u4e00" <= character <= "\u9fff" for character in match):
            tokens.update(match[index : index + 2] for index in range(len(match) - 1))
        else:
            tokens.add(match)
    return frozenset(tokens)


def _episode_terms(text: str) -> frozenset[str]:
    terms = {term for term in _session_tokens(text) if term not in _EPISODE_GENERIC}
    for term in tuple(terms):
        if term.isascii() and len(term) >= 8:
            terms.update(term[index : index + 5] for index in range(len(term) - 4))
    return frozenset(terms)


def _same_episode(left: set[str], right: set[str], rare_terms: set[str]) -> bool:
    return len(left & right & rare_terms) >= 2


def _rank_session_quotes(
    quotes: Iterable[str],
    *,
    question_tokens: frozenset[str],
    ignored_tokens: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    unique_quotes = tuple(dict.fromkeys(quote for quote in quotes if quote))
    if not question_tokens:
        return unique_quotes
    effective_tokens = question_tokens - ignored_tokens
    if not effective_tokens:
        return unique_quotes
    numeric_tokens = {token for token in effective_tokens if token.isdigit()}
    minimum_overlap = min(2, len(effective_tokens))
    scored = []
    for index, quote in enumerate(unique_quotes):
        quote_tokens = _session_tokens(quote)
        if numeric_tokens and not numeric_tokens <= quote_tokens:
            continue
        overlap = len(effective_tokens & quote_tokens)
        if overlap >= minimum_overlap:
            scored.append((overlap, index, quote))
    return tuple(
        quote for _score, _index, quote in sorted(scored, key=lambda item: (-item[0], item[1]))
    )


def queue_startup_capsule(
    connection: sqlite3.Connection,
    *,
    source_ids: Sequence[str],
    host: Host,
    project_root: Path,
    max_characters: int = 6000,
    created_at: datetime | None = None,
) -> StartupCapsule:
    if not source_ids:
        raise ValueError("at least one --source is required")
    if len(source_ids) > 3:
        raise ValueError("first version supports at most three sessions")
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("conversation sources must be unique")
    if max_characters < len(_PREFIX) + 80:
        raise ValueError("max_characters is too small")
    if max_characters > 12000:
        raise ValueError("max_characters cannot exceed 12000")

    _ensure_schema(connection)
    placeholders = ", ".join("?" for _ in source_ids)
    rows = connection.execute(
        f"""
        SELECT
            sources.source_id,
            versions.id,
            versions.title,
            versions.observed_at,
            COALESCE((
                SELECT facts.quote
                FROM wiki_facts AS facts
                WHERE facts.source_id = sources.source_id
                  AND facts.source_version = versions.id
                  AND lower(facts.section_path) NOT LIKE '%user prompt%'
                  AND facts.quote NOT LIKE '```%'
                  AND trim(facts.quote) NOT LIKE 'func %'
                  AND trim(facts.quote) NOT LIKE 'def %'
                  AND trim(facts.quote) NOT LIKE 'class %'
                ORDER BY {_SUMMARY_ORDER}
                LIMIT 1
            ), '') AS summary
        FROM source_versions AS versions
        JOIN sources ON sources.id = versions.source_id
        JOIN applied_source_versions AS applied
          ON applied.source_id = sources.source_id
         AND applied.source_version_id = versions.id
        WHERE versions.tags_json LIKE '%"conversation"%'
          AND sources.source_id IN ({placeholders})
        """,
        tuple(source_ids),
    ).fetchall()
    by_source = {str(row[0]): row for row in rows}
    missing = [source_id for source_id in source_ids if source_id not in by_source]
    if missing:
        raise ValueError("conversation source is not applied: " + ", ".join(missing))

    per_session = max(80, (max_characters - len(_PREFIX) - 4) // len(source_ids))
    sections = [_PREFIX]
    refs: list[dict[str, object]] = []
    for source_id in source_ids:
        row = by_source[source_id]
        title = str(row[2])
        observed_at = str(row[3])
        summary = str(row[4]).strip() or "No compiled session summary."
        block = f"## {title}\nObserved: {observed_at}\n{summary}"
        sections.append(block[:per_session].rstrip())
        refs.append(
            {
                "source_id": source_id,
                "source_version": int(row[1]),
                "title": title,
            }
        )

    content = "\n\n".join(sections)[:max_characters].rstrip()
    now = created_at or datetime.now(UTC)
    canonical_project = str(project_root.resolve(strict=False))
    digest_input = json.dumps(
        {
            "host": host,
            "project_root": canonical_project,
            "source_refs": refs,
            "content": content,
            "created_at": now.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    capsule_id = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]

    connection.execute(
        """
        UPDATE startup_capsules
        SET consumed_at = ?
        WHERE host = ? AND project_root = ? AND consumed_at IS NULL
        """,
        (now.isoformat(), host, canonical_project),
    )
    connection.execute(
        """
        INSERT INTO startup_capsules(
            capsule_id, host, project_root, content, source_refs_json,
            character_count, created_at, consumed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            capsule_id,
            host,
            canonical_project,
            content,
            json.dumps(refs, ensure_ascii=False, sort_keys=True),
            len(content),
            now.isoformat(),
        ),
    )
    connection.commit()
    return StartupCapsule(
        capsule_id=capsule_id,
        host=host,
        project_root=canonical_project,
        source_ids=tuple(source_ids),
        content=content,
        character_count=len(content),
    )


def consume_startup_capsule(
    connection: sqlite3.Connection,
    *,
    host: Host,
    project_root: Path,
    consumed_at: datetime | None = None,
) -> StartupCapsule | None:
    _ensure_schema(connection)
    canonical_project = str(project_root.resolve(strict=False))
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT capsule_id, content, source_refs_json, character_count
            FROM startup_capsules
            WHERE host = ? AND project_root = ? AND consumed_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (host, canonical_project),
        ).fetchone()
        if row is None:
            connection.commit()
            return None

        connection.execute(
            "UPDATE startup_capsules SET consumed_at = ? WHERE capsule_id = ?",
            ((consumed_at or datetime.now(UTC)).isoformat(), str(row[0])),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    refs = json.loads(str(row[2]))
    return StartupCapsule(
        capsule_id=str(row[0]),
        host=host,
        project_root=canonical_project,
        source_ids=tuple(str(ref["source_id"]) for ref in refs),
        content=str(row[1]),
        character_count=int(row[3]),
    )


def clear_pending_startup_capsules(
    connection: sqlite3.Connection,
    *,
    host: Host,
    cleared_at: datetime | None = None,
) -> int:
    """Discard pending automatic startup context for one Host."""
    _ensure_schema(connection)
    cursor = connection.execute(
        """
        UPDATE startup_capsules
        SET consumed_at = ?
        WHERE host = ? AND consumed_at IS NULL
        """,
        ((cleared_at or datetime.now(UTC)).isoformat(), host),
    )
    connection.commit()
    return cursor.rowcount


def main() -> None:
    """Lightweight SessionStart entry point; avoids loading the full CLI."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--host", choices=("codex", "claude"), required=True)
    arguments = parser.parse_args()
    try:
        hook_input = json.load(sys.stdin)
        project = Path(str(hook_input["cwd"])).resolve(strict=True)
        database = arguments.workspace / ".memoryforge" / "index.sqlite"
        with sqlite3.connect(database) as connection:
            capsule = consume_startup_capsule(
                connection,
                host=arguments.host,
                project_root=project,
            )
        if capsule is not None:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "SessionStart",
                            "additionalContext": capsule.content,
                        }
                    },
                    ensure_ascii=False,
                )
            )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"systemMessage": f"MemoryForge startup skipped: {exc}"}))


if __name__ == "__main__":
    main()
