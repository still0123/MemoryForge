from __future__ import annotations

import json
import sqlite3
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from memoryforge.core.capture_models import CaptureEvent
from memoryforge.core.models import ChangeOrigin, RiskLevel
from memoryforge.storage.capture_inbox import (
    CHAR_LIMITS,
    ProposalDraft,
    RedactionResult,
    build_capture_proposal,
    drain_capture_spool,
    record_capture_event,
    spool_capture_event,
)

REPO_A = "a" * 64
REPO_B = "b" * 64


def _identity_sanitize(text: str) -> RedactionResult:
    return RedactionResult(text=text, redaction_count=0)


def _counting_sanitize(text: str) -> RedactionResult:
    return RedactionResult(text=text, redaction_count=len(text) // 10 + 1)


def _make_event(
    *,
    event_id: str,
    repository_id: str = REPO_A,
    client: str = "codex",
    session_id: str = "sess-1",
    event_type: str = "user_prompt",
    text: str = "hello",
    paths: tuple[str, ...] = (),
) -> CaptureEvent:
    return CaptureEvent(
        event_id=event_id,
        repository_id=repository_id,
        client=client,  # type: ignore[arg-type]
        session_id=session_id,
        event_type=event_type,  # type: ignore[arg-type]
        observed_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        text=text,
        paths=paths,
    )


def test_duplicate_event_id_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    event = _make_event(event_id="evt-dup-1")
    first = record_capture_event(connection, event, sanitize=_identity_sanitize)
    second = record_capture_event(connection, event, sanitize=_identity_sanitize)
    assert first == "stored"
    assert second == "duplicate"

    cursor = connection.execute(
        "SELECT COUNT(*) FROM capture_events WHERE event_id = ?",
        ("evt-dup-1",),
    )
    assert cursor.fetchone()[0] == 1


def test_spool_file_permissions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        event = _make_event(event_id="evt-perm-1")
        result = spool_capture_event(workspace, event, sanitize=_identity_sanitize)
        assert result == "spooled"

        spool_dir = workspace / ".memoryforge" / "capture" / "spool"
        assert spool_dir.is_dir()
        dir_mode = stat.S_IMODE(spool_dir.stat().st_mode)
        assert dir_mode == 0o700, f"dir mode {oct(dir_mode)}"

        target = spool_dir / "evt-perm-1.json"
        assert target.is_file()
        file_mode = stat.S_IMODE(target.stat().st_mode)
        assert file_mode == 0o600, f"file mode {oct(file_mode)}"


def test_drain_crash_recovery_invalid_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        spool_dir = workspace / ".memoryforge" / "capture" / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        half = spool_dir / "evt-broken-1.json"
        half.write_text('{"event_id": "evt-broken-1", "incomplete', encoding="utf-8")
        good = spool_dir / "evt-good-1.json"
        good_event = _make_event(event_id="evt-good-1")
        good.write_text(good_event.model_dump_json(), encoding="utf-8")

        connection = sqlite3.connect(":memory:")
        drain_result = drain_capture_spool(workspace, connection)
        assert drain_result.processed == 1
        assert drain_result.failed >= 1
        assert not half.is_file() or drain_result.failed >= 1


def test_text_truncated_at_limit() -> None:
    connection = sqlite3.connect(":memory:")
    long_text = "A" * 4001
    event = _make_event(event_id="evt-trunc-1", event_type="user_prompt", text=long_text)
    result = record_capture_event(connection, event, sanitize=_identity_sanitize)
    assert result == "stored"

    cursor = connection.execute(
        "SELECT text, truncated FROM capture_events WHERE event_id = ?",
        ("evt-trunc-1",),
    )
    row = cursor.fetchone()
    stored_text, truncated_flag = row[0], bool(row[1])
    assert len(stored_text) == CHAR_LIMITS["user_prompt"]
    assert truncated_flag is True


def test_sanitize_applied_before_persist() -> None:
    connection = sqlite3.connect(":memory:")
    event = _make_event(event_id="evt-san-1", text="hello world sanitize me please")
    result = record_capture_event(connection, event, sanitize=_counting_sanitize)
    assert result == "stored"

    cursor = connection.execute(
        "SELECT redaction_count FROM capture_events WHERE event_id = ?",
        ("evt-san-1",),
    )
    row = cursor.fetchone()
    assert row is not None
    assert row[0] > 0


def test_repository_isolation() -> None:
    connection = sqlite3.connect(":memory:")
    event_a = _make_event(event_id="evt-a-1", repository_id=REPO_A, session_id="sess-A")
    event_b = _make_event(event_id="evt-b-1", repository_id=REPO_B, session_id="sess-B")
    record_capture_event(connection, event_a, sanitize=_identity_sanitize)
    record_capture_event(connection, event_b, sanitize=_identity_sanitize)

    proposal_a = build_capture_proposal(connection, repository_id=REPO_A, session_id="sess-A")
    assert isinstance(proposal_a, ProposalDraft)
    assert proposal_a.page_path.startswith("wiki/pages/capture/")
    assert "evt-a-1" not in proposal_a.page_path
    assert "sess-A" in proposal_a.page_path
    assert "evt-b-1" not in proposal_a.content or "sess-B" not in proposal_a.content


def test_proposal_does_not_write_wiki() -> None:
    connection = sqlite3.connect(":memory:")
    event = _make_event(event_id="evt-nw-1", session_id="sess-nw")
    record_capture_event(connection, event, sanitize=_identity_sanitize)
    proposal = build_capture_proposal(connection, repository_id=REPO_A, session_id="sess-nw")
    assert isinstance(proposal, ProposalDraft)
    assert proposal.origin == ChangeOrigin.AGENT_PROPOSAL
    assert proposal.risk == RiskLevel.HIGH
    assert proposal.citations == ()
    assert "capture_session" in proposal.reason_codes
    assert "manual_save_required" in proposal.reason_codes


def test_spool_duplicate_does_not_overwrite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        event_v1 = _make_event(event_id="evt-spool-dup", text="version1")
        first = spool_capture_event(workspace, event_v1, sanitize=_identity_sanitize)
        assert first == "spooled"
        event_v2 = _make_event(event_id="evt-spool-dup", text="version2")
        second = spool_capture_event(workspace, event_v2, sanitize=_identity_sanitize)
        assert second == "duplicate"

        spool_file = workspace / ".memoryforge" / "capture" / "spool" / "evt-spool-dup.json"
        stored = json.loads(spool_file.read_text(encoding="utf-8"))
        assert stored["text"] == "version1"


def test_spool_atomic_rename_pattern(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    event = _make_event(event_id="evt-atomic-1")
    result = spool_capture_event(workspace, event, sanitize=_identity_sanitize)
    assert result == "spooled"
    spool_dir = workspace / ".memoryforge" / "capture" / "spool"
    tmp_files = list(spool_dir.glob("tmp-*.json"))
    assert tmp_files == []


def test_drain_processes_all_valid_and_removes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    event1 = _make_event(event_id="evt-drain-1", text="first")
    event2 = _make_event(event_id="evt-drain-2", text="second")
    spool_capture_event(workspace, event1, sanitize=_identity_sanitize)
    spool_capture_event(workspace, event2, sanitize=_identity_sanitize)
    connection = sqlite3.connect(":memory:")
    drain_result = drain_capture_spool(workspace, connection)
    assert drain_result.processed == 2
    assert drain_result.failed == 0
    spool_dir = workspace / ".memoryforge" / "capture" / "spool"
    remaining = list(spool_dir.glob("*.json"))
    assert remaining == []


def test_drain_duplicate_event_skips(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    event = _make_event(event_id="evt-drain-dup")
    spool_capture_event(workspace, event, sanitize=_identity_sanitize)
    connection = sqlite3.connect(":memory:")
    record_capture_event(connection, event, sanitize=_identity_sanitize)
    drain_result = drain_capture_spool(workspace, connection)
    assert drain_result.skipped_duplicates == 1
