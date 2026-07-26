from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from memoryforge.errors import WorkspaceError
from memoryforge.manifests import SourceManifestStore
from memoryforge.models import (
    ChangeSet,
    ClaimStatus,
    ImportResult,
    LocalDocument,
    SearchResult,
    Sensitivity,
    SourceCategory,
    SourceVersionManifest,
)
from memoryforge.version_store import GitVersionStore

DATABASE_RELATIVE_PATH = Path(".memoryforge/index.sqlite")
RAW_CATEGORIES = ("design", "postmortem", "summary", "notes", "refs")
WIKI_DIRECTORIES = (
    "sources",
    "entities",
    "concepts",
    "pitfalls",
    "adrs",
    "comparisons",
    "overviews",
)
_GITIGNORE_RULES = (
    "/raw/",
    "/.memoryforge/index.sqlite*",
    "/.memoryforge/manifests/",
    "/.memoryforge/staging/",
    "/.memoryforge/rejected/",
    "/.memoryforge/traces/",
    "/.memoryforge/vectors/",
)
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_SEARCH_RUN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_CHAR_LOCATOR = re.compile(r"^chars:(?P<start>\d+)-(?P<end>\d+)$")
_BLOB_ROOT = Path("raw/blobs")
_SECURE_DIR_FD_SUPPORTED = all(
    function in os.supports_dir_fd for function in (os.open, os.mkdir, os.stat, os.unlink)
)
_BASELINE_PATHS = (
    ".gitignore",
    ".memoryforgeignore",
    "AGENTS.md",
    "wiki/INDEX.md",
    ".memoryforge/config.yaml",
    ".memoryforge/schema.yaml",
)
_DEFAULT_CONFIG_YAML = """workspace_version: 1
schema_version: 1
provider:
  enabled: false
  allowed_environment_variables: []
index:
  fts: true
  embeddings: false
"""
_DEFAULT_SCHEMA_YAML = """source_categories:
  - design
  - postmortem
  - summary
  - notes
  - refs
page_types:
  - sources
  - entities
  - concepts
  - pitfalls
  - adrs
  - comparisons
  - overviews
claim_rules:
  verified_claim_requires_citation: true
  allow_raw_mutation: false
  unresolved_high_conflict_blocks_apply: true
"""
_DEFAULT_AGENTS_MD = """# MemoryForge Workspace

This workspace contains personal developer knowledge only. Never add company,
customer, credential, or production-secret material.

`raw/` stores immutable imported evidence. Stable `wiki/` content may only be
changed through a reviewed ChangeSet. Verified claims require citations.
Sources marked `local_only` must not be sent to remote providers.
"""
_DEFAULT_MEMORYFORGEIGNORE = """.env
.env.*
*.pem
*.key
id_rsa
.ssh/
.aws/
.git/
"""

_SCHEMA_STATEMENTS = (
    """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    source_uri TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind = 'local'),
    created_at TEXT NOT NULL
)""",
    """
CREATE TABLE IF NOT EXISTS blobs (
    id INTEGER PRIMARY KEY,
    content_sha256 TEXT NOT NULL UNIQUE,
    snapshot_path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL
)""",
    """
CREATE TABLE IF NOT EXISTS source_versions (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    blob_id INTEGER NOT NULL REFERENCES blobs(id),
    supersedes_version_id INTEGER REFERENCES source_versions(id),
    media_type TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    legacy_category TEXT,
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1))
)""",
    """
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_versions_one_current
ON source_versions(source_id)
WHERE is_current = 1""",
    """
CREATE INDEX IF NOT EXISTS idx_source_versions_observed
ON source_versions(source_id, observed_at DESC)""",
    """
CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
    title,
    content,
    search_terms,
    tokenize='unicode61'
)""",
)


class WorkspaceSecurityError(ValueError):
    """Raised when a workspace path violates the local storage boundary."""


class WorkspaceIntegrityError(RuntimeError):
    """Raised when immutable evidence no longer matches its recorded digest."""


