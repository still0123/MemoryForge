from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from memoryforge.capture_inbox import RedactionResult, record_capture_event
from memoryforge.capture_models import CaptureEvent
from memoryforge.handoff import build_handoff


REPO_A = "a" * 64
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _identity_sanitize(text: str) -> RedactionResult:
    return RedactionResult(text=text, redaction_count=0)


def _add_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    text: str,
    minutes_ago: int,
    session_id: str = "sess-handoff-1",
    paths: tuple[str, ...] = (),
    client: str = "codex",
    event_id: str | None = None,
) -> None:
    observed_at = NOW - timedelta(minutes=minutes_ago)
    evt_id = event_id or f"evt-{event_type}-{minutes_ago}-{id(text)}"
    event = CaptureEvent(
        event_id=evt_id,
        repository_id=REPO_A,
        client=client,  # type: ignore[arg-type]
        session_id=session_id,
        event_type=event_type,  # type: ignore[arg-type]
        observed_at=observed_at,
        text=text,
        paths=paths,
    )
    record_capture_event(connection, event, sanitize=_identity_sanitize)


def test_empty_handoff_returns_reminder() -> None:
    connection = sqlite3.connect(":memory:")
    handoff = build_handoff(connection, repository_id=REPO_A, before=NOW)
    assert handoff.content == "Unverified handoff. No recent capture sessions."
    assert handoff.handoff_id == ""
    assert handoff.decisions == ()
    assert handoff.changed_files == ()
    assert handoff.unfinished_items == ()


def test_ignored_session_not_included() -> None:
    connection = sqlite3.connect(":memory:")
    _add_event(connection, event_type="session_start", text="ignored", minutes_ago=30, session_id="sess-ignored")
    connection.execute(
        "UPDATE capture_sessions SET state = 'ignored' WHERE repository_id = ? AND client = 'codex' AND session_id = ?",
        (REPO_A, "sess-ignored"),
    )
    connection.commit()
    handoff = build_handoff(connection, repository_id=REPO_A, before=NOW)
    assert handoff.content == "Unverified handoff. No recent capture sessions."


def test_handoff_respects_character_limit() -> None:
    connection = sqlite3.connect(":memory:")
    _add_event(
        connection,
        event_type="user_prompt",
        text="Please refactor the authentication module with role-based access control and auditing",
        minutes_ago=5,
    )
    for i in range(8):
        _add_event(
            connection,
            event_type="decision",
            text=f"Decision #{i} regarding module architecture approach option " + "x" * 100,
            minutes_ago=4 - i // 4,
        )
    handoff = build_handoff(connection, repository_id=REPO_A, before=NOW, max_characters=2000)
    assert handoff.character_count <= 2000
    assert len(handoff.content) <= 2000


def test_handoff_contains_unverified_prefix() -> None:
    connection = sqlite3.connect(":memory:")
    _add_event(connection, event_type="user_prompt", text="hello world task", minutes_ago=2)
    handoff = build_handoff(connection, repository_id=REPO_A, before=NOW)
    assert handoff.content.startswith("Unverified handoff.")
    assert "Verify against code, tests, or cited Wiki before acting." in handoff.content


def test_handoff_deterministic_same_input() -> None:
    def build_once() -> str:
        connection = sqlite3.connect(":memory:")
        _add_event(
            connection,
            event_type="user_prompt",
            text="build the capture inbox module",
            minutes_ago=3,
            event_id="evt-det-1",
        )
        _add_event(
            connection,
            event_type="decision",
            text="use pydantic frozen models",
            minutes_ago=2,
            event_id="evt-det-2",
        )
        _add_event(
            connection,
            event_type="file_changed",
            text="changed capture_models.py",
            minutes_ago=1,
            paths=("src/memoryforge/capture_models.py", "src/memoryforge/capture_inbox.py"),
            event_id="evt-det-3",
        )
        handoff = build_handoff(connection, repository_id=REPO_A, before=NOW)
        return handoff.handoff_id

    first = build_once()
    second = build_once()
    assert first == second
    assert len(first) == 16


def test_handoff_extracts_recent_task_decisions_files() -> None:
    connection = sqlite3.connect(":memory:")
    _add_event(
        connection,
        event_type="user_prompt",
        text="Build the capture inbox module with spool and drain support.",
        minutes_ago=10,
    )
    _add_event(
        connection,
        event_type="decision",
        text="Use SQLite tables capture_events and capture_sessions with composite PK.",
        minutes_ago=9,
    )
    _add_event(
        connection,
        event_type="decision",
        text="Atomic rename for spool files with tmp- prefix.",
        minutes_ago=8,
    )
    _add_event(
        connection,
        event_type="file_changed",
        text="created capture models",
        minutes_ago=7,
        paths=("src/memoryforge/capture_models.py",),
    )
    _add_event(
        connection,
        event_type="file_changed",
        text="created capture inbox",
        minutes_ago=6,
        paths=("src/memoryforge/capture_inbox.py",),
    )
    _add_event(
        connection,
        event_type="test_result",
        text="test_capture_inbox.py: 5 passed, 0 failed",
        minutes_ago=5,
    )
    _add_event(
        connection,
        event_type="decision",
        text="TODO: add session_end event handling edge case",
        minutes_ago=4,
    )
    _add_event(
        connection,
        event_type="pre_compact",
        text="Summary notes:\n- TODO: verify handoff truncation order\n- fix: handle empty sessions\n- 待办: write more tests",
        minutes_ago=3,
    )

    handoff = build_handoff(connection, repository_id=REPO_A, before=NOW, max_characters=4000)
    assert handoff.recent_task_line is not None
    assert "capture inbox" in handoff.recent_task_line.lower()
    assert len(handoff.decisions) >= 2
    assert len(handoff.changed_files) >= 2
    assert handoff.last_test_result is not None
    assert "passed" in handoff.last_test_result.lower()
    assert len(handoff.unfinished_items) >= 2


def test_handoff_truncation_priority() -> None:
    connection = sqlite3.connect(":memory:")
    _add_event(
        connection,
        event_type="user_prompt",
        text="Small task",
        minutes_ago=10,
    )
    for i in range(3):
        _add_event(
            connection,
            event_type="decision",
            text=f"decision {i}: " + "y" * 140,
            minutes_ago=9 - i,
            event_id=f"evt-dec-{i}",
        )
    _add_event(
        connection,
        event_type="file_changed",
        text="files",
        minutes_ago=5,
        paths=tuple(f"src/module/file_{i}.py" for i in range(8)),
        event_id="evt-files",
    )
    _add_event(
        connection,
        event_type="pre_compact",
        text="\n".join(f"- TODO unfinished item {i}: " + "z" * 100 for i in range(5)),
        minutes_ago=4,
        event_id="evt-precompact",
    )
    handoff = build_handoff(connection, repository_id=REPO_A, before=NOW, max_characters=500)
    assert handoff.character_count <= 500
    assert handoff.recent_task_line is not None
    assert "Unverified handoff" in handoff.content
