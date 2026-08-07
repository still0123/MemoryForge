from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from memoryforge.folder_adapter import sync_folder
from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.importer import SourceValidationError
from memoryforge.models import Sensitivity
from memoryforge.workspace import init_workspace, search_sources


def test_folder_import_recurses_formats_ignore_and_context(tmp_path: Path) -> None:
    source_root = tmp_path / "knowledge"
    _write(source_root / "docs" / "overview.md", "# Overview\n\nThe cache uses a namespace.")
    _write(source_root / "notes" / "operations.txt", "Deployments run every Friday.")
    _write(
        source_root / "saved" / "retry.html",
        """
        <html><head><title>Retry Design</title></head><body><main>
        <h1>Retry Design</h1>
        <p>Retries stop after three attempts to avoid amplifying an upstream outage.</p>
        <p>Each retry uses bounded exponential backoff before another request.</p>
        <script>const secret = "ignored script";</script>
        </main></body></html>
        """,
    )
    _write(source_root / "ignored" / "private.md", "API_TOKEN=ghp_" + "a" * 40)
    _write(source_root / ".hidden" / "hidden.md", "# Hidden\n\nDo not import.")
    _write(source_root / ".memoryforgeignore", "ignored/\n")
    workspace = init_workspace(tmp_path / "workspace")

    invoked = CliRunner().invoke(
        app,
        [
            "folder-import",
            str(source_root),
            "--workspace",
            str(workspace),
            "--public",
        ],
    )

    assert invoked.exit_code == 0, invoked.output
    payload = json.loads(invoked.stdout)
    assert payload["created"] == 3
    assert payload["updated"] == 0
    assert payload["unchanged"] == 0
    assert payload["deleted"] == 0
    assert [document["relative_path"] for document in payload["documents"]] == [
        "docs/overview.md",
        "notes/operations.txt",
        "saved/retry.html",
    ]
    assert str(source_root) not in invoked.stdout
    assert str(workspace) not in invoked.stdout

    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        rows = connection.execute(
            """
            SELECT sources.source_path, versions.media_type, versions.sensitivity,
                   versions.tags_json
            FROM source_versions AS versions
            JOIN sources ON sources.id = versions.source_id
            WHERE versions.is_current = 1
            ORDER BY sources.source_path
            """
        ).fetchall()
    assert [row[0] for row in rows] == [
        "docs/overview.md",
        "notes/operations.txt",
        "saved/retry.html",
    ]
    assert [row[1] for row in rows] == ["text/markdown", "text/plain", "text/markdown"]
    assert {row[2] for row in rows} == {"public"}
    assert [set(json.loads(row[3])) for row in rows] == [
        {"folder", "folder-path:docs"},
        {"folder", "folder-path:notes"},
        {"folder", "folder-path:saved"},
    ]
    html_result = search_sources(workspace, "bounded exponential")
    assert [result.source_path for result in html_result] == ["saved/retry.html"]
    assert "ignored script" not in (workspace / html_result[0].snapshot_path).read_text(
        encoding="utf-8"
    )


def test_folder_import_duplicate_sync_is_idempotent(tmp_path: Path) -> None:
    source_root = tmp_path / "knowledge"
    _write(source_root / "design.md", "# Cache Design\n\nUse a versioned namespace.")
    workspace = init_workspace(tmp_path / "workspace")

    first = sync_folder(workspace, source_root)
    second = sync_folder(workspace, source_root)

    assert first.folder_id == second.folder_id
    assert (first.created, first.updated, first.unchanged, first.deleted) == (1, 0, 0, 0)
    assert (second.created, second.updated, second.unchanged, second.deleted) == (0, 0, 1, 0)
    assert first.documents[0].source_id == second.documents[0].source_id
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM folder_source_versions").fetchone() == (1,)
        assert connection.execute(
            "SELECT sensitivity FROM source_versions WHERE is_current = 1"
        ).fetchone() == ("local_only",)


def test_folder_import_updates_and_deactivates_deleted_source(tmp_path: Path) -> None:
    source_root = tmp_path / "knowledge"
    active = source_root / "active.md"
    retired = source_root / "retired.md"
    _write(active, "# Active\n\nThe active policy allows one attempt.")
    _write(retired, "# Retired\n\nThe retired policy allowed five attempts.")
    workspace = init_workspace(tmp_path / "workspace")
    first = sync_folder(workspace, source_root)
    source_ids = {document.relative_path: document.source_id for document in first.documents}

    _write(active, "# Active\n\nThe active policy allows two attempts.")
    retired.unlink()
    second = sync_folder(workspace, source_root)

    assert (second.created, second.updated, second.unchanged, second.deleted) == (0, 1, 0, 1)
    assert second.documents[0].source_id == source_ids["active.md"]
    assert search_sources(workspace, "retired policy") == []
    assert [result.source_id for result in search_sources(workspace, "two attempts")] == [
        source_ids["active.md"]
    ]
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM source_versions AS versions
            JOIN sources ON sources.id = versions.source_id
            WHERE sources.source_id = ?
            """,
            (source_ids["active.md"],),
        ).fetchone() == (2,)
        assert connection.execute(
            """
            SELECT COUNT(*) FROM source_versions AS versions
            JOIN sources ON sources.id = versions.source_id
            WHERE sources.source_id = ? AND versions.is_current = 1
            """,
            (source_ids["retired.md"],),
        ).fetchone() == (0,)


def test_folder_import_privacy_failure_has_no_partial_writes(tmp_path: Path) -> None:
    source_root = tmp_path / "knowledge"
    _write(source_root / "a-safe.md", "# Safe\n\nThis file sorts before the secret.")
    _write(
        source_root / "z-config.md",
        "AWS_SECRET_ACCESS_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789ABCD",
    )
    workspace = init_workspace(tmp_path / "workspace")

    with pytest.raises(SourceValidationError) as exc_info:
        sync_folder(workspace, source_root)

    assert str(source_root) not in str(exc_info.value)
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM folder_imports").fetchone() == (0,)


def test_folder_import_preserves_exact_citation_replay(tmp_path: Path) -> None:
    source_root = tmp_path / "knowledge"
    content = "# Cache Policy\n\nCache entries expire after sixty seconds."
    _write(source_root / "policies" / "cache.md", content)
    workspace = init_workspace(tmp_path / "workspace")
    synced = sync_folder(
        workspace,
        source_root,
        sensitivity=Sensitivity.PUBLIC,
    )
    source_id = synced.documents[0].source_id
    runner = CliRunner()

    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    changeset_id = json.loads(staged.stdout)["changeset_id"]
    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output
    answered = runner.invoke(
        app,
        [
            "ask",
            "When do cache entries expire?",
            "--verify",
            "--workspace",
            str(workspace),
        ],
    )

    assert answered.exit_code == 0, answered.output
    payload = json.loads(answered.stdout)
    assert payload["status"] == "answered"
    assert payload["citations"][0]["source_id"] == source_id
    assert payload["evidence"][0]["text"] == "Cache entries expire after sixty seconds."
    assert payload["citations"][0]["quote"] == payload["evidence"][0]["text"]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
