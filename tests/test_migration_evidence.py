from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import sqlite3
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

import memoryforge.workspace as workspace_module
from memoryforge.changesets import ChangeSetStore
from memoryforge.errors import ChangeSetStoreError, WorkspaceError
from memoryforge.importer import import_local_file
from memoryforge.manifests import SourceManifestStore
from memoryforge.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    Citation,
    Claim,
    ClaimStatus,
)
from memoryforge.workspace import Workspace, search_sources


def test_real_phase1a_workspace_migrates_data_contract_and_manifests(
    tmp_path: Path,
) -> None:
    workspace, source_id, content_sha256 = _legacy_workspace_with_data(tmp_path)

    opened = Workspace.open(workspace)
    reopened = Workspace.open(workspace)

    lines = (workspace / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.memoryforge/" not in lines
    assert "# preserve this user rule" in lines
    assert "/.memoryforge/index.sqlite*" in lines
    assert {
        ".memoryforge/config.yaml",
        ".memoryforge/schema.yaml",
    } <= set(_git(workspace, "ls-files").splitlines())

    results = search_sources(workspace, "legacy searchable")
    assert len(results) == 1
    assert results[0].source_id == source_id
    assert results[0].category.value == "notes"

    with sqlite3.connect(opened.index_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT category, legacy_category FROM source_versions").fetchone()
        assert row is not None
        assert dict(row) == {"category": "notes", "legacy_category": "custom"}

    manifests = SourceManifestStore(opened.manifest_dir).list_all()
    assert reopened.root == opened.root
    assert len(manifests) == 1
    assert manifests[0].source_id == source_id
    assert manifests[0].content_sha256 == content_sha256
    assert manifests[0].category.value == "notes"
    assert manifests[0].legacy_category == "custom"


def test_database_schema_migration_rolls_back_every_column_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _legacy_workspace(tmp_path)
    database = workspace / ".memoryforge/index.sqlite"
    original_statements = workspace_module._SCHEMA_STATEMENTS
    monkeypatch.setattr(
        workspace_module,
        "_SCHEMA_STATEMENTS",
        (*original_statements, "CREATE TABLE this is not valid SQL"),
    )

    with pytest.raises(sqlite3.Error):
        workspace_module._migrate_database(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(source_versions)")}
    assert "sensitivity" not in columns
    assert "tags_json" not in columns
    assert "legacy_category" not in columns


def test_origin_main_e603_workspace_migrates_and_remains_fully_usable(
    tmp_path: Path,
) -> None:
    workspace, expected = _origin_main_e603_workspace_with_data(tmp_path)
    original_raw = {
        path.relative_to(workspace): path.read_bytes()
        for path in (workspace / "raw").rglob("*")
        if path.is_file()
    }

    opened = Workspace.open(workspace)
    reopened = Workspace.open(workspace)

    assert reopened.root == opened.root
    with sqlite3.connect(opened.index_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        sources = connection.execute(
            """
            SELECT source_id, source_uri, source_path, legacy_source_id, created_at
            FROM sources
            ORDER BY legacy_source_id
            """
        ).fetchall()
        versions = connection.execute(
            """
            SELECT
                v.category, v.legacy_category, v.title, v.observed_at,
                v.sensitivity, v.tags_json, b.content_sha256, b.snapshot_path
            FROM source_versions AS v
            JOIN blobs AS b ON b.id = v.blob_id
            ORDER BY v.id
            """
        ).fetchall()

    assert {"sources", "blobs", "source_versions", "source_fts"} <= tables
    assert "source_documents" not in tables
    assert len(sources) == 2
    assert [row[3] for row in sources] == sorted(item["legacy_source_id"] for item in expected)
    assert all(len(str(row[0])) == 64 for row in sources)
    assert all(str(row[1]) == f"mf://source/{row[0]}" for row in sources)
    assert [row[2] for row in sources] == [
        item["raw_path"] for item in sorted(expected, key=lambda item: item["legacy_source_id"])
    ]
    assert versions[0][0:6] == (
        "design",
        None,
        expected[0]["title"],
        expected[0]["observed_at"],
        "local_only",
        '["cache", "phase2"]',
    )
    assert versions[1][0:6] == (
        "notes",
        "custom",
        expected[1]["title"],
        expected[1]["imported_at"],
        "local_only",
        "[]",
    )
    for item in expected:
        blob_path = workspace / f"raw/blobs/{item['content_sha256'][:2]}/"
        blob = blob_path / f"{item['content_sha256']}.blob"
        assert blob.read_bytes() == item["content"]
    assert {
        path.relative_to(workspace): path.read_bytes()
        for path in (workspace / "raw").rglob("*")
        if path.is_file() and path.relative_to(workspace) in original_raw
    } == original_raw

    valid_results = search_sources(workspace, "namespaced migration")
    defaulted_results = search_sources(workspace, "fallback metadata")
    assert valid_results[0].title == expected[0]["title"]
    assert defaulted_results[0].category.value == "notes"

    manifests = SourceManifestStore(opened.manifest_dir).list_all()
    assert len(manifests) == 2
    assert {manifest.legacy_source_id for manifest in manifests} == {
        item["legacy_source_id"] for item in expected
    }
    assert (opened.manifest_dir / f"{expected[0]['legacy_source_id']}.json").is_file()

    source_root = tmp_path / "new-source"
    source_root.mkdir()
    source = source_root / "new.md"
    source.write_text("# New\n\npost migration import remains searchable\n", encoding="utf-8")
    imported = import_local_file(workspace, source, source_root=source_root)
    assert search_sources(workspace, "post migration import")[0].source_id == imported.source_id
    assert len(SourceManifestStore(opened.manifest_dir).list_all()) == 3


def test_phase1_manifest_without_legacy_id_keeps_original_payload_and_hash(
    tmp_path: Path,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "phase1.md"
    source.write_text("phase1 evidence", encoding="utf-8")
    import_local_file(workspace.root, source, source_root=source_root)
    stored = SourceManifestStore(workspace.manifest_dir).list_all()[0]
    for path in workspace.manifest_dir.iterdir():
        path.unlink()
    old_payload = (stored.model_dump_json(indent=2, exclude={"legacy_source_id"}) + "\n").encode()
    old_hash = hashlib.sha256(old_payload).hexdigest()
    old_name = f"{stored.source_id}--{old_hash}.json"
    old_path = workspace.manifest_dir / old_name
    old_path.write_bytes(old_payload)

    Workspace.open(workspace.root)

    current_manifests = [
        path for path in workspace.manifest_dir.iterdir() if path.name.startswith(stored.source_id)
    ]
    assert current_manifests == [old_path]
    assert old_path.read_bytes() == old_payload
    assert b"legacy_source_id" not in old_payload


def test_origin_main_migration_iterates_rows_without_full_materialization() -> None:
    source = textwrap.dedent(inspect.getsource(workspace_module._migrate_origin_main_schema))
    tree = ast.parse(source)

    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fetchall"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "migrated"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id == "rows"
        for node in ast.walk(tree)
    )


def test_origin_main_e603_migration_failure_rolls_back_database_and_new_blobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, expected = _origin_main_e603_workspace_with_data(tmp_path)
    database = workspace / ".memoryforge/index.sqlite"
    original_write_blob = workspace_module._write_blob
    original_rebuild_fts = workspace_module._rebuild_origin_main_fts
    published_hashes: list[str] = []

    def recording_write_blob(
        root: Path,
        content_sha256: str,
        content: bytes,
    ) -> tuple[Path, bool]:
        published_hashes.append(content_sha256)
        return original_write_blob(root, content_sha256, content)

    monkeypatch.setattr(workspace_module, "_write_blob", recording_write_blob)

    def fail_during_fts_rebuild(connection: sqlite3.Connection, root: Path) -> None:
        original_rebuild_fts(connection, root)
        connection.execute("CREATE TABLE this is not valid SQL")

    monkeypatch.setattr(
        workspace_module,
        "_rebuild_origin_main_fts",
        fail_during_fts_rebuild,
    )

    with pytest.raises(sqlite3.Error):
        workspace_module._migrate_database(database)

    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        count = connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()
    assert "source_documents" in tables
    assert "sources" not in tables
    assert count == (2,)
    assert published_hashes == [item["content_sha256"] for item in expected]
    for item in expected:
        canonical_blob = (
            workspace / "raw/blobs" / item["content_sha256"][:2] / f"{item['content_sha256']}.blob"
        )
        assert not canonical_blob.exists()
        assert (workspace / item["raw_path"]).read_bytes() == item["content"]


@pytest.mark.parametrize("git_entry_kind", ["symlink", "gitdir"])
def test_workspace_open_rejects_external_git_metadata_without_writing(
    tmp_path: Path,
    git_entry_kind: str,
) -> None:
    workspace = _legacy_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _run_git(outside, "init", "--quiet")
    git_entry = workspace / ".git"
    if git_entry_kind == "symlink":
        git_entry.symlink_to(outside / ".git", target_is_directory=True)
    else:
        git_entry.write_text(f"gitdir: {outside / '.git'}\n", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="Git|git|symbolic|regular"):
        Workspace.open(workspace)

    assert _git_result(outside, "rev-parse", "--verify", "HEAD").returncode != 0
    assert list((outside / ".git/objects").glob("[0-9a-f][0-9a-f]/*")) == []


def test_changeset_model_rejects_malformed_source_ids() -> None:
    with pytest.raises(ValidationError, match="source_ids"):
        ChangeSet(
            changeset_id="chg_bad_source",
            base_commit="a" * 40,
            source_ids=("not-a-source",),
            status=ChangeSetStatus.PROPOSED,
        )


def test_changeset_staging_validates_verified_citation_against_evidence(
    tmp_path: Path,
) -> None:
    source_root, source = _source(tmp_path)
    content = source.read_text(encoding="utf-8")
    workspace = Workspace.initialize(tmp_path / "workspace")
    imported = import_local_file(workspace.root, source, source_root=source_root)
    quote = "public project note"
    start = content.index(quote)
    citation = Citation(
        source_id=imported.source_id,
        content_sha256=imported.content_sha256,
        snapshot_uri=imported.snapshot_uri,
        quote=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        locator=f"chars:{start}-{start + len(quote)}",
    )
    changeset = _evidence_changeset(workspace, imported.source_id, citation)

    stored = ChangeSetStore(workspace).create(
        changeset,
        {"wiki/adrs/evidence.md": "# Evidence\n"},
    )

    assert stored.changeset.claims[0].citations == (citation,)


@pytest.mark.parametrize(
    ("tamper", "expected"),
    [
        ("missing_source", "source"),
        ("wrong_version", "version|evidence|blob"),
        ("wrong_locator", "quote|locator"),
    ],
)
def test_changeset_staging_rejects_forged_verified_citation(
    tmp_path: Path,
    tamper: str,
    expected: str,
) -> None:
    source_root, source = _source(tmp_path)
    content = source.read_text(encoding="utf-8")
    workspace = Workspace.initialize(tmp_path / "workspace")
    imported = import_local_file(workspace.root, source, source_root=source_root)
    quote = "public project note"
    start = content.index(quote)
    source_id = imported.source_id
    content_sha256 = imported.content_sha256
    snapshot_uri = imported.snapshot_uri
    locator = f"chars:{start}-{start + len(quote)}"
    if tamper == "missing_source":
        source_id = "f" * 64
    elif tamper == "wrong_version":
        content_sha256 = "e" * 64
        snapshot_uri = f"mf://blob/{content_sha256}"
    else:
        locator = "chars:0-1"
    citation = Citation(
        source_id=source_id,
        content_sha256=content_sha256,
        snapshot_uri=snapshot_uri,
        quote=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        locator=locator,
    )
    changeset = _evidence_changeset(workspace, source_id, citation)

    with pytest.raises(ChangeSetStoreError, match=expected):
        ChangeSetStore(workspace).create(
            changeset,
            {"wiki/adrs/evidence.md": "# Evidence\n"},
        )

    assert list(workspace.staging_dir.iterdir()) == []


def test_nested_candidate_parent_is_fsynced_after_file_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    directory_inodes: set[tuple[int, int]] = set()
    real_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        file_stat = os.fstat(descriptor)
        if stat.S_ISDIR(file_stat.st_mode):
            directory_inodes.add((file_stat.st_dev, file_stat.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr("memoryforge.changesets.os.fsync", recording_fsync)
    ChangeSetStore(workspace).create(
        _page_changeset(workspace),
        {"wiki/adrs/nested/cache.md": "# Cache\n"},
    )

    parent_stat = (workspace.staging_dir / "chg_nested/proposed/wiki/adrs/nested").stat()
    assert (parent_stat.st_dev, parent_stat.st_ino) in directory_inodes


def _legacy_workspace_with_data(tmp_path: Path) -> tuple[Path, str, str]:
    workspace = _legacy_workspace(tmp_path)
    content = "# Legacy\n\nlegacy searchable evidence\n"
    content_bytes = content.encode()
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()
    source_id = hashlib.sha256(b"legacy.md").hexdigest()
    snapshot_path = f"raw/blobs/{content_sha256[:2]}/{content_sha256}.blob"
    snapshot = workspace / snapshot_path
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(content_bytes)
    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        source_row = connection.execute(
            """
            INSERT INTO sources(source_id, source_uri, source_path, source_kind, created_at)
            VALUES (?, ?, 'legacy.md', 'local', '2026-01-01T00:00:00+00:00')
            """,
            (source_id, f"mf://source/{source_id}"),
        )
        blob_row = connection.execute(
            """
            INSERT INTO blobs(content_sha256, snapshot_path, size_bytes, created_at)
            VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00')
            """,
            (content_sha256, snapshot_path, len(content_bytes)),
        )
        version_row = connection.execute(
            """
            INSERT INTO source_versions(
                source_id, blob_id, supersedes_version_id, media_type, category,
                title, observed_at, is_current
            ) VALUES (?, ?, NULL, 'text/markdown', 'custom', 'Legacy',
                      '2026-01-01T00:00:00+00:00', 1)
            """,
            (source_row.lastrowid, blob_row.lastrowid),
        )
        connection.execute(
            """
            INSERT INTO source_fts(rowid, title, content, search_terms)
            VALUES (?, 'Legacy', ?, 'legacy searchable evidence')
            """,
            (version_row.lastrowid, content),
        )
    (workspace / ".gitignore").write_text(
        "/raw/\n/.memoryforge/\n# preserve this user rule\n",
        encoding="utf-8",
    )
    return workspace, source_id, content_sha256


def _legacy_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "legacy"
    (workspace / "raw").mkdir(parents=True)
    (workspace / "wiki").mkdir()
    internal = workspace / ".memoryforge"
    internal.mkdir()
    with sqlite3.connect(internal / "index.sqlite") as connection:
        connection.executescript(_LEGACY_SCHEMA)
    return workspace


def _origin_main_e603_workspace_with_data(
    tmp_path: Path,
) -> tuple[Path, list[dict[str, object]]]:
    workspace = tmp_path / "origin-main-e603"
    (workspace / "raw/design").mkdir(parents=True)
    (workspace / "raw/notes").mkdir()
    (workspace / "wiki").mkdir()
    internal = workspace / ".memoryforge"
    manifest_dir = internal / "manifests/sources"
    manifest_dir.mkdir(parents=True)
    valid = _e603_source_record(
        legacy_source_id="src_0123456789abcdef",
        raw_path="raw/design/src_0123456789abcdef--cache-design.md",
        content=b"# Cache design\n\nnamespaced migration evidence\n",
        media_type="text/markdown",
        category="design",
        imported_at="2026-02-01T10:00:00+00:00",
        observed_at="2026-01-31T09:00:00+00:00",
        sensitivity="local_only",
        tags_json='["cache", "phase2"]',
        title="src_0123456789abcdef--cache-design.md",
    )
    defaulted = _e603_source_record(
        legacy_source_id="src_fedcba9876543210",
        raw_path="raw/notes/src_fedcba9876543210--fallback.txt",
        content=b"fallback metadata remains searchable\n",
        media_type="text/plain",
        category="custom",
        imported_at="2026-02-02T11:00:00+00:00",
        observed_at=None,
        sensitivity="unknown",
        tags_json='{"not": "a list"}',
        title="src_fedcba9876543210--fallback.txt",
    )
    records = [valid, defaulted]
    with sqlite3.connect(internal / "index.sqlite") as connection:
        connection.executescript(_E603_SCHEMA)
        for record in records:
            (workspace / str(record["raw_path"])).write_bytes(bytes(record["content"]))
            connection.execute(
                """
                INSERT INTO source_documents(
                    source_id, uri, content_sha256, media_type, category,
                    imported_at, observed_at, sensitivity, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["legacy_source_id"],
                    record["raw_path"],
                    record["content_sha256"],
                    record["media_type"],
                    record["category"],
                    record["imported_at"],
                    record["observed_at"],
                    record["sensitivity"],
                    record["tags_json"],
                ),
            )
            connection.execute(
                "INSERT INTO source_fts(source_id, title, body) VALUES (?, ?, ?)",
                (
                    record["legacy_source_id"],
                    record["title"],
                    bytes(record["content"]).decode(),
                ),
            )
    legacy_manifest = {
        "source_id": valid["legacy_source_id"],
        "uri": valid["raw_path"],
        "content_sha256": valid["content_sha256"],
        "media_type": valid["media_type"],
        "category": valid["category"],
        "imported_at": valid["imported_at"],
        "observed_at": valid["observed_at"],
        "supersedes_source_id": None,
        "sensitivity": valid["sensitivity"],
        "tags": ["cache", "phase2"],
    }
    (manifest_dir / f"{valid['legacy_source_id']}.json").write_text(
        json.dumps(legacy_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (internal / ".gitignore").write_text(
        "index.sqlite\nindex.sqlite-*\nvectors/\ntraces/\n",
        encoding="utf-8",
    )
    return workspace, records


def _e603_source_record(
    *,
    legacy_source_id: str,
    raw_path: str,
    content: bytes,
    media_type: str,
    category: str,
    imported_at: str,
    observed_at: str | None,
    sensitivity: str,
    tags_json: str,
    title: str,
) -> dict[str, object]:
    return {
        "legacy_source_id": legacy_source_id,
        "raw_path": raw_path,
        "content": content,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "media_type": media_type,
        "category": category,
        "imported_at": imported_at,
        "observed_at": observed_at,
        "sensitivity": sensitivity,
        "tags_json": tags_json,
        "title": title,
    }


def _source(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "evidence.md"
    source.write_text("# Evidence\n\npublic project note\n", encoding="utf-8")
    return source_root, source


def _evidence_changeset(
    workspace: Workspace,
    source_id: str,
    citation: Citation,
) -> ChangeSet:
    claim = Claim(
        claim_id="clm_evidence",
        subject="evidence",
        predicate="contains",
        object="public project note",
        status=ClaimStatus.VERIFIED,
        confidence=1.0,
        citations=(citation,),
    )
    return ChangeSet(
        changeset_id="chg_evidence",
        base_commit=workspace.current_commit(),
        source_ids=(source_id,),
        status=ChangeSetStatus.PROPOSED,
        operations=(
            ChangeOperation(
                type=ChangeOperationType.CREATE_PAGE,
                path="wiki/adrs/evidence.md",
            ),
        ),
        claims=(claim,),
    )


def _page_changeset(workspace: Workspace) -> ChangeSet:
    return ChangeSet(
        changeset_id="chg_nested",
        base_commit=workspace.current_commit(),
        status=ChangeSetStatus.PROPOSED,
        operations=(
            ChangeOperation(
                type=ChangeOperationType.CREATE_PAGE,
                path="wiki/adrs/nested/cache.md",
            ),
        ),
    )


def _git(root: Path, *arguments: str) -> str:
    return _run_git(root, *arguments).stdout.strip()


def _git_result(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
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

_E603_SCHEMA = """
CREATE TABLE source_documents (
    source_id TEXT PRIMARY KEY,
    uri TEXT NOT NULL UNIQUE,
    content_sha256 TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    category TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    observed_at TEXT,
    sensitivity TEXT NOT NULL,
    tags_json TEXT NOT NULL
);

CREATE VIRTUAL TABLE source_fts USING fts5(
    source_id UNINDEXED,
    title,
    body
);
"""
