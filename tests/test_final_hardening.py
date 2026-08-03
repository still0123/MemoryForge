from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from memoryforge.changesets import ChangeSetStore
from memoryforge.cli import app
from memoryforge.errors import ChangeSetStoreError, WorkspaceError
from memoryforge.importer import import_local_file
from memoryforge.manifests import SourceManifestStore
from memoryforge.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    SourceCategory,
    SourceVersionManifest,
)
from memoryforge.version_store import GitVersionStore
from memoryforge.workspace import Workspace, WorkspaceIntegrityError, search_sources


def test_workspace_open_rejects_non_workspace_before_writing(tmp_path: Path) -> None:
    root = tmp_path / "not-a-workspace"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("unchanged\n", encoding="utf-8")

    with pytest.raises((FileNotFoundError, WorkspaceError), match="initialized|workspace"):
        Workspace.open(root)

    assert sorted(path.name for path in root.iterdir()) == ["keep.txt"]
    assert marker.read_text(encoding="utf-8") == "unchanged\n"


def test_search_rejects_an_incomplete_workspace_without_upgrading_it(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "legacy.md"
    source.write_text("# Legacy\n\nsearch upgrade evidence\n", encoding="utf-8")
    root = tmp_path / "workspace"
    workspace = Workspace.initialize(root)
    import_local_file(root, source, source_root=source_root)
    shutil.rmtree(root / ".git")
    (root / ".memoryforge/config.yaml").unlink()
    (root / ".memoryforge/schema.yaml").unlink()
    shutil.rmtree(workspace.manifest_dir)

    with pytest.raises(WorkspaceError, match="configuration is missing"):
        search_sources(root, "upgrade evidence")

    assert not (root / ".git").exists()
    assert not (root / ".memoryforge/config.yaml").exists()
    assert not (root / ".memoryforge/schema.yaml").exists()
    assert not workspace.manifest_dir.exists()


def test_open_existing_repository_never_commits_contract_or_staged_work(
    tmp_path: Path,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    agents = workspace.root / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\nuser draft\n", encoding="utf-8")
    _git(workspace.root, "rm", "--cached", ".memoryforge/config.yaml")
    _git(workspace.root, "commit", "-m", "test: remove tracked contract")
    unrelated = workspace.root / "unrelated.md"
    unrelated.write_text("private draft\n", encoding="utf-8")
    _git(workspace.root, "add", "unrelated.md")
    original_head = _git(workspace.root, "rev-parse", "HEAD")

    Workspace.open(workspace.root)

    assert _git(workspace.root, "rev-parse", "HEAD") == original_head
    assert "user draft" in agents.read_text(encoding="utf-8")
    assert (
        ".memoryforge/config.yaml"
        not in _git(
            workspace.root,
            "ls-tree",
            "-r",
            "--name-only",
            "HEAD",
        ).splitlines()
    )
    assert _git(workspace.root, "diff", "--cached", "--name-only").splitlines() == ["unrelated.md"]


def test_git_environment_redirection_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_index = outside / "index"
    monkeypatch.setenv("GIT_DIR", str(outside / "git-dir"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outside))
    monkeypatch.setenv("GIT_INDEX_FILE", str(outside_index))

    assert Workspace.open(workspace.root).current_commit() == workspace.current_commit()
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("metadata_directory", ["objects", "refs"])
def test_git_metadata_directory_symlink_is_rejected_without_external_write(
    tmp_path: Path,
    metadata_directory: str,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    target = workspace.root / ".git" / metadata_directory
    backup = workspace.root / ".git" / f"{metadata_directory}.backup"
    target.rename(backup)
    outside = tmp_path / "outside"
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError, match=metadata_directory):
        Workspace.open(workspace.root)

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("nested_directory", ["refs/heads", "objects/info"])
def test_nested_git_metadata_symlink_is_rejected_without_external_write(
    tmp_path: Path,
    nested_directory: str,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    target = workspace.root / ".git" / nested_directory
    backup = target.with_name(target.name + ".backup")
    target.rename(backup)
    outside = tmp_path / "outside"
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError, match="symbolic|symlink|metadata"):
        Workspace.open(workspace.root)

    assert list(outside.iterdir()) == []


def test_workspace_upgrade_disables_repository_hooks(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    _git(workspace.root, "rm", "--cached", ".memoryforge/config.yaml")
    _git(workspace.root, "commit", "-m", "test: remove tracked contract")
    marker = tmp_path / "hook-ran"
    hook = workspace.root / ".git/hooks/post-commit"
    hook.write_text(
        f"#!/bin/sh\nprintf triggered > '{marker.as_posix()}'\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)
    original_head = _git(workspace.root, "rev-parse", "HEAD")

    Workspace.open(workspace.root)

    assert not marker.exists()
    assert _git(workspace.root, "rev-parse", "HEAD") == original_head
    assert (
        ".memoryforge/config.yaml"
        not in _git(
            workspace.root,
            "ls-tree",
            "-r",
            "--name-only",
            "HEAD",
        ).splitlines()
    )


def test_workspace_open_does_not_invoke_existing_repository_clean_filter(
    tmp_path: Path,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    marker = tmp_path / "filter-ran"
    filter_script = tmp_path / "clean-filter.sh"
    filter_script.write_text(
        f"#!/bin/sh\nprintf triggered > '{marker.as_posix()}'\ncat\n",
        encoding="utf-8",
    )
    filter_script.chmod(0o700)
    (workspace.root / ".gitattributes").write_text(
        ".memoryforge/config.yaml filter=tripwire\n",
        encoding="utf-8",
    )
    _git(workspace.root, "add", ".gitattributes")
    _git(workspace.root, "commit", "-m", "test: configure attributes")
    _git(workspace.root, "config", "filter.tripwire.clean", filter_script.as_posix())
    _git(workspace.root, "rm", "--cached", ".memoryforge/config.yaml")
    _git(workspace.root, "commit", "-m", "test: remove tracked contract")
    original_head = _git(workspace.root, "rev-parse", "HEAD")
    marker.unlink(missing_ok=True)

    Workspace.open(workspace.root)

    assert not marker.exists()
    assert _git(workspace.root, "rev-parse", "HEAD") == original_head
    assert _git(workspace.root, "diff", "--cached", "--name-only") == ""
    assert (workspace.root / ".memoryforge/config.yaml").is_file()


def test_existing_repository_without_head_requires_manual_baseline(
    tmp_path: Path,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    shutil.rmtree(workspace.root / ".git")
    _git(workspace.root, "init", "--quiet", "--initial-branch=main")

    with pytest.raises(WorkspaceError, match="HEAD|manual|commit"):
        Workspace.open(workspace.root)

    completed = subprocess.run(
        ["git", "-C", str(workspace.root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0


def test_git_baseline_requires_repository_created_by_same_store(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")

    with pytest.raises(WorkspaceError, match="new|created|controlled"):
        GitVersionStore(workspace.root).ensure_baseline((".gitignore",))


def test_idempotent_changeset_retry_rejects_a_stale_base(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    changeset = _changeset(workspace)
    candidates = {"wiki/adrs/cache.md": "# Cache\n"}
    store = ChangeSetStore(workspace)
    store.create(changeset, candidates)
    (workspace.root / "wiki/INDEX.md").write_text("# Changed\n", encoding="utf-8")
    _git(workspace.root, "add", "wiki/INDEX.md")
    _git(workspace.root, "commit", "-m", "test: advance head")

    with pytest.raises(ChangeSetStoreError, match="base_commit"):
        store.create(changeset, candidates)


def test_review_rejects_a_stale_staged_changeset(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    changeset = _changeset(workspace)
    ChangeSetStore(workspace).create(changeset, {"wiki/adrs/cache.md": "# Cache\n"})
    (workspace.root / "wiki/INDEX.md").write_text("# Advanced\n", encoding="utf-8")
    _git(workspace.root, "add", "wiki/INDEX.md")
    _git(workspace.root, "commit", "-m", "test: advance head")

    result = CliRunner().invoke(
        app,
        ["review", changeset.changeset_id, "--workspace", str(workspace.root)],
    )

    assert result.exit_code == 1
    assert "base_commit" in result.output
    assert "not enabled" not in result.output


def test_changeset_rechecks_head_immediately_before_publish(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    changeset = _changeset(workspace)

    class HeadAdvancingCandidates(dict[str, str]):
        advanced = False

        def items(self):  # type: ignore[no-untyped-def]
            if not self.advanced:
                self.advanced = True
                (workspace.root / "wiki/INDEX.md").write_text("# Advanced\n", encoding="utf-8")
                _git(workspace.root, "add", "wiki/INDEX.md")
                _git(workspace.root, "commit", "-m", "test: advance during staging")
            return super().items()

    with pytest.raises(ChangeSetStoreError, match="base_commit"):
        ChangeSetStore(workspace).create(
            changeset,
            HeadAdvancingCandidates({"wiki/adrs/cache.md": "# Cache\n"}),
        )

    assert list(workspace.staging_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (WorkspaceIntegrityError("tampered evidence"), "workspace integrity check failed"),
        (sqlite3.OperationalError("broken database"), "workspace operation failed safely"),
    ],
)
def test_future_cli_commands_have_a_stable_workspace_error_boundary(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    message: str,
) -> None:
    def fail_open(_: Path) -> Workspace:
        raise failure

    monkeypatch.setattr("memoryforge.cli.Workspace.open_readonly", fail_open)
    result = CliRunner().invoke(app, ["ask", "question", "--workspace", "unused"])

    assert result.exit_code == 1
    assert message in result.output
    assert "Traceback" not in result.output


def test_changeset_rejects_conflicting_operations_for_one_path() -> None:
    with pytest.raises(ValidationError, match="conflict"):
        ChangeSet(
            changeset_id="chg_conflict",
            base_commit="a" * 40,
            status=ChangeSetStatus.PROPOSED,
            operations=(
                ChangeOperation(
                    type=ChangeOperationType.CREATE_PAGE,
                    path="wiki/adrs/cache.md",
                ),
                ChangeOperation(
                    type=ChangeOperationType.UPDATE_PAGE,
                    path="wiki/adrs/cache.md",
                ),
            ),
        )


def test_manifest_listing_cleans_owned_crash_temp_but_rejects_unknown_entries(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "evidence.md"
    source.write_text("# Evidence\n", encoding="utf-8")
    workspace = Workspace.initialize(tmp_path / "workspace")
    import_local_file(workspace.root, source, source_root=source_root)
    store = SourceManifestStore(workspace.manifest_dir)
    owned_temp = workspace.manifest_dir / (f".{'a' * 64}--{'b' * 64}.json.{'c' * 32}.tmp")
    owned_temp.write_text("partial", encoding="utf-8")

    assert len(store.list_all()) == 1
    assert not owned_temp.exists()

    (workspace.manifest_dir / "unknown.tmp").write_text("partial", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="Unexpected"):
        store.list_all()


def test_manifest_listing_rejects_self_consistent_but_nonexistent_evidence(
    tmp_path: Path,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    content_sha256 = hashlib.sha256(b"missing").hexdigest()
    source_id = hashlib.sha256(b"missing-source").hexdigest()
    manifest = SourceVersionManifest(
        source_id=source_id,
        source_uri=f"mf://source/{source_id}",
        source_path="missing.md",
        content_sha256=content_sha256,
        snapshot_uri=f"mf://blob/{content_sha256}",
        snapshot_path=f"raw/blobs/{content_sha256[:2]}/{content_sha256}.blob",
        media_type="text/markdown",
        category=SourceCategory.NOTES,
        title="Missing",
        observed_at=datetime.now(UTC),
    )
    store = SourceManifestStore(workspace.manifest_dir)
    store.write(manifest)

    with pytest.raises(WorkspaceError, match="SourceVersion and Blob"):
        store.list_all()


def test_manifest_model_binds_snapshot_uri_to_content_hash() -> None:
    content_sha256 = hashlib.sha256(b"evidence").hexdigest()
    source_id = hashlib.sha256(b"source").hexdigest()

    with pytest.raises(ValidationError, match="snapshot_uri"):
        SourceVersionManifest(
            source_id=source_id,
            source_uri=f"mf://source/{source_id}",
            source_path="evidence.md",
            content_sha256=content_sha256,
            snapshot_uri=f"mf://blob/{'f' * 64}",
            snapshot_path=f"raw/blobs/{content_sha256[:2]}/{content_sha256}.blob",
            media_type="text/markdown",
            category=SourceCategory.NOTES,
            title="Evidence",
            observed_at=datetime.now(UTC),
        )


def test_manifest_listing_rejects_metadata_not_bound_to_source_version(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "evidence.md"
    source.write_text("# Evidence\n", encoding="utf-8")
    workspace = Workspace.initialize(tmp_path / "workspace")
    import_local_file(workspace.root, source, source_root=source_root)
    store = SourceManifestStore(workspace.manifest_dir)
    manifest = store.list_all()[0]
    store.write(manifest.model_copy(update={"title": "Forged title"}))

    with pytest.raises(WorkspaceError, match="SourceVersion and Blob"):
        store.list_all()


def test_search_does_not_rehash_historical_blobs_with_existing_manifests(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "evidence.md"
    source.write_text("# V1\n\nobsolete historical phrase\n", encoding="utf-8")
    workspace = Workspace.initialize(tmp_path / "workspace")
    first = import_local_file(workspace.root, source, source_root=source_root)
    source.write_text("# V2\n\ncurrent searchable phrase\n", encoding="utf-8")
    second = import_local_file(workspace.root, source, source_root=source_root)
    historical_blob = workspace.root / first.snapshot_path
    historical_blob.write_text("tampered historical content\n", encoding="utf-8")

    results = search_sources(workspace.root, "current searchable")

    assert [result.content_sha256 for result in results] == [second.content_sha256]
    with pytest.raises(WorkspaceError, match="integrity"):
        SourceManifestStore(workspace.manifest_dir).list_all()


def test_manifest_backfill_retries_only_missing_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "evidence.md"
    source.write_text("# V1\n", encoding="utf-8")
    workspace = Workspace.initialize(tmp_path / "workspace")
    import_local_file(workspace.root, source, source_root=source_root)
    source.write_text("# V2\n", encoding="utf-8")
    import_local_file(workspace.root, source, source_root=source_root)
    for manifest_path in workspace.manifest_dir.iterdir():
        manifest_path.unlink()

    original_write = SourceManifestStore.write
    writes = 0

    def fail_after_first(
        store: SourceManifestStore,
        manifest: SourceVersionManifest,
    ) -> Path:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise WorkspaceError("simulated manifest publication failure")
        return original_write(store, manifest)

    with monkeypatch.context() as patch:
        patch.setattr(SourceManifestStore, "write", fail_after_first)
        with pytest.raises(WorkspaceError, match="simulated"):
            Workspace.open(workspace.root)

    assert len(list(workspace.manifest_dir.iterdir())) == 1
    reopened = Workspace.open(workspace.root)
    assert len(SourceManifestStore(reopened.manifest_dir).list_all()) == 2


def _changeset(workspace: Workspace) -> ChangeSet:
    return ChangeSet(
        changeset_id="chg_final_hardening",
        base_commit=workspace.current_commit(),
        status=ChangeSetStatus.PROPOSED,
        operations=(
            ChangeOperation(
                type=ChangeOperationType.CREATE_PAGE,
                path="wiki/adrs/cache.md",
            ),
        ),
    )


def _git(root: Path, *arguments: str) -> str:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
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
        env=environment,
    )
    return completed.stdout.strip()
