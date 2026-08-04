from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

import memoryforge.workspace as workspace_module
from memoryforge.importer import import_local_file
from memoryforge.workspace import (
    Workspace,
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    init_workspace,
    search_sources,
)


def test_init_workspace_creates_minimal_directories_and_fts_schema(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path / "knowledge")

    assert (workspace / "raw").is_dir()
    assert (workspace / "wiki").is_dir()
    assert (workspace / ".memoryforge").is_dir()
    assert (workspace / ".memoryforge/index.sqlite").is_file()
    assert (workspace / ".gitignore").read_text(encoding="utf-8") == (
        "/raw/\n"
        "/.memoryforge/index.sqlite*\n"
        "/.memoryforge/manifests/\n"
        "/.memoryforge/staging/\n"
        "/.memoryforge/workspace.lock\n"
        "/.memoryforge/rejected/\n"
            "/.memoryforge/traces/\n"
            "/.memoryforge/vectors/\n"
            "/.memoryforge/sessions/\n"
        )
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / "raw").stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / "wiki").stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / ".memoryforge").stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / ".memoryforge/index.sqlite").stat().st_mode) == 0o600


def test_search_opens_workspace_without_running_upgrade_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "notes.md"
    source.write_text("# Cache\n\nCache entries expire after sixty seconds.", encoding="utf-8")
    workspace = init_workspace(tmp_path / "knowledge")
    import_local_file(workspace, source, source_root=source_root)

    def reject_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only search must not upgrade the workspace")

    monkeypatch.setattr(workspace_module, "_upgrade_workspace_contract", reject_write)
    monkeypatch.setattr(workspace_module, "_migrate_database", reject_write)
    monkeypatch.setattr(workspace_module, "_backfill_source_manifests", reject_write)

    assert search_sources(workspace, "cache")


def test_workspace_exclusive_lock_blocks_a_second_writer(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "knowledge")

    with workspace.exclusive_lock():
        descriptor = os.open(workspace.internal_dir / "workspace.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)


def test_init_workspace_rejects_workspace_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real-workspace"
    target.mkdir()
    workspace = tmp_path / "linked-workspace"
    workspace.symlink_to(target, target_is_directory=True)

    with pytest.raises(WorkspaceSecurityError, match="symbolic link"):
        init_workspace(workspace)


@pytest.mark.skipif(
    not Path("/var").is_symlink() or Path("/var").resolve() != Path("/private/var"),
    reason="macOS /var alias is not present",
)
def test_init_workspace_allows_fixed_macos_system_alias(tmp_path: Path) -> None:
    alias_path = Path("/var") / tmp_path.relative_to("/private/var") / "alias-workspace"

    workspace = init_workspace(alias_path)

    assert workspace == alias_path
    assert workspace.is_dir()


@pytest.mark.parametrize("component", ["raw", "wiki", ".memoryforge"])
def test_init_workspace_rejects_managed_directory_symlink(
    tmp_path: Path,
    component: str,
) -> None:
    workspace = tmp_path / "knowledge"
    workspace.mkdir()
    outside = tmp_path / f"outside-{component.removeprefix('.')}"
    outside.mkdir()
    (workspace / component).symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceSecurityError, match="symbolic link"):
        init_workspace(workspace)


