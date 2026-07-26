from __future__ import annotations

from pathlib import Path

import pytest

from memoryforge.changesets import ChangeSetStore
from memoryforge.errors import ChangeSetStoreError
from memoryforge.models import ChangeOperation, ChangeOperationType, ChangeSet, ChangeSetStatus
from memoryforge.workspace import Workspace, WorkspaceSecurityError


def test_changeset_staging_is_idempotent_and_keeps_stable_wiki_untouched(
    tmp_path: Path,
) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    changeset = _page_changeset(workspace)
    candidates = {"wiki/adrs/cache-key.md": "# Cache key\n\nUse a namespaced hash key.\n"}
    store = ChangeSetStore(workspace)

    first = store.create(changeset, candidates)
    repeated = store.create(changeset, candidates)

    assert first.record == repeated.record
    assert first.candidate_files == candidates
    assert not (workspace.wiki_dir / "adrs" / "cache-key.md").exists()
    assert [stored.changeset.changeset_id for stored in store.list_all()] == ["chg_cache_key_v1"]


def test_changeset_staging_rejects_stale_base_commit(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")

    with pytest.raises(ChangeSetStoreError, match="base_commit"):
        ChangeSetStore(workspace).create(
            _page_changeset(workspace, base_commit="0" * 40),
            {"wiki/adrs/cache-key.md": "# Cache key\n"},
        )


def test_changeset_load_and_listing_reject_stale_proposals(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    store = ChangeSetStore(workspace)
    store.create(
        _page_changeset(workspace),
        {"wiki/adrs/cache-key.md": "# Cache key\n"},
    )
    (workspace.root / "wiki/INDEX.md").write_text("# Advanced\n", encoding="utf-8")
    _git(workspace.root, "add", "wiki/INDEX.md")
    _git(workspace.root, "commit", "-m", "test: advance head")

    with pytest.raises(ChangeSetStoreError, match="base_commit"):
        store.get("chg_cache_key_v1")
    with pytest.raises(ChangeSetStoreError, match="base_commit"):
        store.list_all()


def test_changeset_store_detects_candidate_tampering(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    store = ChangeSetStore(workspace)
    stored = store.create(
        _page_changeset(workspace),
        {"wiki/adrs/cache-key.md": "# Cache key\n"},
    )
    candidate = stored.directory / "proposed/wiki/adrs/cache-key.md"
    candidate.write_text("# Tampered\n", encoding="utf-8")

    with pytest.raises(ChangeSetStoreError, match="hash"):
        store.get("chg_cache_key_v1")


def test_changeset_staging_requires_operation_candidate_bijection(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    store = ChangeSetStore(workspace)
    no_operations = ChangeSet(
        changeset_id="chg_unlinked",
        base_commit=workspace.current_commit(),
        status=ChangeSetStatus.PROPOSED,
    )

    with pytest.raises(ChangeSetStoreError, match="lack a create/update operation"):
        store.create(no_operations, {"wiki/adrs/cache-key.md": "# Cache key\n"})
    with pytest.raises(ChangeSetStoreError, match="lack a candidate file"):
        store.create(_page_changeset(workspace), {})


def test_changeset_store_rejects_staging_symlink_replacement(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.staging_dir.rmdir()
    workspace.staging_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceSecurityError, match="symbolic link"):
        ChangeSetStore(workspace).create(
            _page_changeset(workspace),
            {"wiki/adrs/cache-key.md": "# Cache key\n"},
        )


def _page_changeset(workspace: Workspace, base_commit: str | None = None) -> ChangeSet:
    return ChangeSet(
        changeset_id="chg_cache_key_v1",
        base_commit=base_commit or workspace.current_commit(),
        status=ChangeSetStatus.PROPOSED,
        operations=(
            ChangeOperation(
                type=ChangeOperationType.CREATE_PAGE,
                path="wiki/adrs/cache-key.md",
            ),
        ),
    )


def _git(root: Path, *arguments: str) -> str:
    import subprocess

    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.name=MemoryForge Tests",
            "-c",
            "user.email=memoryforge-tests@example.invalid",
            "-C",
            str(root),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
