from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import memoryforge.github_thread_adapter as thread_adapter
import pytest
from memoryforge.github_thread_adapter import (
    GitHubThreadError,
    delete_github_thread,
    import_github_thread,
    import_github_thread_json,
    parse_github_thread_url,
)
from typer.testing import CliRunner

from memoryforge.cli import app
from tests.cli_helpers import review_approve_apply
from memoryforge.importer import SourceValidationError
from memoryforge.models import Sensitivity
from memoryforge.workspace import init_workspace, search_sources


@pytest.mark.parametrize("kind", ["issue", "pull"])
def test_github_thread_import_fetches_only_one_exact_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    path_kind = "issues" if kind == "issue" else "pull"
    source_url = f"https://github.com/octo/demo/{path_kind}/7"
    resource_endpoint = (
        "https://api.github.com/repos/octo/demo/issues/7"
        if kind == "issue"
        else "https://api.github.com/repos/octo/demo/pulls/7"
    )
    responses: dict[str, object] = {
        resource_endpoint: _api_resource(source_url),
        "https://api.github.com/repos/octo/demo/issues/7/comments?per_page=100": [
            _comment(
                2,
                "Second chronological comment.",
                "2025-01-03T00:00:00Z",
                f"{source_url}#issuecomment-2",
            ),
            _comment(
                1,
                "First chronological comment.",
                "2025-01-02T00:00:00Z",
                f"{source_url}#issuecomment-1",
            ),
        ],
    }
    if kind == "pull":
        responses["https://api.github.com/repos/octo/demo/pulls/7/reviews?per_page=100"] = [
            {
                "id": 3,
                "body": "Review body.",
                "submitted_at": "2025-01-04T00:00:00Z",
                "html_url": f"{source_url}#pullrequestreview-3",
                "user": {"login": "reviewer"},
            }
        ]
        responses["https://api.github.com/repos/octo/demo/pulls/7/comments?per_page=100"] = [
            _comment(
                4,
                "Inline review comment.",
                "2025-01-05T00:00:00Z",
                f"{source_url}#discussion_r4",
            )
        ]
    requested: list[str] = []

    def request_page(url: str) -> tuple[object, str | None]:
        requested.append(url)
        return responses[url], None

    monkeypatch.setattr(thread_adapter, "_request_json_page", request_page)
    workspace = init_workspace(tmp_path / "workspace")

    result = import_github_thread(workspace, source_url)

    assert result.status == "created"
    assert requested == list(responses)
    assert all("/repos/octo/demo/" in url for url in requested)
    assert all(not url.endswith(("/issues", "/pulls")) for url in requested)
    snapshot = (workspace / result.snapshot_path).read_text(encoding="utf-8")
    assert snapshot.index("First chronological comment.") < snapshot.index(
        "Second chronological comment."
    )
    assert source_url in snapshot
    assert f"{source_url}#issuecomment-1" in snapshot
    if kind == "pull":
        assert snapshot.index("Review body.") < snapshot.index("Inline review comment.")


def test_github_thread_saved_json_replays_offline_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = "https://github.com/octo/demo/issues/7"
    saved = tmp_path / "thread.json"
    saved.write_text(json.dumps(_snapshot(source_url), indent=2) + "\n", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")
    monkeypatch.setattr(
        thread_adapter,
        "_request_json_page",
        lambda _url: (_ for _ in ()).throw(AssertionError("offline import must not fetch")),
    )
    runner = CliRunner()

    first = runner.invoke(
        app,
        [
            "github-thread-import-json",
            str(saved),
            "--workspace",
            str(workspace),
        ],
    )
    second = import_github_thread_json(workspace, saved, source_root=tmp_path)

    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.stdout)
    assert first_payload["status"] == "created"
    assert second.status == "unchanged"
    assert str(saved) not in first.stdout
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (1,)


