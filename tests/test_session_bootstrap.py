from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from memoryforge.interface.cli import app
from memoryforge.interface.codex_connect import install_codex_session_hook
from memoryforge.interface.mcp_server import (
    _episode_list_payload,
    _session_list_payload,
    _session_memory_payload,
)
from memoryforge.storage.session_bootstrap import (
    clear_pending_startup_capsules,
    consume_startup_capsule,
    list_conversation_episodes,
    list_conversation_sessions,
    load_conversation_sessions,
    queue_startup_capsule,
)


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE source_versions (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            tags_json TEXT NOT NULL
        );
        CREATE TABLE applied_source_versions (
            source_id TEXT PRIMARY KEY,
            source_version_id INTEGER NOT NULL
        );
        CREATE TABLE wiki_facts (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_version INTEGER NOT NULL,
            section_path TEXT NOT NULL,
            quote TEXT NOT NULL
        );
        """
    )
    now = datetime(2026, 8, 14, tzinfo=UTC)
    for index, title in enumerate(("Codex auth work", "Claude retry design"), start=1):
        source_id = str(index) * 64
        connection.execute("INSERT INTO sources VALUES (?, ?)", (index, source_id))
        connection.execute(
            "INSERT INTO source_versions VALUES (?, ?, ?, ?, ?, ?)",
            (
                index,
                index,
                title,
                (now + timedelta(minutes=index)).isoformat(),
                "public" if index == 1 else "local_only",
                '["conversation"]',
            ),
        )
        connection.execute("INSERT INTO applied_source_versions VALUES (?, ?)", (source_id, index))
        connection.execute(
            "INSERT INTO wiki_facts VALUES (?, ?, ?, 'Assistant conclusions', ?)",
            (index * 10, source_id, index, "逻辑大概是："),
        )
        connection.execute(
            "INSERT INTO wiki_facts VALUES (?, ?, ?, 'User prompts (search only)', ?)",
            (index * 10 + 1, source_id, index, "User asked a detailed question."),
        )
        connection.execute(
            "INSERT INTO wiki_facts VALUES (?, ?, ?, 'Assistant conclusions', ?)",
            (
                index * 10 + 2,
                source_id,
                index,
                f"Verified reusable summary for {title}, including the decision and its evidence. "
                "This is long enough to outrank a conversational lead-in.",
            ),
        )
        connection.execute(
            "INSERT INTO wiki_facts VALUES (?, ?, ?, 'Assistant conclusions', ?)",
            (
                index * 10 + 3,
                source_id,
                index,
                "func buildNameLockKey(uid, name string) string { return uid + name } " * 4,
            ),
        )
    connection.commit()
    return connection


def test_lists_applied_conversations_newest_first() -> None:
    sessions = list_conversation_sessions(_database())

    assert [session.title for session in sessions] == ["Claude retry design", "Codex auth work"]
    assert sessions[0].summary.startswith("Verified reusable summary for Claude retry design")


def test_lists_only_public_sessions_without_local_authorization() -> None:
    sessions = list_conversation_sessions(_database(), public_only=True)

    assert [session.title for session in sessions] == ["Codex auth work"]
    assert sessions[0].session_ref == "session:1"


def test_groups_related_sessions_into_topic_episodes() -> None:
    connection = _database()
    connection.execute(
        "UPDATE source_versions SET title = 'Codex 会话：OAuth callback work' WHERE id = 1"
    )
    source_id = "3" * 64
    connection.execute("INSERT INTO sources VALUES (3, ?)", (source_id,))
    connection.execute(
        "INSERT INTO source_versions VALUES (3, 3, ?, ?, 'local_only', ?)",
        (
            "Codex 会话：OAuth callback follow-up",
            datetime(2026, 8, 14, 1, 3, tzinfo=UTC).isoformat(),
            '["conversation"]',
        ),
    )
    connection.execute("INSERT INTO applied_source_versions VALUES (?, 3)", (source_id,))
    connection.execute(
        "INSERT INTO wiki_facts VALUES (14, ?, 1, 'Assistant conclusions', ?)",
        (
            "1" * 64,
            "OAuth token callback uses the device authorization result and refresh state.",
        ),
    )
    connection.execute(
        "INSERT INTO wiki_facts VALUES (30, ?, 3, 'Assistant conclusions', ?)",
        (
            source_id,
            "OAuth token callback follow-up confirmed the same device authorization flow.",
        ),
    )
    connection.commit()

    episodes = list_conversation_episodes(connection)
    auth = next(episode for episode in episodes if episode.topic == "OAuth callback follow-up")
    filtered = list_conversation_episodes(connection, query="OAuth token")
    public_only = list_conversation_episodes(connection, public_only=True)

    assert auth.session_refs == ("session:3", "session:1")
    assert auth.episode_ref.startswith("episode:")
    assert filtered[0] == auth
    assert public_only[0].session_refs == ("session:1",)


def test_loads_selected_session_memory_without_user_prompts() -> None:
    memory = load_conversation_sessions(
        _database(),
        session_refs=("session:2",),
        max_characters=1000,
    )

    assert memory.session_refs == ("session:2",)
    assert "Claude retry design" in memory.content
    assert "Verified reusable summary" in memory.content
    assert "User asked a detailed question" not in memory.content
    assert memory.character_count <= 1000
    assert memory.mode == "overview"
    assert memory.matched_facts == 2


def test_focuses_follow_up_inside_selected_sessions() -> None:
    memory = load_conversation_sessions(
        _database(),
        session_refs=("session:2",),
        question="What was the retry decision?",
        max_characters=1000,
    )

    assert memory.mode == "focused"
    assert memory.matched_facts == 1
    assert "Verified reusable summary for Claude retry design" in memory.content
    assert "逻辑大概是" not in memory.content


def test_focused_session_miss_does_not_return_unrelated_summary() -> None:
    memory = load_conversation_sessions(
        _database(),
        session_refs=("session:2",),
        question="EFS 冷热分层如何迁移数据？",
        max_characters=1000,
    )

    assert memory.mode == "focused"
    assert memory.matched_facts == 0
    assert memory.content == ""


def test_focused_session_rejects_question_without_searchable_terms() -> None:
    with pytest.raises(ValueError, match="searchable terms"):
        load_conversation_sessions(
            _database(),
            session_refs=("session:2",),
            question="如何",
        )


def test_load_denies_local_session_without_local_authorization() -> None:
    with pytest.raises(ValueError, match="not visible"):
        load_conversation_sessions(
            _database(),
            session_refs=("session:2",),
            public_only=True,
        )


def test_mcp_payload_lists_then_loads_selected_session(tmp_path: Path, monkeypatch) -> None:
    source = _database()
    database = tmp_path / "index.sqlite"
    with sqlite3.connect(database) as destination:
        source.backup(destination)
    source.close()
    monkeypatch.setattr(
        "memoryforge.interface.mcp_server.Workspace.open_readonly",
        lambda _workspace: SimpleNamespace(index_path=database),
    )

    listed = _session_list_payload(tmp_path, allow_local=True, limit=10)
    local = next(
        session for session in listed["sessions"] if session["title"] == "Claude retry design"
    )
    loaded = _session_memory_payload(
        tmp_path,
        allow_local=True,
        session_refs=[str(local["session_ref"])],
        max_characters=1000,
    )
    episodes = _episode_list_payload(
        tmp_path,
        allow_local=True,
        query=None,
        limit=10,
    )
    focused = _session_memory_payload(
        tmp_path,
        allow_local=True,
        session_refs=[str(local["session_ref"])],
        max_characters=1000,
        question="What was the retry decision?",
    )
    missed = _session_memory_payload(
        tmp_path,
        allow_local=True,
        session_refs=[str(local["session_ref"])],
        max_characters=1000,
        question="EFS 冷热分层如何迁移数据？",
    )
    public_only = _session_list_payload(tmp_path, allow_local=False, limit=10)

    assert listed["status"] == "ok"
    assert episodes["status"] == "ok"
    assert episodes["episodes"][0]["session_refs"]
    assert all(len(str(session["summary"])) <= 240 for session in listed["sessions"])
    assert loaded["status"] == "loaded"
    assert loaded["session_count"] == 1
    assert "Verified reusable summary for Claude retry design" in str(loaded["memory"])
    assert focused["status"] == "loaded"
    assert focused["mode"] == "focused"
    assert focused["retrieval_scope"] == "selected_sessions"
    assert missed["status"] == "no_session_evidence"
    assert missed["memory"] == ""
    assert "memoryforge_context" in str(missed["next_action"])
    assert [session["title"] for session in public_only["sessions"]] == ["Codex auth work"]


def test_session_capsule_rejects_unbounded_or_duplicate_selection(tmp_path: Path) -> None:
    connection = _database()

    with pytest.raises(ValueError, match="unique"):
        queue_startup_capsule(
            connection,
            source_ids=("1" * 64, "1" * 64),
            host="codex",
            project_root=tmp_path,
        )
    with pytest.raises(ValueError, match="12000"):
        queue_startup_capsule(
            connection,
            source_ids=("1" * 64,),
            host="codex",
            project_root=tmp_path,
            max_characters=12001,
        )


def test_queued_capsule_is_consumed_once(tmp_path: Path) -> None:
    connection = _database()
    queued = queue_startup_capsule(
        connection,
        source_ids=("1" * 64, "2" * 64),
        host="claude",
        project_root=tmp_path,
        max_characters=1000,
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
    )

    loaded = consume_startup_capsule(connection, host="claude", project_root=tmp_path)

    assert loaded == queued
    assert "Codex auth work" in loaded.content
    assert "Claude retry design" in loaded.content
    assert consume_startup_capsule(connection, host="claude", project_root=tmp_path) is None


def test_capsule_is_bound_to_exact_destination_directory(tmp_path: Path) -> None:
    connection = _database()
    destination = tmp_path / "destination"
    other = tmp_path / "other"
    destination.mkdir()
    other.mkdir()
    queued = queue_startup_capsule(
        connection,
        source_ids=("1" * 64,),
        host="codex",
        project_root=destination,
    )

    assert consume_startup_capsule(connection, host="codex", project_root=other) is None
    assert consume_startup_capsule(connection, host="codex", project_root=destination) == queued


def test_new_queue_replaces_pending_capsule(tmp_path: Path) -> None:
    connection = _database()
    first = queue_startup_capsule(
        connection,
        source_ids=("1" * 64,),
        host="codex",
        project_root=tmp_path,
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    second = queue_startup_capsule(
        connection,
        source_ids=("2" * 64,),
        host="codex",
        project_root=tmp_path,
        created_at=datetime(2026, 8, 14, 0, 1, tzinfo=UTC),
    )

    loaded = consume_startup_capsule(connection, host="codex", project_root=tmp_path)

    assert loaded is not None
    assert loaded.capsule_id == second.capsule_id
    assert loaded.capsule_id != first.capsule_id


def test_clear_pending_capsules_is_scoped_to_host(tmp_path: Path) -> None:
    connection = _database()
    queue_startup_capsule(
        connection,
        source_ids=("1" * 64,),
        host="codex",
        project_root=tmp_path,
    )
    claude = queue_startup_capsule(
        connection,
        source_ids=("2" * 64,),
        host="claude",
        project_root=tmp_path,
    )

    cleared = clear_pending_startup_capsules(connection, host="codex")

    assert cleared == 1
    assert consume_startup_capsule(connection, host="codex", project_root=tmp_path) is None
    assert consume_startup_capsule(connection, host="claude", project_root=tmp_path) == claude


def test_hook_config_prints_copyable_codex_session_start_command(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["capture", "hook-config", "--host", "codex", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["merge_into"] == "~/.codex/hooks.json"
    hook = payload["config"]["hooks"]["SessionStart"][0]["hooks"][0]
    assert hook["type"] == "command"
    assert "memoryforge.storage.session_bootstrap --host codex" in hook["command"]
    assert str(tmp_path) in hook["command"]


def test_startup_command_emits_context_then_consumes_it(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source_connection = _database()
    queued = queue_startup_capsule(
        source_connection,
        source_ids=("1" * 64,),
        host="codex",
        project_root=project,
    )
    database = tmp_path / "index.sqlite"
    with sqlite3.connect(database) as destination:
        source_connection.backup(destination)
    source_connection.close()
    monkeypatch.setattr(
        "memoryforge.storage.workspace.workspace_database",
        lambda _workspace: database,
    )
    arguments = [
        "capture",
        "startup",
        "--host",
        "codex",
        "--project",
        str(project),
        "--workspace",
        str(tmp_path),
    ]

    first = CliRunner().invoke(app, arguments)
    second = CliRunner().invoke(app, arguments)

    assert first.exit_code == 0
    payload = json.loads(first.stdout)
    assert payload["hookSpecificOutput"] == {
        "hookEventName": "SessionStart",
        "additionalContext": queued.content,
    }
    assert second.exit_code == 0
    assert second.stdout == ""


def test_continue_command_selects_session_without_source_ids(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source_connection = _database()
    database = tmp_path / "index.sqlite"
    with sqlite3.connect(database) as destination:
        source_connection.backup(destination)
    source_connection.close()
    monkeypatch.setattr(
        "memoryforge.storage.workspace.workspace_database",
        lambda _workspace: database,
    )
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    install_codex_session_hook(tmp_path / "wiki", codex_home)

    result = CliRunner().invoke(
        app,
        [
            "continue",
            "--to",
            "codex",
            "--project",
            str(project),
        ],
        input="1\n",
    )

    assert result.exit_code == 0, result.output
    assert "Claude retry design" in result.output
    assert "已准备 1 条会话" in result.output
    assert "点击“新建任务”" in result.output
    assert "不要在终端输入 new" in result.output
    with sqlite3.connect(database) as connection:
        pending = connection.execute(
            "SELECT host, project_root FROM startup_capsules WHERE consumed_at IS NULL"
        ).fetchone()
    assert pending == ("codex", str(project))


def test_continue_prompts_for_project_when_started_at_filesystem_root(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source_connection = _database()
    database = tmp_path / "index.sqlite"
    with sqlite3.connect(database) as destination:
        source_connection.backup(destination)
    source_connection.close()
    monkeypatch.setattr(
        "memoryforge.storage.workspace.workspace_database",
        lambda _workspace: database,
    )
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    install_codex_session_hook(tmp_path / "wiki", codex_home)

    result = CliRunner().invoke(
        app,
        ["continue", "--project", "/"],
        input=f"{project}\n1\n",
    )

    assert result.exit_code == 0, result.output
    assert "当前位于根目录" in result.output
    assert f"工作目录选择：{project}" in result.output


def test_lightweight_hook_entrypoint_consumes_capsule(tmp_path: Path) -> None:
    project = tmp_path / "project"
    workspace = tmp_path / "wiki"
    database = workspace / ".memoryforge" / "index.sqlite"
    project.mkdir()
    database.parent.mkdir(parents=True)
    source_connection = _database()
    queued = queue_startup_capsule(
        source_connection,
        source_ids=("1" * 64,),
        host="codex",
        project_root=project,
    )
    with sqlite3.connect(database) as destination:
        source_connection.backup(destination)
    source_connection.close()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "memoryforge.storage.session_bootstrap",
            "--host",
            "codex",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps({"cwd": str(project)}),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["hookSpecificOutput"]["additionalContext"] == queued.content