def test_search_sources_returns_title_uri_snippet_and_hash(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "cache-design.md"
    source.write_text(
        "# Public cache design\n\nThe cache uses a versioned namespace for migrations.",
        encoding="utf-8",
    )
    workspace = init_workspace(tmp_path / "knowledge")
    imported = import_local_file(workspace, source, source_root=source_root)

    results = search_sources(workspace, "versioned namespace")

    assert len(results) == 1
    assert results[0].title == "Public cache design"
    assert results[0].source_id == imported.source_id
    assert results[0].source_uri == f"mf://source/{imported.source_id}"
    assert results[0].source_path == "cache-design.md"
    assert results[0].snapshot_uri == f"mf://blob/{imported.content_sha256}"
    assert results[0].snapshot_path == imported.snapshot_path
    assert results[0].category == "notes"
    assert "[versioned]" in results[0].snippet
    assert results[0].content_sha256 == imported.content_sha256


@pytest.mark.parametrize("query", ["缓", "缓存", "缓存键"])
def test_search_sources_supports_chinese_partial_terms(tmp_path: Path, query: str) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "cache-design.md"
    source.write_text("# 缓存设计\n\n缓存键使用版本命名空间。", encoding="utf-8")
    workspace = init_workspace(tmp_path / "knowledge")
    import_local_file(workspace, source, source_root=source_root)

    results = search_sources(workspace, query)

    assert [result.title for result in results] == ["缓存设计"]


def test_search_sources_only_returns_current_version_with_immutable_uri(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "design.md"
    source.write_text("# Design\n\nobsolete cache policy", encoding="utf-8")
    workspace = init_workspace(tmp_path / "knowledge")
    first = import_local_file(workspace, source, source_root=source_root)

    source.write_text("# Design\n\ncurrent versioned namespace", encoding="utf-8")
    second = import_local_file(workspace, source, source_root=source_root)

    assert second.status == "updated"
    assert search_sources(workspace, "obsolete") == []
    current = search_sources(workspace, "current versioned")
    assert len(current) == 1
    assert current[0].source_uri == f"mf://source/{second.source_id}"
    assert current[0].snapshot_uri == f"mf://blob/{second.content_sha256}"
    assert (
        (workspace / first.snapshot_path)
        .read_text(encoding="utf-8")
        .endswith("obsolete cache policy")
    )
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM source_versions WHERE is_current = 1"
        ).fetchone() == (1,)


def test_source_can_return_to_a_previous_blob_as_a_new_version(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "policy.md"
    workspace = init_workspace(tmp_path / "knowledge")

    source.write_text("# Policy\n\nversion alpha", encoding="utf-8")
    alpha = import_local_file(workspace, source, source_root=source_root)
    source.write_text("# Policy\n\nversion beta", encoding="utf-8")
    import_local_file(workspace, source, source_root=source_root)
    source.write_text("# Policy\n\nversion alpha", encoding="utf-8")
    reverted = import_local_file(workspace, source, source_root=source_root)

    assert reverted.status == "updated"
    assert reverted.snapshot_path == alpha.snapshot_path
    assert search_sources(workspace, "beta") == []
    assert [result.title for result in search_sources(workspace, "alpha")] == ["Policy"]
    with closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (3,)
        assert connection.execute("SELECT COUNT(*) FROM blobs").fetchone() == (2,)


def test_search_sources_rejects_tampered_snapshot(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    source = source_root / "evidence.md"
    source.write_text("# Evidence\n\ntrusted searchable statement", encoding="utf-8")
    workspace = init_workspace(tmp_path / "knowledge")
    imported = import_local_file(workspace, source, source_root=source_root)
    (workspace / imported.snapshot_path).write_text(
        "# Evidence\n\ntampered searchable statement",
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceIntegrityError, match="integrity"):
        search_sources(workspace, "searchable")


def test_search_sources_distinguishes_sources_that_share_a_blob(tmp_path: Path) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    first_source = source_root / "first.md"
    second_source = source_root / "second.md"
    content = "# Shared\n\nsame searchable evidence"
    first_source.write_text(content, encoding="utf-8")
    second_source.write_text(content, encoding="utf-8")
    workspace = init_workspace(tmp_path / "knowledge")
    first = import_local_file(
        workspace,
        first_source,
        category="design",
        source_root=source_root,
    )
    second = import_local_file(
        workspace,
        second_source,
        category="postmortem",
        source_root=source_root,
    )

    results = search_sources(workspace, "searchable")

    assert {result.source_id for result in results} == {first.source_id, second.source_id}
    assert {result.source_uri for result in results} == {
        f"mf://source/{first.source_id}",
        f"mf://source/{second.source_id}",
    }
    assert {result.category for result in results} == {"design", "postmortem"}
    assert {result.source_path for result in results} == {"first.md", "second.md"}
    assert len({result.snapshot_uri for result in results}) == 1
    assert len({result.snapshot_path for result in results}) == 1
