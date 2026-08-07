from __future__ import annotations

import stat
from pathlib import Path

import memoryforge.github_thread_adapter as thread_adapter
from memoryforge.github_thread_adapter import (
    GitHubThreadSnapshot,
    import_github_thread,
    import_github_thread_json,
)
from memoryforge.workspace import init_workspace


def test_github_thread_save_json_is_private_and_replayable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_url = "https://github.com/octo/demo/issues/7"
    snapshot = GitHubThreadSnapshot.model_validate(
        {
            "schema_version": 1,
            "source_url": source_url,
            "resource": {
                "kind": "issue",
                "owner": "octo",
                "repository": "demo",
                "number": 7,
                "title": "Cache rollout",
                "body": "Cache entries expire after sixty seconds.",
                "state": "open",
                "author": "author",
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z",
                "html_url": source_url,
            },
            "contributions": [],
        }
    )
    monkeypatch.setattr(thread_adapter, "_fetch_github_snapshot", lambda _identity: snapshot)
    saved = tmp_path / "thread.json"
    first_workspace = init_workspace(tmp_path / "first-workspace")
    second_workspace = init_workspace(tmp_path / "second-workspace")

    fetched = import_github_thread(first_workspace, source_url, save_json=saved)
    replayed = import_github_thread_json(second_workspace, saved, source_root=tmp_path)

    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert fetched.source_id == replayed.source_id
    assert fetched.content_sha256 == replayed.content_sha256
