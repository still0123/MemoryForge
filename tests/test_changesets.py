from __future__ import annotations

from pathlib import Path

import pytest

from memoryforge.changesets import ChangeSetStore
from memoryforge.errors import ChangeSetStoreError
from memoryforge.models import ChangeOperation, ChangeOperationType, ChangeSet, ChangeSetStatus
from memoryforge.workspace import Workspace


def test_changeset_staging_is_idempotent_and_leaves_stable_wiki_untouched(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    changeset = _page_changeset(workspace)
    candidates = {
        "wiki/adrs/cache-key.md": "# Cache key\n\nUse a namespaced hash key.\n",
    }
    store = ChangeSetStore(workspace)

    first = store.create(changeset, candidates)
    repeated = store.create(changeset, candidates)

    assert first.record == repeated.record
    assert first.changeset == changeset
    assert first.candidate_files == candidates
    assert (first.directory / "changeset.json").is_file()
    assert (
        first.directory / "proposed" / "wiki" / "adrs" / "cache-key.md"
    ).read_text(encoding="utf-8") == candidates["wiki/adrs/cache-key.md"]
    assert not (workspace.wiki_dir / "adrs" / "cache-key.md").exists()
    assert [stored.changeset.changeset_id for stored in store.list_all()] == ["chg_cache_key_v1"]


def test_changeset_staging_rejects_stale_base_commit(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    stale = _page_changeset(workspace, base_commit="0" * 40)

    with pytest.raises(ChangeSetStoreError, match="base_commit"):
        ChangeSetStore(workspace).create(
            stale,
            {"wiki/adrs/cache-key.md": "# Cache key\n"},
        )


def test_changeset_store_detects_candidate_tampering(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    store = ChangeSetStore(workspace)
    stored = store.create(
        _page_changeset(workspace),
        {"wiki/adrs/cache-key.md": "# Cache key\n"},
    )
    candidate = stored.directory / "proposed" / "wiki" / "adrs" / "cache-key.md"
    candidate.write_text("# Tampered\n", encoding="utf-8")

    with pytest.raises(ChangeSetStoreError, match="hash"):
        store.get("chg_cache_key_v1")


def test_changeset_staging_requires_a_matching_page_operation(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")
    changeset = ChangeSet(
        changeset_id="chg_unlinked_file",
        base_commit=workspace.current_commit(),
        status=ChangeSetStatus.PROPOSED,
    )

    with pytest.raises(ChangeSetStoreError, match="lack a create/update operation"):
        ChangeSetStore(workspace).create(
            changeset,
            {"wiki/adrs/cache-key.md": "# Cache key\n"},
        )


def test_changeset_staging_requires_candidate_for_each_page_write(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "wiki")

    with pytest.raises(ChangeSetStoreError, match="lack a candidate file"):
        ChangeSetStore(workspace).create(_page_changeset(workspace), {})


def _page_changeset(workspace: Workspace, base_commit: str | None = None) -> ChangeSet:
    """Build a minimal page proposal against the current workspace revision."""

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
