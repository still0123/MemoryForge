from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime

from memoryforge.storage.capture_inbox import _ensure_schema
from memoryforge.core.capture_models import HandoffPayload


_UNVERIFIED_PREFIX = "Unverified handoff. Verify against code, tests, or cited Wiki before acting."
_EMPTY_PREFIX = "Unverified handoff. No recent capture sessions."

_TODO_PATTERN = re.compile(r"(?i)(TODO|todo|fix|待办)")


def _fetch_latest_session(
    connection: sqlite3.Connection,
    *,
    repository_id: str,
    before: datetime,
) -> tuple[str, str] | None:
    cursor = connection.execute(
        """
        SELECT client, session_id
        FROM capture_sessions
        WHERE repository_id = ?
          AND state IN ('open', 'ready')
          AND last_seen_at <= ?
        ORDER BY last_seen_at DESC, client ASC, session_id ASC
        LIMIT 1
        """,
        (repository_id, before.isoformat()),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return (row[0], row[1])


def _fetch_events(
    connection: sqlite3.Connection,
    *,
    repository_id: str,
    client: str,
    session_id: str,
    before: datetime,
) -> list[tuple[str, str, str, str]]:
    cursor = connection.execute(
        """
        SELECT event_type, observed_at, text, paths
        FROM capture_events
        WHERE repository_id = ? AND client = ? AND session_id = ?
          AND observed_at <= ?
        ORDER BY observed_at DESC, event_id DESC
        """,
        (repository_id, client, session_id, before.isoformat()),
    )
    return [
        (row[0], row[1], row[2], row[3])
        for row in cursor.fetchall()
    ]


def _parse_paths(paths_json: str) -> list[str]:
    try:
        result = json.loads(paths_json)
        if isinstance(result, list):
            return [str(p) for p in result]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _extract_unfinished(events_desc: list[tuple[str, str, str, str]]) -> list[str]:
    found: list[str] = []
    for event_type, _observed_at, text, _paths in events_desc:
        if event_type not in ("pre_compact", "decision"):
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if _TODO_PATTERN.search(line):
                if line not in found:
                    found.append(line)
                if len(found) >= 5:
                    return found
    return found


def build_handoff(
    connection: sqlite3.Connection,
    *,
    repository_id: str,
    before: datetime,
    max_characters: int = 2000,
) -> HandoffPayload:
    _ensure_schema(connection)

    session_info = _fetch_latest_session(
        connection, repository_id=repository_id, before=before
    )
    if session_info is None:
        return HandoffPayload(
            handoff_id="",
            repository_id=repository_id,
            before=before,
            character_count=len(_EMPTY_PREFIX),
            recent_task_line=None,
            decisions=(),
            changed_files=(),
            last_test_result=None,
            unfinished_items=(),
            content=_EMPTY_PREFIX,
        )

    client, session_id = session_info
    events_desc = _fetch_events(
        connection,
        repository_id=repository_id,
        client=client,
        session_id=session_id,
        before=before,
    )

    recent_task_line: str | None = None
    for event_type, _observed_at, text, _paths in events_desc:
        if event_type == "user_prompt":
            recent_task_line = text[:200]
            break

    decisions: list[str] = []
    for event_type, _observed_at, text, _paths in events_desc:
        if event_type == "decision":
            decisions.append(text[:150])
            if len(decisions) >= 3:
                break

    changed_files_set: list[str] = []
    for event_type, _observed_at, _text, paths_json in events_desc:
        if event_type == "file_changed":
            for path in _parse_paths(paths_json):
                truncated_path = path[:50]
                if truncated_path not in changed_files_set:
                    changed_files_set.append(truncated_path)
                if len(changed_files_set) >= 10:
                    break
            if len(changed_files_set) >= 10:
                break

    last_test_result: str | None = None
    for event_type, _observed_at, text, _paths in events_desc:
        if event_type == "test_result":
            last_test_result = text[:200]
            break

    unfinished_items = _extract_unfinished(events_desc)

    while True:
        sections: list[str] = [_UNVERIFIED_PREFIX]

        if recent_task_line:
            sections.append(f"**Recent task:** {recent_task_line}")

        if decisions:
            sections.append("**Recent decisions:**")
            for d in decisions:
                sections.append(f"- {d}")

        if changed_files_set:
            sections.append("**Changed files:**")
            for f in changed_files_set:
                sections.append(f"- {f}")

        if last_test_result:
            sections.append(f"**Last test:** {last_test_result}")

        if unfinished_items:
            sections.append("**Unfinished items:**")
            for u in unfinished_items:
                sections.append(f"- {u}")

        content = "\n\n".join(sections)
        character_count = len(content)

        if character_count <= max_characters:
            break

        if unfinished_items:
            unfinished_items = unfinished_items[:-1]
            continue
        if len(changed_files_set) > 1:
            changed_files_set = changed_files_set[:-1]
            continue
        if len(decisions) > 1:
            decisions = decisions[:-1]
            continue
        if changed_files_set:
            changed_files_set = []
            continue
        if decisions:
            decisions = []
            continue
        break

    if len(content) > max_characters:
        if recent_task_line:
            sections_reduced = [_UNVERIFIED_PREFIX, f"**Recent task:** {recent_task_line}"]
            content = "\n\n".join(sections_reduced)
        if len(content) > max_characters:
            content = _UNVERIFIED_PREFIX

    character_count = len(content)
    handoff_hash = hashlib.sha256(
        (repository_id + str(before) + content).encode("utf-8")
    ).hexdigest()[:16]

    return HandoffPayload(
        handoff_id=handoff_hash,
        repository_id=repository_id,
        before=before,
        character_count=character_count,
        recent_task_line=recent_task_line,
        decisions=tuple(decisions),
        changed_files=tuple(changed_files_set),
        last_test_result=last_test_result,
        unfinished_items=tuple(unfinished_items),
        content=content,
    )
