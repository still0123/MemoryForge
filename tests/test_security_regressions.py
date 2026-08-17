from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from memoryforge.adapters.importer import SourceValidationError, import_local_file
from memoryforge.core.errors import ChangeSetStoreError, WorkspaceError
from memoryforge.core.manifests import SourceManifestStore
from memoryforge.core.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    Citation,
)
from memoryforge.storage.changesets import ChangeSetStore
from memoryforge.storage.workspace import Workspace, WorkspaceSecurityError


def test_manifest_parent_symlink_cannot_escape_workspace(tmp_path: Path) -> None:
    source_root, source = _source(tmp_path)
    workspace = Workspace.initialize(tmp_path / "workspace")
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.manifest_dir.rmdir()
    workspace.manifest_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises((WorkspaceError, WorkspaceSecurityError), match="symbolic link|unsafe"):
        import_local_file(workspace.root, source, source_root=source_root)

    assert list(outside.iterdir()) == []


def test_manifest_tampering_and_filename_identity_mismatch_are_rejected(
    tmp_path: Path,
) -> None:
    source_root, source = _source(tmp_path)
    workspace = Workspace.initialize(tmp_path / "workspace")
    imported = import_local_file(workspace.root, source, source_root=source_root)
    manifest_path = next(workspace.manifest_dir.iterdir())
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["title"] = "tampered"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkspaceError, match="integrity"):
        SourceManifestStore(workspace.manifest_dir).list_all()

    manifest_path.unlink()
    payload["title"] = "Evidence"
    encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(encoded).hexdigest()
    mismatched = workspace.manifest_dir / f"{'0' * 64}--{digest}.json"
    mismatched.write_bytes(encoded)
    with pytest.raises(WorkspaceError, match="identity"):
        SourceManifestStore(workspace.manifest_dir).list_all()
    assert imported.source_id != "0" * 64


def test_changeset_rejects_parent_symlink_and_metadata_tampering(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    store = ChangeSetStore(workspace)
    stored = store.create(
        _changeset(workspace),
        {"wiki/adrs/cache.md": "# Cache\n"},
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "cache.md"
    outside_file.write_text("# forged\n", encoding="utf-8")
    adrs = stored.directory / "proposed/wiki/adrs"
    (adrs / "cache.md").unlink()
    adrs.rmdir()
    adrs.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ChangeSetStoreError, match="directory chain"):
        store.get("chg_security")

    adrs.unlink()
    adrs.mkdir()
    (adrs / "cache.md").write_text("# Cache\n", encoding="utf-8")
    metadata_path = stored.directory / "changeset.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["changeset"]["status"] = "APPROVED"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ChangeSetStoreError, match="integrity"):
        store.get("chg_security")


def test_changeset_create_only_accepts_proposed_and_publishes_no_temps(
    tmp_path: Path,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    approved = _changeset(workspace).model_copy(update={"status": ChangeSetStatus.APPROVED})
    store = ChangeSetStore(workspace)
    with pytest.raises(ChangeSetStoreError, match="PROPOSED"):
        store.create(approved, {"wiki/adrs/cache.md": "# Cache\n"})
    store.create(_changeset(workspace), {"wiki/adrs/cache.md": "# Cache\n"})
    assert (workspace.staging_dir / "chg_security/changeset.sha256").is_file()
    assert not any(path.name.endswith(".tmp") for path in workspace.staging_dir.iterdir())


def test_legacy_phase1a_database_is_migrated_and_import_remains_usable(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "legacy"
    (workspace_root / "raw").mkdir(parents=True)
    (workspace_root / "wiki").mkdir()
    internal = workspace_root / ".memoryforge"
    internal.mkdir()
    database = internal / "index.sqlite"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(_LEGACY_SCHEMA)
    source_root, source = _source(tmp_path)

    imported = import_local_file(workspace_root, source, source_root=source_root)

    assert imported.status == "created"
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(source_versions)")}
    assert {"sensitivity", "tags_json"} <= columns
    assert (workspace_root / ".git").is_dir()
    assert (internal / "config.yaml").is_file()


def test_memoryforgeignore_excludes_before_read_and_rejects_symlink(
    tmp_path: Path,
) -> None:
    source_root, source = _source(tmp_path)
    workspace = Workspace.initialize(tmp_path / "workspace")
    (source_root / ".memoryforgeignore").write_text("*.md\n", encoding="utf-8")
    with pytest.raises(SourceValidationError, match="excluded"):
        import_local_file(workspace.root, source, source_root=source_root)

    (source_root / ".memoryforgeignore").unlink()
    outside = tmp_path / "ignore"
    outside.write_text("", encoding="utf-8")
    (source_root / ".memoryforgeignore").symlink_to(outside)
    with pytest.raises(SourceValidationError, match="opened safely"):
        import_local_file(workspace.root, source, source_root=source_root)


def test_category_and_citation_are_bound_to_stable_schema_and_source_version(
    tmp_path: Path,
) -> None:
    source_root, source = _source(tmp_path)
    workspace = Workspace.initialize(tmp_path / "workspace")
    with pytest.raises(SourceValidationError, match="must be one of"):
        import_local_file(
            workspace.root,
            source,
            source_root=source_root,
            category="custom",
        )
    with pytest.raises(ValidationError, match="snapshot_uri"):
        Citation(
            source_id="a" * 64,
            content_sha256="b" * 64,
            snapshot_uri=f"mf://blob/{'c' * 64}",
            quote="evidence",
            quote_sha256=hashlib.sha256(b"evidence").hexdigest(),
            locator="chars:0-8",
        )


def _source(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    source_root.mkdir(exist_ok=True)
    source = source_root / "evidence.md"
    source.write_text("# Evidence\n\npublic project note\n", encoding="utf-8")
    return source_root, source


def _changeset(workspace: Workspace) -> ChangeSet:
    return ChangeSet(
        changeset_id="chg_security",
        base_commit=workspace.current_commit(),
        status=ChangeSetStatus.PROPOSED,
        operations=(
            ChangeOperation(
                type=ChangeOperationType.CREATE_PAGE,
                path="wiki/adrs/cache.md",
            ),
        ),
    )


_LEGACY_SCHEMA = """
CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    source_uri TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind = 'local'),
    created_at TEXT NOT NULL
);
CREATE TABLE blobs (
    id INTEGER PRIMARY KEY,
    content_sha256 TEXT NOT NULL UNIQUE,
    snapshot_path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE source_versions (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    blob_id INTEGER NOT NULL REFERENCES blobs(id),
    supersedes_version_id INTEGER REFERENCES source_versions(id),
    media_type TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    is_current INTEGER NOT NULL
);
CREATE UNIQUE INDEX idx_source_versions_one_current
ON source_versions(source_id) WHERE is_current = 1;
CREATE INDEX idx_source_versions_observed
ON source_versions(source_id, observed_at DESC);
CREATE VIRTUAL TABLE source_fts USING fts5(
    title, content, search_terms, tokenize='unicode61'
);
"""
