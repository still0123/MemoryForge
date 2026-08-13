from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from memoryforge.capture_models import CaptureEvent, InboxSession
from memoryforge.models import ChangeOrigin, RiskLevel


class RedactionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    redaction_count: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class ProposalDraft:
    page_path: str
    content: str
    citations: tuple[str, ...]
    origin: ChangeOrigin | Literal["CAP_SESSION"]
    risk: RiskLevel
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class DrainResult:
    processed: int
    failed: int
    skipped_duplicates: int
    errors: tuple[str, ...]


CHAR_LIMITS: dict[str, int] = {
    "user_prompt": 4000,
    "decision": 4000,
    "pre_compact": 4000,
    "file_changed": 1000,
    "test_result": 1000,
    "session_start": 1000,
    "session_end": 1000,
}

SCHEMA_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS capture_events (
        event_id TEXT NOT NULL,
        repository_id TEXT NOT NULL,
        client TEXT NOT NULL,
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        text TEXT NOT NULL,
        paths TEXT NOT NULL DEFAULT '[]',
        sensitivity TEXT NOT NULL DEFAULT 'local_only',
        unverified INTEGER NOT NULL DEFAULT 1,
        origin_sha256 TEXT NOT NULL DEFAULT '',
        redaction_count INTEGER NOT NULL DEFAULT 0,
        truncated INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (event_id, repository_id, client)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capture_events_repository
    ON capture_events (repository_id, client)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capture_events_session
    ON capture_events (repository_id, client, session_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capture_events_time
    ON capture_events (repository_id, client, observed_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS capture_sessions (
        repository_id TEXT NOT NULL,
        client TEXT NOT NULL,
        session_id TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'open',
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        PRIMARY KEY (repository_id, client, session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capture_sessions_repository
    ON capture_sessions (repository_id, client)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_capture_sessions_time
    ON capture_sessions (repository_id, client, last_seen_at)
    """,
)


def _ensure_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_SQL:
        connection.execute(statement)
    connection.commit()


def _truncate_text(event: CaptureEvent) -> tuple[str, bool]:
    limit = CHAR_LIMITS.get(event.event_type, 1000)
    if len(event.text) > limit:
        return event.text[:limit], True
    return event.text, False


def _compute_origin_sha256(event: CaptureEvent) -> str:
    data = event.model_dump(mode="json")
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _upsert_session(connection: sqlite3.Connection, event: CaptureEvent) -> None:
    now_iso = event.observed_at.isoformat()
    connection.execute(
        """
        INSERT INTO capture_sessions (repository_id, client, session_id, state, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, 'open', ?, ?)
        ON CONFLICT(repository_id, client, session_id) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            state = CASE WHEN capture_sessions.state = 'ignored' THEN 'ignored' ELSE 'open' END
        """,
        (
            event.repository_id,
            event.client,
            event.session_id,
            now_iso,
            now_iso,
        ),
    )


def record_capture_event(
    connection: sqlite3.Connection,
    event: CaptureEvent,
    *,
    sanitize: Callable[[str], RedactionResult],
) -> Literal["stored", "duplicate"]:
    _ensure_schema(connection)

    cursor = connection.execute(
        "SELECT 1 FROM capture_events WHERE event_id = ? AND repository_id = ? AND client = ?",
        (event.event_id, event.repository_id, event.client),
    )
    if cursor.fetchone() is not None:
        return "duplicate"

    redacted = sanitize(event.text)
    truncated_text, truncated_flag = _truncate_text(
        CaptureEvent(
            event_id=event.event_id,
            repository_id=event.repository_id,
            client=event.client,
            session_id=event.session_id,
            event_type=event.event_type,
            observed_at=event.observed_at,
            text=redacted.text,
            paths=event.paths,
            sensitivity=event.sensitivity,
            unverified=event.unverified,
            origin_sha256=event.origin_sha256,
            redaction_count=redacted.redaction_count,
            truncated=event.truncated,
        )
    )

    prepared_event = CaptureEvent(
        event_id=event.event_id,
        repository_id=event.repository_id,
        client=event.client,
        session_id=event.session_id,
        event_type=event.event_type,
        observed_at=event.observed_at,
        text=truncated_text,
        paths=event.paths,
        sensitivity=event.sensitivity,
        unverified=event.unverified,
        origin_sha256="",
        redaction_count=redacted.redaction_count,
        truncated=truncated_flag or event.truncated,
    )
    origin_sha256 = _compute_origin_sha256(prepared_event)

    final_event = CaptureEvent(
        event_id=prepared_event.event_id,
        repository_id=prepared_event.repository_id,
        client=prepared_event.client,
        session_id=prepared_event.session_id,
        event_type=prepared_event.event_type,
        observed_at=prepared_event.observed_at,
        text=prepared_event.text,
        paths=prepared_event.paths,
        sensitivity=prepared_event.sensitivity,
        unverified=prepared_event.unverified,
        origin_sha256=origin_sha256,
        redaction_count=prepared_event.redaction_count,
        truncated=prepared_event.truncated,
    )

    connection.execute(
        """
        INSERT INTO capture_events (
            event_id, repository_id, client, session_id, event_type,
            observed_at, text, paths, sensitivity, unverified,
            origin_sha256, redaction_count, truncated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id, repository_id, client) DO NOTHING
        """,
        (
            final_event.event_id,
            final_event.repository_id,
            final_event.client,
            final_event.session_id,
            final_event.event_type,
            final_event.observed_at.isoformat(),
            final_event.text,
            json.dumps(list(final_event.paths), ensure_ascii=False),
            final_event.sensitivity,
            1 if final_event.unverified else 0,
            final_event.origin_sha256,
            final_event.redaction_count,
            1 if final_event.truncated else 0,
        ),
    )
    _upsert_session(connection, final_event)
    connection.commit()
    return "stored"


def _spool_dir(workspace: Path) -> Path:
    return workspace / ".memoryforge" / "capture" / "spool"


def spool_capture_event(
    workspace: Path,
    event: CaptureEvent,
    *,
    sanitize: Callable[[str], RedactionResult],
) -> Literal["spooled", "duplicate"]:
    spool_directory = _spool_dir(workspace)
    spool_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(spool_directory, 0o700)
    except OSError:
        pass

    target_path = spool_directory / f"{event.event_id}.json"
    if target_path.is_file():
        return "duplicate"

    redacted = sanitize(event.text)
    truncated_text, truncated_flag = _truncate_text(
        CaptureEvent(
            event_id=event.event_id,
            repository_id=event.repository_id,
            client=event.client,
            session_id=event.session_id,
            event_type=event.event_type,
            observed_at=event.observed_at,
            text=redacted.text,
            paths=event.paths,
            sensitivity=event.sensitivity,
            unverified=event.unverified,
            origin_sha256=event.origin_sha256,
            redaction_count=redacted.redaction_count,
            truncated=event.truncated,
        )
    )

    prepared_event = CaptureEvent(
        event_id=event.event_id,
        repository_id=event.repository_id,
        client=event.client,
        session_id=event.session_id,
        event_type=event.event_type,
        observed_at=event.observed_at,
        text=truncated_text,
        paths=event.paths,
        sensitivity=event.sensitivity,
        unverified=event.unverified,
        origin_sha256="",
        redaction_count=redacted.redaction_count,
        truncated=truncated_flag or event.truncated,
    )
    origin_sha256 = _compute_origin_sha256(prepared_event)

    final_event = CaptureEvent(
        event_id=prepared_event.event_id,
        repository_id=prepared_event.repository_id,
        client=prepared_event.client,
        session_id=prepared_event.session_id,
        event_type=prepared_event.event_type,
        observed_at=prepared_event.observed_at,
        text=prepared_event.text,
        paths=prepared_event.paths,
        sensitivity=prepared_event.sensitivity,
        unverified=prepared_event.unverified,
        origin_sha256=origin_sha256,
        redaction_count=prepared_event.redaction_count,
        truncated=prepared_event.truncated,
    )

    payload = json.dumps(final_event.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
    tmp_name = f"tmp-{os.getpid()}-{random.randint(0, 1_000_000_000):010d}.json"
    tmp_path = spool_directory / tmp_name

    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass

    os.rename(tmp_path, target_path)
    return "spooled"


def drain_capture_spool(workspace: Path, connection: sqlite3.Connection) -> DrainResult:
    _ensure_schema(connection)
    spool_directory = _spool_dir(workspace)
    if not spool_directory.is_dir():
        return DrainResult(processed=0, failed=0, skipped_duplicates=0, errors=())

    processed = 0
    failed = 0
    skipped_duplicates = 0
    errors: list[str] = []

    spool_files = sorted(spool_directory.glob("*.json"))
    for spool_file in spool_files:
        try:
            raw = spool_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            event = CaptureEvent.model_validate(data)
        except Exception as exc:
            failed += 1
            errors.append(f"{spool_file.name}: parse error: {exc!r}")
            continue

        try:
            result = record_capture_event(
                connection,
                event,
                sanitize=lambda text: RedactionResult(text=text, redaction_count=0),
            )
        except Exception as exc:
            failed += 1
            errors.append(f"{event.event_id}: store error: {exc!r}")
            continue

        if result == "duplicate":
            skipped_duplicates += 1
        else:
            processed += 1

        try:
            spool_file.unlink()
        except OSError as exc:
            errors.append(f"{spool_file.name}: unlink failed: {exc!r}")

    return DrainResult(
        processed=processed,
        failed=failed,
        skipped_duplicates=skipped_duplicates,
        errors=tuple(errors),
    )


def _parse_session_events(
    connection: sqlite3.Connection,
    *,
    repository_id: str,
    session_id: str,
) -> list[CaptureEvent]:
    cursor = connection.execute(
        """
        SELECT event_id, repository_id, client, session_id, event_type,
               observed_at, text, paths, sensitivity, unverified,
               origin_sha256, redaction_count, truncated
        FROM capture_events
        WHERE repository_id = ? AND session_id = ?
        ORDER BY observed_at ASC, event_id ASC
        """,
        (repository_id, session_id),
    )
    rows = cursor.fetchall()
    events: list[CaptureEvent] = []
    for row in rows:
        try:
            paths_list = json.loads(row[7]) if row[7] else []
        except (json.JSONDecodeError, TypeError):
            paths_list = []
        events.append(
            CaptureEvent(
                event_id=row[0],
                repository_id=row[1],
                client=row[2],
                session_id=row[3],
                event_type=row[4],
                observed_at=datetime.fromisoformat(row[5]),
                text=row[6],
                paths=tuple(paths_list),
                sensitivity=row[8],
                unverified=bool(row[9]),
                origin_sha256=row[10],
                redaction_count=row[11],
                truncated=bool(row[12]),
            )
        )
    return events


def build_capture_proposal(
    connection: sqlite3.Connection,
    *,
    repository_id: str,
    session_id: str,
) -> ProposalDraft:
    _ensure_schema(connection)
    events = _parse_session_events(
        connection, repository_id=repository_id, session_id=session_id
    )

    client_name = events[0].client if events else "unknown"
    repo_prefix = repository_id[:12]
    page_path = f"wiki/pages/capture/{repo_prefix}/{client_name}-{session_id}.md"

    lines: list[str] = []
    lines.append(f"# Capture Session: {client_name} / {session_id}")
    lines.append("")
    lines.append("> **UNVERIFIED** — This page contains raw capture data.")
    lines.append("> Verify against code, tests, or cited Wiki before acting.")
    lines.append("")
    lines.append(f"- Repository: `{repository_id}`")
    lines.append(f"- Client: `{client_name}`")
    lines.append(f"- Session: `{session_id}`")
    lines.append(f"- Events: {len(events)}")
    lines.append("")
    lines.append("## Events")
    lines.append("")
    for event in events:
        lines.append(f"### `{event.event_type}` @ {event.observed_at.isoformat()}")
        lines.append("")
        if event.paths:
            lines.append("**Paths:**")
            for path in event.paths:
                lines.append(f"- `{path}`")
            lines.append("")
        snippet = event.text
        if len(snippet) > 600:
            snippet = snippet[:600] + "\n... [truncated in summary]"
        if snippet.strip():
            lines.append("```text")
            lines.append(snippet)
            lines.append("```")
            lines.append("")
        if event.redaction_count:
            lines.append(f"_Redactions applied: {event.redaction_count}_")
            lines.append("")

    content = "\n".join(lines)
    return ProposalDraft(
        page_path=page_path,
        content=content,
        citations=(),
        origin=ChangeOrigin.AGENT_PROPOSAL,
        risk=RiskLevel.HIGH,
        reason_codes=("capture_session", "manual_save_required"),
    )