def test_github_thread_update_delete_and_reimport_lifecycle(tmp_path: Path) -> None:
    source_url = "https://github.com/octo/demo/issues/7"
    saved = tmp_path / "thread.json"
    payload = _snapshot(source_url)
    saved.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")
    first = import_github_thread_json(workspace, saved, source_root=tmp_path)

    payload["resource"]["body"] = "Updated thread body with a new retry policy."
    payload["resource"]["updated_at"] = "2025-02-01T00:00:00Z"
    saved.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    updated = import_github_thread_json(workspace, saved, source_root=tmp_path)
    deleted = delete_github_thread(workspace, source_url)

    assert updated.status == "updated"
    assert updated.source_id == first.source_id
    assert deleted.deleted is True
    assert deleted.source_id == first.source_id
    assert search_sources(workspace, "new retry policy") == []

    reimported = import_github_thread_json(workspace, saved, source_root=tmp_path)
    assert reimported.status == "updated"
    assert reimported.source_id == first.source_id
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (3,)
        assert connection.execute(
            "SELECT COUNT(*) FROM source_versions WHERE is_current = 1"
        ).fetchone() == (1,)


def test_github_thread_privacy_boundaries_fail_before_source_writes(
    tmp_path: Path,
) -> None:
    invalid = [
        "http://github.com/octo/demo/issues/7",
        "https://example.com/octo/demo/issues/7",
        "https://user@github.com/octo/demo/issues/7",
        "https://github.com/octo/demo/issues/0",
        "https://github.com/octo/demo/issues/7/comments",
        "https://github.com/octo/demo/issues/7?all=true",
    ]
    for value in invalid:
        with pytest.raises(GitHubThreadError):
            parse_github_thread_url(value)
    with pytest.raises(GitHubThreadError):
        thread_adapter._validate_api_url(
            "https://example.com/repos/octo/demo/issues/7/comments?page=2",
            expected_path="/repos/octo/demo/issues/7/comments",
        )

    source_url = "https://github.com/octo/demo/issues/7"
    payload = _snapshot(source_url)
    payload["resource"]["body"] = "AWS_SECRET_ACCESS_KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789ABCD"
    saved = tmp_path / "thread.json"
    saved.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")

    with pytest.raises(SourceValidationError):
        import_github_thread_json(workspace, saved, source_root=tmp_path)

    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone() == (0,)


def test_github_thread_preserves_locator_and_exact_citation_replay(tmp_path: Path) -> None:
    source_url = "https://github.com/octo/demo/issues/7"
    payload = _snapshot(source_url)
    payload["resource"]["body"] = "Cache entries expire after sixty seconds."
    saved = tmp_path / "thread.json"
    saved.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    workspace = init_workspace(tmp_path / "workspace")
    imported = import_github_thread_json(
        workspace,
        saved,
        source_root=tmp_path,
        sensitivity=Sensitivity.PUBLIC,
    )
    snapshot = (workspace / imported.snapshot_path).read_text(encoding="utf-8")
    assert source_url in snapshot
    assert f"{source_url}#issuecomment-11" in snapshot
    runner = CliRunner()

    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    changeset_id = json.loads(staged.stdout)["changeset_id"]
    assert review_approve_apply(runner, changeset_id, workspace).exit_code == 0
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
    answer = json.loads(answered.stdout)
    assert answer["status"] == "answered"
    assert answer["citations"][0]["source_id"] == imported.source_id
    assert answer["citations"][0]["quote"] == answer["evidence"][0]["text"]


def _snapshot(source_url: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source_url": source_url,
        "resource": _resource("issue", source_url),
        "contributions": [
            {
                "kind": "issue_comment",
                "id": "11",
                "author": "commenter",
                "body": "The rollout starts on Friday.",
                "created_at": "2025-01-02T00:00:00Z",
                "updated_at": "2025-01-02T00:00:00Z",
                "html_url": f"{source_url}#issuecomment-11",
            }
        ],
    }


def _resource(kind: str, source_url: str) -> dict[str, object]:
    return {
        "kind": kind,
        "owner": "octo",
        "repository": "demo",
        "number": 7,
        "title": "Cache rollout",
        "body": "The cache rollout uses a bounded policy.",
        "state": "open",
        "author": "author",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "html_url": source_url,
    }


def _api_resource(source_url: str) -> dict[str, object]:
    return {
        "id": 7,
        "number": 7,
        "title": "Cache rollout",
        "body": "The cache rollout uses a bounded policy.",
        "state": "open",
        "user": {"login": "author"},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "html_url": source_url,
    }


def _comment(identifier: int, body: str, created_at: str, html_url: str) -> dict[str, object]:
    return {
        "id": identifier,
        "body": body,
        "created_at": created_at,
        "updated_at": created_at,
        "html_url": html_url,
        "user": {"login": "commenter"},
    }