@dataclass(frozen=True)
class Workspace:
    """Validated paths and version-store access for one workspace."""

    root: Path

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def wiki_dir(self) -> Path:
        return self.root / "wiki"

    @property
    def internal_dir(self) -> Path:
        return self.root / ".memoryforge"

    @property
    def config_path(self) -> Path:
        return self.internal_dir / "config.yaml"

    @property
    def schema_path(self) -> Path:
        return self.internal_dir / "schema.yaml"

    @property
    def manifest_dir(self) -> Path:
        return self.internal_dir / "manifests" / "sources"

    @property
    def staging_dir(self) -> Path:
        return self.internal_dir / "staging"

    @property
    def rejected_dir(self) -> Path:
        return self.internal_dir / "rejected"

    @property
    def index_path(self) -> Path:
        return self.root / DATABASE_RELATIVE_PATH

    @property
    def version_store(self) -> GitVersionStore:
        return GitVersionStore(self.root)

    def current_commit(self) -> str:
        commit = self.version_store.head()
        if commit is None:
            raise WorkspaceError("workspace is missing its Git baseline commit")
        return commit

    def validate_changeset_evidence(self, changeset: ChangeSet) -> None:
        """Bind staged source and citation identities to immutable local evidence."""
        with _connect(self.index_path) as connection:
            for source_id in changeset.source_ids:
                exists = connection.execute(
                    "SELECT 1 FROM sources WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
                if exists is None:
                    raise WorkspaceIntegrityError(f"ChangeSet source does not exist: {source_id}")

            for claim in changeset.claims:
                if claim.status is not ClaimStatus.VERIFIED:
                    continue
                for citation in claim.citations:
                    row = connection.execute(
                        """
                        SELECT b.snapshot_path
                        FROM sources AS s
                        JOIN source_versions AS v ON v.source_id = s.id
                        JOIN blobs AS b ON b.id = v.blob_id
                        WHERE s.source_id = ? AND b.content_sha256 = ?
                        """,
                        (citation.source_id, citation.content_sha256),
                    ).fetchone()
                    if row is None:
                        raise WorkspaceIntegrityError(
                            "Citation does not identify an imported SourceVersion"
                        )
                    snapshot_path = Path(str(row["snapshot_path"]))
                    evidence = _read_blob_bytes(
                        self.root,
                        citation.content_sha256,
                        snapshot_path,
                    )
                    try:
                        text = evidence.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise WorkspaceIntegrityError(
                            "Citation evidence is not valid UTF-8"
                        ) from exc
                    match = _CHAR_LOCATOR.fullmatch(citation.locator)
                    if match is None:
                        raise WorkspaceIntegrityError("Citation locator is invalid")
                    start = int(match.group("start"))
                    end = int(match.group("end"))
                    if end > len(text) or text[start:end] != citation.quote:
                        raise WorkspaceIntegrityError(
                            "Citation quote does not match its locator in immutable evidence"
                        )

    def validate_internal_directory(self, path: Path) -> None:
        if not path.is_relative_to(self.internal_dir):
            raise WorkspaceSecurityError("managed path must remain inside .memoryforge")
        _validate_managed_directory(path)

    @classmethod
    def initialize(cls, root: Path) -> Workspace:
        initialized = _initialize_workspace(root)
        return cls(initialized)

    @classmethod
    def open(cls, root: Path) -> Workspace:
        resolved = _validated_workspace_root(root)
        _validate_workspace_identity_readonly(resolved)
        version_store = GitVersionStore(resolved)
        version_store.validate_metadata(allow_missing=True)
        _upgrade_workspace_contract(resolved)
        workspace_database(resolved)
        _backfill_source_manifests(resolved)
        workspace = cls(resolved)
        if not workspace.config_path.is_file() or not workspace.schema_path.is_file():
            raise WorkspaceError("workspace configuration is missing")
        workspace.version_store.validate_metadata()
        workspace.current_commit()
        return workspace


def init_workspace(workspace: Path) -> Path:
    return Workspace.initialize(workspace).root


def _initialize_workspace(workspace: Path) -> Path:
    root = _absolute_path(workspace)
    _reject_symlink_components(root)
    if root.exists() and not root.is_dir():
        raise WorkspaceSecurityError("workspace must be a directory")
    _validate_initialization_targets(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)

    managed_directories = [
        Path("raw"),
        Path("wiki"),
        Path(".memoryforge"),
        *(Path("wiki") / page_type for page_type in WIKI_DIRECTORIES),
        Path(".memoryforge/manifests/sources"),
        Path(".memoryforge/staging"),
        Path(".memoryforge/rejected"),
        Path(".memoryforge/traces"),
        Path(".memoryforge/vectors"),
    ]
    for relative in managed_directories:
        _ensure_private_directory(root / relative)
    _write_protective_gitignore(root)
    _write_new(root / ".memoryforgeignore", _DEFAULT_MEMORYFORGEIGNORE)
    _write_new(root / "AGENTS.md", _DEFAULT_AGENTS_MD)
    _write_new(root / "wiki/INDEX.md", "# Knowledge Index\n")
    _write_new(root / ".memoryforge/config.yaml", _DEFAULT_CONFIG_YAML)
    _write_new(root / ".memoryforge/schema.yaml", _DEFAULT_SCHEMA_YAML)

    database_path = root / DATABASE_RELATIVE_PATH
    _reject_symlink_components(database_path)
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _apply_schema(connection)
    database_path.chmod(0o600)

    version_store = GitVersionStore(root)
    version_store.initialize()
    version_store.ensure_baseline(_BASELINE_PATHS)
    return root


def workspace_database(workspace: Path) -> Path:
    root = _validated_workspace_root(workspace)
    for relative in (Path("raw"), Path("wiki"), Path(".memoryforge")):
        _validate_managed_directory(root / relative)

    database_path = root / DATABASE_RELATIVE_PATH
    _reject_symlink_components(database_path)
    if not database_path.is_file():
        raise FileNotFoundError(
            f"MemoryForge workspace is not initialized: {workspace}. Run 'memoryforge init' first."
        )
    if not database_path.resolve().is_relative_to(root.resolve()):
        raise WorkspaceSecurityError("workspace database escapes the workspace")
    _migrate_database(database_path)
    database_path.chmod(0o600)
    return database_path


def _validate_workspace_identity_readonly(root: Path) -> None:
    """Reject arbitrary directories before upgrade code can write into them."""
    for relative in (Path("raw"), Path("wiki"), Path(".memoryforge")):
        path = root / relative
        _reject_symlink_components(path)
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            raise WorkspaceError("MemoryForge workspace is not initialized") from None
        if not stat.S_ISDIR(metadata.st_mode):
            raise WorkspaceError("MemoryForge workspace is not initialized")

    database_path = root / DATABASE_RELATIVE_PATH
    _reject_symlink_components(database_path)
    try:
        database_metadata = os.lstat(database_path)
    except FileNotFoundError:
        raise WorkspaceError("MemoryForge workspace is not initialized") from None
    if not stat.S_ISREG(database_metadata.st_mode):
        raise WorkspaceError("MemoryForge workspace database is invalid")

    try:
        with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                  AND name IN ('sources', 'blobs', 'source_versions', 'source_fts')
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise WorkspaceError("MemoryForge workspace database is invalid") from exc
    if {str(row[0]) for row in rows} != {
        "sources",
        "blobs",
        "source_versions",
        "source_fts",
    }:
        raise WorkspaceError("MemoryForge workspace database schema is invalid")


def store_source(
    workspace: Path,
    *,
    source_id: str,
    content_sha256: str,
    document: LocalDocument,
) -> ImportResult:
    root = _validated_workspace_root(workspace)
    database_path = workspace_database(root)
    snapshot_bytes = document.content.encode("utf-8")
    relative_snapshot = _blob_relative_path(content_sha256)
    created_snapshot = False
    status: Literal["created", "updated"] = "created"
    observed_at = _now()

    try:
        with _connect(database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _cleanup_blob_temps(root, content_sha256)
            source_row = connection.execute(
                "SELECT id, source_id FROM sources WHERE source_uri = ?",
                (document.source_uri,),
            ).fetchone()
            current_row = None
            if source_row is not None:
                current_row = connection.execute(
                    """
                    SELECT
                        v.id AS version_id,
                        v.title,
                        v.media_type,
                        v.category,
                        v.observed_at,
                        v.sensitivity,
                        v.tags_json,
                        b.content_sha256,
                        b.snapshot_path
                    FROM source_versions AS v
                    JOIN blobs AS b ON b.id = v.blob_id
                    WHERE v.source_id = ? AND v.is_current = 1
                    """,
                    (source_row["id"],),
                ).fetchone()
                metadata_unchanged = (
                    current_row is not None
                    and current_row["content_sha256"] == content_sha256
                    and current_row["category"] == document.category
                    and current_row["media_type"] == document.media_type
                    and current_row["title"] == document.title
                    and current_row["sensitivity"] == document.sensitivity.value
                    and current_row["tags_json"] == json.dumps(document.tags)
                )
                if metadata_unchanged:
                    snapshot_relative = str(current_row["snapshot_path"])
                    _verify_blob_hash(root, content_sha256, Path(snapshot_relative))
                    return ImportResult(
                        status="unchanged",
                        source_id=source_row["source_id"],
                        title=current_row["title"],
                        source_uri=document.source_uri,
                        category=current_row["category"],
                        content_sha256=content_sha256,
                        snapshot_uri=_blob_uri(content_sha256),
                        snapshot_path=snapshot_relative,
                        observed_at=datetime.fromisoformat(str(current_row["observed_at"])),
                    )
                status = "updated"

            blob_row = connection.execute(
                "SELECT id, snapshot_path FROM blobs WHERE content_sha256 = ?",
                (content_sha256,),
            ).fetchone()
            if blob_row is None:
                relative_snapshot, created_snapshot = _write_blob(
                    root, content_sha256, snapshot_bytes
                )
                blob_cursor = connection.execute(
                    """
                    INSERT INTO blobs(content_sha256, snapshot_path, size_bytes, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        content_sha256,
                        relative_snapshot.as_posix(),
                        len(snapshot_bytes),
                        _now(),
                    ),
                )
                blob_id = _lastrowid(blob_cursor)
                snapshot_relative = relative_snapshot.as_posix()
            else:
                blob_id = int(blob_row["id"])
                snapshot_relative = str(blob_row["snapshot_path"])
                _verify_blob_hash(root, content_sha256, Path(snapshot_relative))

            if source_row is None:
                source_cursor = connection.execute(
                    """
                    INSERT INTO sources(
                        source_id, source_uri, source_path, source_kind, created_at
                    ) VALUES (?, ?, ?, 'local', ?)
                    """,
                    (source_id, document.source_uri, document.source_path, _now()),
                )
                database_source_id = _lastrowid(source_cursor)
                stable_source_id = source_id
                supersedes_version_id = None
            else:
                database_source_id = int(source_row["id"])
                stable_source_id = str(source_row["source_id"])
                supersedes_version_id = (
                    int(current_row["version_id"]) if current_row is not None else None
                )
                connection.execute(
                    "UPDATE sources SET source_path = ? WHERE id = ?",
                    (document.source_path, database_source_id),
                )
                connection.execute(
                    "UPDATE source_versions SET is_current = 0 WHERE source_id = ?",
                    (database_source_id,),
                )

            version_cursor = connection.execute(
                """
                INSERT INTO source_versions(
                    source_id, blob_id, supersedes_version_id, media_type,
                    category, title, observed_at, sensitivity, tags_json, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    database_source_id,
                    blob_id,
                    supersedes_version_id,
                    document.media_type,
                    document.category,
                    document.title,
                    observed_at,
                    document.sensitivity.value,
                    json.dumps(document.tags),
                ),
            )
            version_id = _lastrowid(version_cursor)
            connection.execute(
                """
                INSERT INTO source_fts(rowid, title, content, search_terms)
                VALUES (?, ?, ?, ?)
                """,
                (
                    version_id,
                    document.title,
                    document.content,
                    _search_terms(f"{document.title}\n{document.content}"),
                ),
            )
    except Exception:
        if created_snapshot:
            _cleanup_orphan_blob(root, database_path, content_sha256)
        raise

    return ImportResult(
        status=status,
        source_id=stable_source_id,
        title=document.title,
        source_uri=document.source_uri,
        category=document.category,
        content_sha256=content_sha256,
        snapshot_uri=_blob_uri(content_sha256),
        snapshot_path=snapshot_relative,
        observed_at=datetime.fromisoformat(observed_at),
    )


def search_sources(workspace: Path, query: str, *, limit: int = 10) -> list[SearchResult]:
    if not query.strip():
        raise ValueError("search query must not be empty")
    if limit < 1 or limit > 100:
        raise ValueError("search limit must be between 1 and 100")

    opened = Workspace.open(workspace)
    root = opened.root
    match_query = _fts_query(query)
    with _connect(opened.index_path) as connection:
        rows = connection.execute(
            """
            SELECT
                s.source_id,
                s.source_uri,
                s.source_path,
                v.title,
                v.category,
                source_fts.content,
                b.snapshot_path,
                b.content_sha256,
                v.observed_at
            FROM source_fts
            JOIN source_versions AS v ON v.id = source_fts.rowid
            JOIN blobs AS b ON b.id = v.blob_id
            JOIN sources AS s ON s.id = v.source_id
            WHERE source_fts MATCH ? AND v.is_current = 1
            ORDER BY bm25(source_fts), v.observed_at DESC
            LIMIT ?
            """,
            (match_query, limit),
        ).fetchall()

    results: list[SearchResult] = []
    for row in rows:
        content_sha256 = str(row["content_sha256"])
        snapshot_relative = str(row["snapshot_path"])
        _verify_blob_hash(root, content_sha256, Path(snapshot_relative))
        results.append(
            SearchResult(
                source_id=str(row["source_id"]),
                title=str(row["title"]),
                source_uri=str(row["source_uri"]),
                source_path=str(row["source_path"]),
                snapshot_uri=_blob_uri(content_sha256),
                snapshot_path=snapshot_relative,
                category=SourceCategory(str(row["category"])),
                snippet=_make_snippet(str(row["content"]), query),
                content_sha256=content_sha256,
                observed_at=datetime.fromisoformat(str(row["observed_at"])),
            )
        )
    return results


@contextmanager
def _connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _validated_workspace_root(workspace: Path) -> Path:
    root = _absolute_path(workspace)
    _reject_symlink_components(root)
    if not root.is_dir():
        raise FileNotFoundError(
            f"MemoryForge workspace is not initialized: {workspace}. Run 'memoryforge init' first."
        )
    return root


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            if _is_allowed_system_path_alias(current):
                continue
            raise WorkspaceSecurityError(
                f"symbolic link is not allowed in workspace path: {current}"
            )


def _is_allowed_system_path_alias(path: Path) -> bool:
    allowed_aliases = {
        Path("/tmp"): Path("/private/tmp"),
        Path("/var"): Path("/private/var"),
    }
    expected = allowed_aliases.get(path)
    return expected is not None and path.resolve() == expected


def _ensure_private_directory(path: Path) -> None:
    _reject_symlink_components(path)
    if path.exists() and not path.is_dir():
        raise WorkspaceSecurityError(f"managed workspace path must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _validate_managed_directory(path: Path) -> None:
    _reject_symlink_components(path)
    if not path.is_dir():
        raise WorkspaceSecurityError(f"managed workspace directory is missing: {path}")
    path.chmod(0o700)


def _validate_initialization_targets(root: Path) -> None:
    reserved = (
        root / "raw",
        root / "wiki",
        root / ".memoryforge",
        root / ".git",
        root / ".gitignore",
        root / ".memoryforgeignore",
        root / "AGENTS.md",
    )
    for path in reserved:
        _reject_symlink_components(path)
    conflicts = [path.name for path in reserved if path.exists()]
    if conflicts:
        raise WorkspaceError(
            "workspace paths already exist; refusing to merge: " + ", ".join(conflicts)
        )


def _write_new(path: Path, content: str) -> None:
    _reject_symlink_components(path)
    if path.exists():
        raise WorkspaceError(f"refusing to overwrite existing workspace file: {path.name}")
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _write_protective_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    _reject_symlink_components(path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [line for line in existing.splitlines() if line.strip() != "/.memoryforge/"]
    missing = [rule for rule in _GITIGNORE_RULES if rule not in lines]
    rendered = "\n".join([*lines, *missing])
    if rendered:
        rendered += "\n"
    if rendered == existing:
        return
    path.write_text(rendered, encoding="utf-8")
    path.chmod(0o600)


def _migrate_database(database_path: Path) -> None:
    """Upgrade the Phase 1a schema transactionally without rewriting evidence."""
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(source_versions)").fetchall()
        }
        if not columns:
            _apply_schema(connection)
            return
        if "sensitivity" not in columns:
            connection.execute(
                "ALTER TABLE source_versions ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'public'"
            )
        if "tags_json" not in columns:
            connection.execute(
                "ALTER TABLE source_versions ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "legacy_category" not in columns:
            connection.execute("ALTER TABLE source_versions ADD COLUMN legacy_category TEXT")
        placeholders = ", ".join("?" for _ in RAW_CATEGORIES)
        connection.execute(
            f"""
            UPDATE source_versions
            SET legacy_category = category, category = ?
            WHERE category NOT IN ({placeholders})
            """,
            (SourceCategory.NOTES.value, *RAW_CATEGORIES),
        )
        _apply_schema(connection)


def _apply_schema(connection: sqlite3.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)


def _upgrade_workspace_contract(root: Path) -> None:
    """Add Phase 1 contract files/directories to a valid legacy Phase 1a workspace."""
    version_store = GitVersionStore(root)
    version_store.validate_metadata(allow_missing=True)
    repository_existed = version_store.has_repository()
    for relative in (
        Path(".memoryforge/manifests/sources"),
        Path(".memoryforge/staging"),
        Path(".memoryforge/rejected"),
        Path(".memoryforge/traces"),
        Path(".memoryforge/vectors"),
        *(Path("wiki") / page_type for page_type in WIKI_DIRECTORIES),
    ):
        _ensure_private_directory(root / relative)
    _write_protective_gitignore(root)
    defaults = {
        Path(".memoryforgeignore"): _DEFAULT_MEMORYFORGEIGNORE,
        Path("AGENTS.md"): _DEFAULT_AGENTS_MD,
        Path("wiki/INDEX.md"): "# Knowledge Index\n",
        Path(".memoryforge/config.yaml"): _DEFAULT_CONFIG_YAML,
        Path(".memoryforge/schema.yaml"): _DEFAULT_SCHEMA_YAML,
    }
    for relative, content in defaults.items():
        path = root / relative
        _reject_symlink_components(path)
        if not path.exists():
            _write_new(path, content)
        elif not path.is_file():
            raise WorkspaceSecurityError(f"workspace contract path must be a file: {path}")
    if not repository_existed:
        version_store.initialize()
        version_store.ensure_baseline(_BASELINE_PATHS)
        return
    if version_store.head() is None:
        raise WorkspaceError(
            "existing Git repository has no HEAD; commit the MemoryForge workspace "
            "contract manually before opening it"
        )


def _backfill_source_manifests(root: Path) -> None:
    """Create one verifiable, immutable Manifest for every historical SourceVersion."""
    database_path = root / DATABASE_RELATIVE_PATH
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT
                s.source_id,
                s.source_uri,
                s.source_path,
                v.media_type,
                v.category,
                v.legacy_category,
                v.title,
                v.observed_at,
                v.sensitivity,
                v.tags_json,
                b.content_sha256,
                b.snapshot_path
            FROM source_versions AS v
            JOIN sources AS s ON s.id = v.source_id
            JOIN blobs AS b ON b.id = v.blob_id
            ORDER BY v.id
            """
        ).fetchall()

    store = SourceManifestStore(root / ".memoryforge/manifests/sources")
    for row in rows:
        content_sha256 = str(row["content_sha256"])
        snapshot_path = Path(str(row["snapshot_path"]))
        media_type = str(row["media_type"])
        if media_type not in {"text/markdown", "text/plain"}:
            raise WorkspaceIntegrityError("SourceVersion media type is invalid")
        try:
            tags_value = json.loads(str(row["tags_json"]))
        except json.JSONDecodeError as exc:
            raise WorkspaceIntegrityError("SourceVersion tags metadata is invalid") from exc
        if not isinstance(tags_value, list) or not all(isinstance(tag, str) for tag in tags_value):
            raise WorkspaceIntegrityError("SourceVersion tags metadata is invalid")
        manifest = SourceVersionManifest(
            source_id=str(row["source_id"]),
            source_uri=str(row["source_uri"]),
            source_path=str(row["source_path"]),
            content_sha256=content_sha256,
            snapshot_uri=_blob_uri(content_sha256),
            snapshot_path=snapshot_path.as_posix(),
            media_type=cast(Literal["text/markdown", "text/plain"], media_type),
            category=SourceCategory(str(row["category"])),
            title=str(row["title"]),
            observed_at=datetime.fromisoformat(str(row["observed_at"])),
            sensitivity=Sensitivity(str(row["sensitivity"])),
            tags=tuple(tags_value),
            legacy_category=(
                str(row["legacy_category"]) if row["legacy_category"] is not None else None
            ),
        )
        if store.contains(manifest):
            continue
        _verify_blob_hash(root, content_sha256, snapshot_path)
        store.write(manifest)


def _write_blob(root: Path, content_sha256: str, content: bytes) -> tuple[Path, bool]:
    relative = _blob_relative_path(content_sha256)
    filename = relative.name
    with _open_blob_chain(root, content_sha256, create=True) as chain:
        prefix_fd = chain[-1][2]
        _cleanup_stale_blob_temps(prefix_fd, content_sha256)
        temp_name = f"{content_sha256}.tmp-{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow_flag()
        try:
            descriptor = os.open(temp_name, flags, 0o600, dir_fd=prefix_fd)
            with os.fdopen(descriptor, "wb") as snapshot:
                snapshot.write(content)
                snapshot.flush()
                os.fsync(snapshot.fileno())
                os.fchmod(snapshot.fileno(), 0o600)
            _assert_blob_chain(root, chain)
            try:
                os.link(
                    temp_name,
                    filename,
                    src_dir_fd=prefix_fd,
                    dst_dir_fd=prefix_fd,
                    follow_symlinks=False,
                )
                os.fsync(prefix_fd)
                created = True
            except FileExistsError:
                try:
                    _verify_blob_hash(root, content_sha256, relative)
                    created = False
                except WorkspaceIntegrityError:
                    os.unlink(filename, dir_fd=prefix_fd)
                    os.fsync(prefix_fd)
                    os.link(
                        temp_name,
                        filename,
                        src_dir_fd=prefix_fd,
                        dst_dir_fd=prefix_fd,
                        follow_symlinks=False,
                    )
                    os.fsync(prefix_fd)
                    created = True
            _assert_blob_chain(root, chain)
            return relative, created
        except Exception:
            raise
        finally:
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=prefix_fd)
                os.fsync(prefix_fd)


def _cleanup_stale_blob_temps(prefix_fd: int, content_sha256: str) -> None:
    prefix = f"{content_sha256}.tmp-"
    for name in os.listdir(prefix_fd):
        if name.startswith(prefix):
            with suppress(OSError):
                os.unlink(name, dir_fd=prefix_fd)
    os.fsync(prefix_fd)


def _cleanup_blob_temps(root: Path, content_sha256: str) -> None:
    try:
        with _open_blob_chain(root, content_sha256, create=False) as chain:
            _cleanup_stale_blob_temps(chain[-1][2], content_sha256)
    except FileNotFoundError:
        return


def _verify_blob_hash(root: Path, content_sha256: str, relative: Path) -> None:
    _read_blob_bytes(root, content_sha256, relative)


def _read_blob_bytes(root: Path, content_sha256: str, relative: Path) -> bytes:
    expected_relative = _blob_relative_path(content_sha256)
    if relative != expected_relative:
        raise WorkspaceIntegrityError("blob integrity metadata is inconsistent")

    content = bytearray()
    try:
        with _open_blob_chain(root, content_sha256, create=False) as chain:
            prefix_fd = chain[-1][2]
            descriptor = os.open(
                expected_relative.name,
                os.O_RDONLY | _no_follow_flag(),
                dir_fd=prefix_fd,
            )
            try:
                file_stat = os.fstat(descriptor)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise WorkspaceSecurityError("immutable blob must be a regular file")
                digest = hashlib.sha256()
                with os.fdopen(descriptor, "rb", closefd=False) as snapshot:
                    for chunk in iter(lambda: snapshot.read(1024 * 1024), b""):
                        digest.update(chunk)
                        content.extend(chunk)
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            _assert_blob_chain(root, chain)
    except FileNotFoundError as exc:
        raise WorkspaceIntegrityError("blob integrity check failed: evidence is missing") from exc
    except OSError as exc:
        raise WorkspaceSecurityError("secure blob verification failed") from exc

    if digest.hexdigest() != content_sha256:
        raise WorkspaceIntegrityError("blob integrity check failed: digest mismatch")
    return bytes(content)


def _cleanup_orphan_blob(root: Path, database_path: Path, content_sha256: str) -> None:
    try:
        with _connect(database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM blobs WHERE content_sha256 = ?",
                (content_sha256,),
            ).fetchone()
            if exists is None:
                _unlink_blob(root, content_sha256)
    except (OSError, sqlite3.Error):
        # The import error remains the primary failure. A later import verifies or reuses
        # content-addressed orphan blobs safely.
        return


def _unlink_blob(root: Path, content_sha256: str) -> None:
    try:
        with _open_blob_chain(root, content_sha256, create=False) as chain:
            os.unlink(
                _blob_relative_path(content_sha256).name,
                dir_fd=chain[-1][2],
            )
    except (FileNotFoundError, WorkspaceSecurityError):
        return


@contextmanager
def _open_blob_chain(
    root: Path,
    content_sha256: str,
    *,
    create: bool,
) -> Iterator[list[tuple[int, str, int]]]:
    _require_secure_dir_fd_support()
    descriptors: list[int] = []
    chain: list[tuple[int, str, int]] = []
    directory_flags = os.O_RDONLY | _directory_flag() | _no_follow_flag()
    try:
        root_fd = os.open(root, directory_flags)
        descriptors.append(root_fd)
        raw_fd = _open_directory_at(root_fd, "raw", create=False)
        descriptors.append(raw_fd)
        chain.append((root_fd, "raw", raw_fd))
        blobs_fd = _open_directory_at(raw_fd, "blobs", create=create)
        descriptors.append(blobs_fd)
        chain.append((raw_fd, "blobs", blobs_fd))
        prefix = content_sha256[:2]
        prefix_fd = _open_directory_at(blobs_fd, prefix, create=create)
        descriptors.append(prefix_fd)
        chain.append((blobs_fd, prefix, prefix_fd))
        _assert_blob_chain(root, chain)
        yield chain
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_directory_at(parent_fd: int, name: str, *, create: bool) -> int:
    flags = os.O_RDONLY | _directory_flag() | _no_follow_flag()
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        with suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    os.fchmod(descriptor, 0o700)
    return descriptor


def _assert_blob_chain(root: Path, chain: list[tuple[int, str, int]]) -> None:
    root_stat = root.stat(follow_symlinks=False)
    opened_root_stat = os.fstat(chain[0][0])
    if (root_stat.st_dev, root_stat.st_ino) != (
        opened_root_stat.st_dev,
        opened_root_stat.st_ino,
    ):
        raise WorkspaceSecurityError("workspace changed during blob operation")

    for parent_fd, name, child_fd in chain:
        path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened_stat = os.fstat(child_fd)
        if not stat.S_ISDIR(path_stat.st_mode) or (path_stat.st_dev, path_stat.st_ino) != (
            opened_stat.st_dev,
            opened_stat.st_ino,
        ):
            raise WorkspaceSecurityError("workspace directory changed during blob operation")


def _require_secure_dir_fd_support() -> None:
    if not _SECURE_DIR_FD_SUPPORTED:
        raise WorkspaceSecurityError("secure blob operations are unsupported on this platform")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise WorkspaceSecurityError("secure blob operations are unsupported on this platform")


def _directory_flag() -> int:
    flag = getattr(os, "O_DIRECTORY", None)
    if flag is None:
        raise WorkspaceSecurityError("secure blob operations are unsupported on this platform")
    return int(flag)


def _no_follow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", None)
    if flag is None:
        raise WorkspaceSecurityError("secure blob operations are unsupported on this platform")
    return int(flag)


def _blob_relative_path(content_sha256: str) -> Path:
    return _BLOB_ROOT / content_sha256[:2] / f"{content_sha256}.blob"


def _blob_uri(content_sha256: str) -> str:
    return f"mf://blob/{content_sha256}"


def _search_terms(text: str) -> str:
    terms: list[str] = []
    for match in _SEARCH_RUN.finditer(text):
        run = match.group(0)
        if _CJK_RUN.fullmatch(run):
            terms.extend(run)
            if len(run) > 1:
                terms.extend(run[index : index + 2] for index in range(len(run) - 1))
        else:
            terms.append(run.lower())
    return " ".join(terms)


def _fts_query(query: str) -> str:
    terms = _search_terms(query).split()
    if not terms:
        raise ValueError("search query must contain a word or number")
    escaped = [term.replace('"', '""') for term in terms]
    return "search_terms : (" + " AND ".join(f'"{term}"' for term in escaped) + ")"


def _make_snippet(content: str, query: str, *, max_chars: int = 240) -> str:
    highlighted = content
    raw_terms = [match.group(0) for match in _SEARCH_RUN.finditer(query)]
    for term in sorted(set(raw_terms), key=len, reverse=True):
        highlighted = re.sub(
            re.escape(term),
            lambda match: f"[{match.group(0)}]",
            highlighted,
            count=1,
            flags=re.IGNORECASE,
        )
    marker = highlighted.find("[")
    start = max(0, marker - max_chars // 3) if marker >= 0 else 0
    end = min(len(highlighted), start + max_chars)
    prefix = "… " if start else ""
    suffix = " …" if end < len(highlighted) else ""
    return prefix + highlighted[start:end].replace("\n", " ") + suffix


def _lastrowid(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a row id")
    return int(cursor.lastrowid)


def _now() -> str:
    return datetime.now(UTC).isoformat()
