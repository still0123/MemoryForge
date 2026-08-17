from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from urllib.parse import urlsplit

from memoryforge.compiler.wiki_facts import (
    AppliedCodeSymbolMatch,
    IndexedWikiFact,
    WikiFact,
    WikiFactSearchResult,
    citation_quote_matches_excerpt,
    parse_page_citations,
    parse_page_facts,
)
from memoryforge.core.errors import ChangeSetStoreError, WorkspaceError
from memoryforge.core.manifests import SourceManifestStore
from memoryforge.core.models import (
    ChangeSet,
    ClaimStatus,
    GitRepositoryRecord,
    GitRepositorySyncResult,
    ImportResult,
    LocalDocument,
    SearchResult,
    Sensitivity,
    SourceCategory,
    SourceVersionManifest,
)
from memoryforge.core.platform_lock import UnsafeLockFileError, exclusive_workspace_lock
from memoryforge.core.tokenization import _SEARCH_RUN, index_terms_text
from memoryforge.storage.apply_journal import recover_interrupted_apply
from memoryforge.storage.blob_store import (
    blob_relative_path,
    blob_uri,
    cleanup_blob_temps,
    cleanup_orphan_blob,
    no_follow_flag,
    read_blob_bytes,
    unlink_blob,
    verify_blob_hash,
    write_blob,
)
from memoryforge.storage.capture_inbox import drain_capture_spool
from memoryforge.storage.database import connect, connect_readonly
from memoryforge.storage.errors import (
    WorkspaceIntegrityError as WorkspaceIntegrityError,
)
from memoryforge.storage.errors import WorkspaceSecurityError as WorkspaceSecurityError
from memoryforge.storage.identifiers import CHAR_LOCATOR as _CHAR_LOCATOR
from memoryforge.storage.identifiers import CODE_IDENTIFIER as _CODE_IDENTIFIER
from memoryforge.storage.identifiers import CONTENT_SHA256 as _CONTENT_SHA256
from memoryforge.storage.identifiers import FEISHU_SOURCE_PATH as _FEISHU_SOURCE_PATH
from memoryforge.storage.identifiers import ORIGIN_MAIN_SOURCE_ID as _ORIGIN_MAIN_SOURCE_ID
from memoryforge.storage.projection import (
    candidate_page_sources as candidate_page_sources,
)
from memoryforge.storage.projection import indexed_facts as _indexed_facts
from memoryforge.storage.projection import (
    is_generated_navigation_page as is_generated_navigation_page,
)
from memoryforge.storage.projection import (
    is_generated_repository_overview as is_generated_repository_overview,
)
from memoryforge.storage.projection import is_stable_wiki_page_path as _is_stable_wiki_page_path
from memoryforge.storage.projection import normalize_page_facts as _normalize_page_facts
from memoryforge.storage.projection import normalize_page_sources as _normalize_page_sources
from memoryforge.storage.projection import (
    page_source_ids_from_frontmatter as _page_source_ids_from_frontmatter,
)
from memoryforge.storage.projection import (
    validate_changeset_page_sources as validate_changeset_page_sources,
)
from memoryforge.storage.projection import validate_stable_page_paths as _validate_stable_page_paths
from memoryforge.storage.version_store import GitVersionStore
from memoryforge.storage.workspace_contract import (
    _BASELINE_PATHS,
    _DEFAULT_AGENTS_MD,
    _DEFAULT_CONFIG_YAML,
    _DEFAULT_MEMORYFORGEIGNORE,
    _DEFAULT_SCHEMA_YAML,
    _GITIGNORE_RULES,
    _PROMPT_CONTEXT_LIMIT,
    _SCHEMA_STATEMENTS,
    _SOURCE_FTS_SCHEMA_STATEMENT,
    _WIKI_FACT_FTS_SCHEMA_STATEMENT,
    CAPTURE_SCHEMA,
    CONFLICT_SCHEMA,
    EGRESS_SCHEMA,
)
from memoryforge.storage.workspace_contract import (
    DATABASE_RELATIVE_PATH as DATABASE_RELATIVE_PATH,
)
from memoryforge.storage.workspace_contract import RAW_CATEGORIES as RAW_CATEGORIES
from memoryforge.storage.workspace_contract import WIKI_DIRECTORIES as WIKI_DIRECTORIES

_blob_relative_path = blob_relative_path
_blob_uri = blob_uri
_cleanup_blob_temps = cleanup_blob_temps
_cleanup_orphan_blob = cleanup_orphan_blob
_connect = connect
_connect_readonly = connect_readonly
_no_follow_flag = no_follow_flag
_read_blob_bytes = read_blob_bytes
_unlink_blob = unlink_blob
_verify_blob_hash = verify_blob_hash
_write_blob = write_blob

FACT_SEARCH_TERMS_USER_VERSION = 1


@dataclass(frozen=True)
class RegisteredFeishuDocument:
    document_id: str
    category: SourceCategory
    tags: tuple[str, ...]


@dataclass(frozen=True)
class CurrentGitSourceVersion:
    """Current immutable source revision imported from one registered Git checkout."""

    source_id: str
    source_version: int
    content_sha256: str
    relative_path: str
    commit_sha: str
    tags: tuple[str, ...]


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

    def prompt_context(self, *, max_chars: int = _PROMPT_CONTEXT_LIMIT) -> str:
        """Read the bounded Workspace contract used in model prompts."""
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 0:
            raise ValueError("max_chars must be a non-negative integer")
        sections: list[str] = []
        for label, path in (
            ("AGENTS.md", self.root / "AGENTS.md"),
            (".memoryforge/schema.yaml", self.schema_path),
        ):
            if path.is_file() and not path.is_symlink():
                sections.append(f"[{label}]\n{path.read_text(encoding='utf-8')}")
        return "\n\n".join(sections)[:max_chars]

    def require_current_source_versions(self, source_versions: Mapping[str, int]) -> None:
        """Reject an apply when one of its staged source revisions was superseded."""
        if not source_versions:
            return
        placeholders = ", ".join("?" for _ in source_versions)
        with _connect(self.index_path) as connection:
            rows = connection.execute(
                f"""
                SELECT s.source_id, v.id
                FROM sources AS s
                JOIN source_versions AS v ON v.source_id = s.id
                WHERE s.source_id IN ({placeholders}) AND v.is_current = 1
                """,
                tuple(source_versions),
            ).fetchall()
        current = {str(row[0]): int(row[1]) for row in rows}
        stale = sorted(
            source_id
            for source_id, version_id in source_versions.items()
            if current.get(source_id) != version_id
        )
        if stale:
            raise ChangeSetStoreError(
                "ChangeSet source versions are no longer current: " + ", ".join(stale)
            )

    def record_automation_decision(
        self,
        changeset_id: str,
        *,
        proposal_sha256: str,
        validation_sha256: str,
        policy_sha256: str,
        decision: str,
        risk: str,
        reason_codes: tuple[str, ...],
    ) -> None:
        """Record one automation decision for a staged ChangeSet (upsert)."""
        with _connect(self.index_path) as connection:
            connection.execute(
                """
                INSERT INTO automation_decisions(
                    changeset_id, proposal_sha256, validation_sha256, policy_sha256,
                    decision, risk, reason_codes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(changeset_id) DO UPDATE SET
                    proposal_sha256 = excluded.proposal_sha256,
                    validation_sha256 = excluded.validation_sha256,
                    policy_sha256 = excluded.policy_sha256,
                    decision = excluded.decision,
                    risk = excluded.risk,
                    reason_codes_json = excluded.reason_codes_json
                """,
                (
                    changeset_id,
                    proposal_sha256,
                    validation_sha256,
                    policy_sha256,
                    decision,
                    risk,
                    json.dumps(reason_codes, ensure_ascii=False),
                ),
            )

    def record_automation_event(
        self,
        event_type: str,
        *,
        changeset_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        """Append one automation lifecycle event to the audit log."""
        with _connect(self.index_path) as connection:
            connection.execute(
                """
                INSERT INTO automation_events(event_type, changeset_id, occurred_at, details_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    event_type,
                    changeset_id,
                    datetime.now(UTC).isoformat(),
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )

    def record_applied_source_versions(
        self,
        source_versions: Mapping[str, int],
    ) -> dict[str, int | None]:
        """Record versions for an apply attempt and return their previous mappings."""
        if not source_versions:
            return {}
        with _connect(self.index_path) as connection:
            previous_rows = connection.execute(
                """
                SELECT source_id, source_version_id
                FROM applied_source_versions
                WHERE source_id IN ({})
                """.format(", ".join("?" for _ in source_versions)),
                tuple(source_versions),
            ).fetchall()
            previous: dict[str, int | None] = {source_id: None for source_id in source_versions}
            previous.update({str(row[0]): int(row[1]) for row in previous_rows})
            connection.executemany(
                """
                INSERT INTO applied_source_versions(source_id, source_version_id)
                VALUES (?, ?)
                ON CONFLICT(source_id) DO UPDATE SET source_version_id = excluded.source_version_id
                """,
                source_versions.items(),
            )
        return previous

    def restore_applied_source_versions(self, previous: Mapping[str, int | None]) -> None:
        """Restore version mappings after an apply attempt fails before its Git commit."""
        if not previous:
            return
        with _connect(self.index_path) as connection:
            for source_id, source_version_id in previous.items():
                if source_version_id is None:
                    connection.execute(
                        "DELETE FROM applied_source_versions WHERE source_id = ?",
                        (source_id,),
                    )
                    continue
                connection.execute(
                    """
                    INSERT INTO applied_source_versions(source_id, source_version_id)
                    VALUES (?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        source_version_id = excluded.source_version_id
                    """,
                    (source_id, source_version_id),
                )

    def replace_applied_page_sources(
        self,
        page_sources: Mapping[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        """Replace source ownership for stable Wiki pages in one transaction."""
        normalized = _normalize_page_sources(page_sources)
        if not normalized:
            return {}
        placeholders = ", ".join("?" for _ in normalized)
        with _connect(self.index_path) as connection:
            rows = connection.execute(
                f"""
                SELECT page_path, source_id
                FROM page_sources
                WHERE page_path IN ({placeholders})
                ORDER BY page_path, source_id
                """,
                tuple(normalized),
            ).fetchall()
            previous: dict[str, list[str]] = {path: [] for path in normalized}
            for row in rows:
                previous[str(row["page_path"])].append(str(row["source_id"]))
            connection.executemany(
                "DELETE FROM page_sources WHERE page_path = ?",
                ((path,) for path in normalized),
            )
            connection.executemany(
                "INSERT INTO page_sources(page_path, source_id) VALUES (?, ?)",
                (
                    (page_path, source_id)
                    for page_path, source_ids in normalized.items()
                    for source_id in source_ids
                ),
            )
        return {path: tuple(source_ids) for path, source_ids in previous.items()}

    def remove_applied_page_sources(
        self,
        page_paths: Iterable[str],
    ) -> dict[str, tuple[str, ...]]:
        """Remove source ownership for pages approved for archival."""
        paths = tuple(sorted(set(page_paths)))
        if not paths:
            return {}
        if any(not _is_stable_wiki_page_path(path) for path in paths):
            raise ValueError("page source mappings must stay below wiki/pages/")
        placeholders = ", ".join("?" for _ in paths)
        with _connect(self.index_path) as connection:
            rows = connection.execute(
                f"""
                SELECT page_path, source_id
                FROM page_sources
                WHERE page_path IN ({placeholders})
                ORDER BY page_path, source_id
                """,
                paths,
            ).fetchall()
            previous: dict[str, list[str]] = {path: [] for path in paths}
            for row in rows:
                previous[str(row["page_path"])].append(str(row["source_id"]))
            connection.execute(
                f"DELETE FROM page_sources WHERE page_path IN ({placeholders})",
                paths,
            )
        return {path: tuple(source_ids) for path, source_ids in previous.items()}

    def restore_applied_page_sources(
        self,
        previous: Mapping[str, tuple[str, ...]],
    ) -> None:
        """Restore source ownership when apply fails before its Git commit."""
        restored = _normalize_page_sources(
            {page_path: source_ids for page_path, source_ids in previous.items() if source_ids}
        )
        paths = tuple(sorted(previous))
        if not paths:
            return
        if any(not _is_stable_wiki_page_path(path) for path in paths):
            raise ValueError("page source mappings must stay below wiki/pages/")
        placeholders = ", ".join("?" for _ in paths)
        with _connect(self.index_path) as connection:
            connection.execute(
                f"DELETE FROM page_sources WHERE page_path IN ({placeholders})",
                paths,
            )
            connection.executemany(
                "INSERT INTO page_sources(page_path, source_id) VALUES (?, ?)",
                (
                    (page_path, source_id)
                    for page_path, source_ids in restored.items()
                    for source_id in source_ids
                ),
            )

    def replace_applied_page_facts(
        self,
        page_facts: Mapping[str, tuple[WikiFact, ...]],
    ) -> dict[str, tuple[IndexedWikiFact, ...]]:
        """Replace grounded facts for stable Wiki pages and return prior rows."""
        normalized = _normalize_page_facts(page_facts)
        paths = tuple(sorted(normalized))
        if not paths:
            return {}
        placeholders = ", ".join("?" for _ in paths)
        with _connect(self.index_path) as connection:
            previous_rows = _indexed_facts(
                connection.execute(
                    f"""
                    SELECT
                        fact_id, page_path, repository_id, source_id, source_version,
                        locator, section_path, quote, routing_text, symbol, relation_type
                    FROM wiki_facts
                    WHERE page_path IN ({placeholders})
                    ORDER BY page_path, id
                    """,
                    paths,
                ).fetchall()
            )
            previous = {path: previous_rows.get(path, ()) for path in paths}
            repositories: dict[tuple[str, int], str | None] = {}
            for facts in normalized.values():
                for fact in facts:
                    key = (fact.source_id, fact.source_version)
                    if key in repositories:
                        continue
                    row = connection.execute(
                        """
                        SELECT revisions.repository_id
                        FROM sources
                        JOIN source_versions
                          ON source_versions.source_id = sources.id
                        JOIN applied_source_versions
                          ON applied_source_versions.source_id = sources.source_id
                         AND applied_source_versions.source_version_id = source_versions.id
                        LEFT JOIN git_source_revisions AS revisions
                          ON revisions.source_version_id = source_versions.id
                        WHERE sources.source_id = ? AND source_versions.id = ?
                        """,
                        key,
                    ).fetchone()
                    if row is None:
                        raise WorkspaceIntegrityError(
                            "Wiki fact does not identify an applied SourceVersion"
                        )
                    repositories[key] = (
                        str(row["repository_id"]) if row["repository_id"] is not None else None
                    )
            connection.executemany(
                "DELETE FROM wiki_facts WHERE page_path = ?",
                ((path,) for path in paths),
            )
            connection.executemany(
                """
                INSERT INTO wiki_facts(
                    fact_id, page_path, repository_id, source_id, source_version,
                    locator, section_path, quote, routing_text, symbol, relation_type,
                    search_terms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        fact.fact_id,
                        fact.page_path,
                        repositories[(fact.source_id, fact.source_version)],
                        fact.source_id,
                        fact.source_version,
                        fact.locator,
                        fact.section_path,
                        fact.quote,
                        fact.routing_text,
                        fact.symbol,
                        fact.relation_type,
                        _fact_search_terms(fact),
                    )
                    for path in paths
                    for fact in normalized[path]
                ),
            )
        return previous

    def restore_applied_page_facts(
        self,
        previous: Mapping[str, tuple[IndexedWikiFact, ...]],
    ) -> None:
        """Restore fact rows after a failed apply."""
        paths = tuple(sorted(previous))
        if not paths:
            return
        _validate_stable_page_paths(paths)
        with _connect(self.index_path) as connection:
            connection.executemany(
                "DELETE FROM wiki_facts WHERE page_path = ?",
                ((path,) for path in paths),
            )
            connection.executemany(
                """
                INSERT INTO wiki_facts(
                    fact_id, page_path, repository_id, source_id, source_version,
                    locator, section_path, quote, routing_text, symbol, relation_type,
                    search_terms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        fact.fact_id,
                        fact.page_path,
                        fact.repository_id,
                        fact.source_id,
                        fact.source_version,
                        fact.locator,
                        fact.section_path,
                        fact.quote,
                        fact.routing_text,
                        fact.symbol,
                        fact.relation_type,
                        _fact_search_terms(fact),
                    )
                    for path in paths
                    for fact in previous[path]
                ),
            )

    def page_paths_for_source(self, source_id: str) -> tuple[str, ...]:
        """Return deterministic stable Wiki paths currently supported by one source."""
        if _CONTENT_SHA256.fullmatch(source_id) is None:
            raise ValueError("source_id must be a SHA-256 hex digest")
        with _connect(self.index_path) as connection:
            rows = connection.execute(
                """
                SELECT page_path
                FROM page_sources
                WHERE source_id = ?
                ORDER BY page_path
                """,
                (source_id,),
            ).fetchall()
        return tuple(
            sorted(
                {path for row in rows if _is_stable_wiki_page_path(path := str(row["page_path"]))}
            )
        )

    def source_ids_for_page(self, page_path: str) -> tuple[str, ...]:
        """Return the current source ownership recorded for one stable Wiki page."""
        if not _is_stable_wiki_page_path(page_path):
            raise ValueError("page_path must be a Markdown file below wiki/pages/")
        with _connect(self.index_path) as connection:
            rows = connection.execute(
                """
                SELECT source_id
                FROM page_sources
                WHERE page_path = ?
                ORDER BY source_id
                """,
                (page_path,),
            ).fetchall()
        return tuple(str(row["source_id"]) for row in rows)

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

    @contextmanager
    def exclusive_lock(self) -> Iterator[None]:
        self.validate_internal_directory(self.internal_dir)
        try:
            with exclusive_workspace_lock(
                self.root,
                self.internal_dir / "workspace.lock",
            ):
                yield
        except UnsafeLockFileError as exc:
            raise WorkspaceSecurityError("workspace lock is unsafe") from exc

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
        workspace = cls(resolved)
        if version_store.has_repository():
            workspace.version_store.validate_metadata()
            workspace.current_commit()
            recover_interrupted_apply(
                workspace,
                rebuild_projection=rebuild_applied_projection,
            )
        _upgrade_workspace_contract(resolved)
        workspace_database(resolved)
        _backfill_source_manifests(resolved)
        if not workspace.config_path.is_file() or not workspace.schema_path.is_file():
            raise WorkspaceError("workspace configuration is missing")
        return workspace

    @classmethod
    def open_readonly(cls, root: Path) -> Workspace:
        resolved = _validated_workspace_root(root)
        _validate_workspace_identity_readonly(resolved)
        workspace = cls(resolved)
        if not workspace.config_path.is_file() or not workspace.schema_path.is_file():
            raise WorkspaceError("workspace configuration is missing")
        workspace.version_store.validate_metadata()
        workspace.current_commit()
        return workspace


def init_workspace(workspace: Path) -> Path:
    return Workspace.initialize(workspace).root


def register_git_checkout(
    workspace: Path,
    checkout: Path,
    *,
    sensitivity: Sensitivity = Sensitivity.LOCAL_ONLY,
) -> GitRepositoryRecord:
    """Register one existing local Git checkout without contacting its remote."""
    from memoryforge.adapters.git_adapter import snapshot_git_repository

    opened = Workspace.open(workspace)
    snapshot = snapshot_git_repository(checkout)
    checkout_path = str(snapshot.repository_root)
    repository_id = hashlib.sha256(snapshot.repository_identity.encode("utf-8")).hexdigest()
    repository_name = _git_repository_name(snapshot.repository_root, snapshot.remote_url)

    with _connect(opened.index_path) as connection:
        existing_checkout = connection.execute(
            "SELECT * FROM git_repositories WHERE checkout_path = ?",
            (checkout_path,),
        ).fetchone()
        existing_identity = connection.execute(
            "SELECT * FROM git_repositories WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()
        if existing_checkout is None and existing_identity is None:
            connection.execute(
                """
                INSERT INTO git_repositories(
                    repository_id, name, checkout_path, remote_name, remote_url, sensitivity,
                    registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repository_id,
                    repository_name,
                    checkout_path,
                    snapshot.remote_name,
                    snapshot.remote_url,
                    sensitivity.value,
                    _now(),
                ),
            )
        elif existing_identity is not None:
            connection.execute(
                """
                UPDATE git_repositories
                SET name = ?, checkout_path = ?, remote_name = ?, remote_url = ?, sensitivity = ?
                WHERE repository_id = ?
                """,
                (
                    repository_name,
                    checkout_path,
                    snapshot.remote_name,
                    snapshot.remote_url,
                    sensitivity.value,
                    repository_id,
                ),
            )
        elif existing_checkout is not None:
            if existing_checkout["last_synced_commit"] is not None:
                raise WorkspaceError("registered Git checkout identity changed after sync")
            connection.execute(
                """
                UPDATE git_repositories
                SET repository_id = ?, name = ?, remote_name = ?, remote_url = ?, sensitivity = ?
                WHERE checkout_path = ?
                """,
                (
                    repository_id,
                    repository_name,
                    snapshot.remote_name,
                    snapshot.remote_url,
                    sensitivity.value,
                    checkout_path,
                ),
            )
        row = connection.execute(
            "SELECT * FROM git_repositories WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()
    if row is None:
        raise WorkspaceIntegrityError("registered Git checkout could not be read")
    return _git_repository_record(row)


def _git_repository_name(repository_root: Path, remote_url: str | None) -> str:
    name = repository_root.name
    if re.fullmatch(r"[a-f0-9]{16,64}", name, re.IGNORECASE) is None or remote_url is None:
        return name
    parsed = urlsplit(remote_url)
    remote_path = parsed.path if parsed.scheme else remote_url.partition(":")[2]
    remote_name = PurePosixPath(remote_path).name.removesuffix(".git")
    return remote_name or name


def list_git_checkouts(workspace: Path) -> tuple[GitRepositoryRecord, ...]:
    """List the existing local Git checkouts registered with this workspace."""
    opened = Workspace.open(workspace)
    with _connect_readonly(opened.index_path) as connection:
        rows = connection.execute(
            "SELECT * FROM git_repositories ORDER BY registered_at, repository_id"
        ).fetchall()
    return tuple(_git_repository_record(row) for row in rows)


def find_code_module_repositories(
    workspace: Path,
    module_path: str,
) -> tuple[GitRepositoryRecord, ...]:
    """List repositories that own an applied code-module page."""
    opened = Workspace.open_readonly(workspace)
    section_path = f"Code module: {module_path} / Identity"
    with _connect_readonly(opened.index_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT repositories.*
            FROM wiki_facts AS facts
            JOIN git_repositories AS repositories
              ON repositories.repository_id = facts.repository_id
            WHERE facts.section_path = ?
            ORDER BY repositories.name, repositories.repository_id
            """,
            (section_path,),
        ).fetchall()
    return tuple(_git_repository_record(row) for row in rows)


def get_git_checkout_readonly(workspace: Path, repository_id: str) -> GitRepositoryRecord:
    """Read one registered Git checkout without upgrading or writing the workspace."""

    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        _validate_repository_scope(connection, repository_id)
        row = connection.execute(
            "SELECT * FROM git_repositories WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()
    if row is None:
        raise WorkspaceIntegrityError("registered Git checkout disappeared during read")
    return _git_repository_record(row)


def register_git_code_module(
    workspace: Path,
    repository_id: str,
    relative_path: str,
) -> str:
    """Select one committed Go/Python/TypeScript path for future Git syncs."""
    from memoryforge.adapters.git_adapter import scan_git_snapshot_code, snapshot_git_repository

    opened = Workspace.open(workspace)
    repository = _get_git_repository(opened, repository_id)
    snapshot = snapshot_git_repository(Path(repository.checkout_path))
    selected = relative_path.strip().replace("\\", "/").rstrip("/")
    if not scan_git_snapshot_code(snapshot, (selected,), sensitivity=repository.sensitivity):
        raise ValueError("code path contains no committed .go, .py, .ts, or .tsx files")
    with _connect(opened.index_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO git_code_modules(repository_id, relative_path)
            VALUES (?, ?)
            """,
            (repository_id, selected),
        )
    return selected


def list_git_code_modules(workspace: Workspace, repository_id: str) -> tuple[str, ...]:
    with _connect(workspace.index_path) as connection:
        rows = connection.execute(
            """
            SELECT relative_path FROM git_code_modules
            WHERE repository_id = ?
            ORDER BY relative_path
            """,
            (repository_id,),
        ).fetchall()
    return tuple(str(row["relative_path"]) for row in rows)


def list_current_git_source_versions(
    workspace: Path,
    repository_id: str,
) -> tuple[CurrentGitSourceVersion, ...]:
    """List current immutable Git sources without reading the live checkout."""

    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        _validate_repository_scope(connection, repository_id)
        rows = connection.execute(
            """
            SELECT
                sources.source_id,
                versions.id AS source_version,
                blobs.content_sha256,
                revisions.relative_path,
                revisions.commit_sha,
                versions.tags_json
            FROM git_source_revisions AS revisions
            JOIN source_versions AS versions
              ON versions.id = revisions.source_version_id
            JOIN sources ON sources.id = versions.source_id
            JOIN blobs ON blobs.id = versions.blob_id
            WHERE revisions.repository_id = ?
              AND versions.is_current = 1
            ORDER BY revisions.relative_path
            """,
            (repository_id,),
        ).fetchall()

    sources: list[CurrentGitSourceVersion] = []
    for row in rows:
        try:
            tags_value = json.loads(str(row["tags_json"]))
        except json.JSONDecodeError as exc:
            raise WorkspaceIntegrityError("Git source tags metadata is invalid") from exc
        if not isinstance(tags_value, list) or not all(isinstance(tag, str) for tag in tags_value):
            raise WorkspaceIntegrityError("Git source tags metadata is invalid")
        sources.append(
            CurrentGitSourceVersion(
                source_id=str(row["source_id"]),
                source_version=int(row["source_version"]),
                content_sha256=str(row["content_sha256"]),
                relative_path=str(row["relative_path"]),
                commit_sha=str(row["commit_sha"]),
                tags=tuple(tags_value),
            )
        )
    return tuple(sources)


def register_feishu_document(
    workspace: Path,
    document_id: str,
    *,
    category: SourceCategory,
    tags: tuple[str, ...],
) -> None:
    """Remember one successfully imported Feishu document for manual refreshes."""
    opened = Workspace.open(workspace)
    with _connect(opened.index_path) as connection:
        connection.execute(
            """
            INSERT INTO feishu_documents(document_id, category, tags_json, registered_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                category = excluded.category,
                tags_json = excluded.tags_json
            """,
            (document_id, category.value, json.dumps(tags), _now()),
        )


def reconcile_feishu_sources(
    workspace: Workspace,
    *,
    document_id: str,
    current_source_ids: set[str],
) -> int:
    """Deactivate Feishu sections absent from the completed document snapshot."""
    if re.fullmatch(r"[A-Za-z0-9_-]{8,}", document_id) is None:
        raise ValueError("Feishu document ID is invalid")
    if any(_CONTENT_SHA256.fullmatch(source_id) is None for source_id in current_source_ids):
        raise ValueError("Feishu source IDs must be SHA256 digests")
    base_path = f"feishu/{document_id}.md"
    section_prefix = f"feishu/{document_id}/"
    with _connect(workspace.index_path) as connection:
        rows = connection.execute(
            """
            SELECT versions.id, sources.source_id
            FROM sources
            JOIN source_versions AS versions
              ON versions.source_id = sources.id AND versions.is_current = 1
            WHERE sources.source_path = ?
               OR sources.source_path LIKE ?
            """,
            (base_path, section_prefix + "%"),
        ).fetchall()
        stale_version_ids = [
            int(row["id"]) for row in rows if str(row["source_id"]) not in current_source_ids
        ]
        connection.executemany(
            "UPDATE source_versions SET is_current = 0 WHERE id = ?",
            ((version_id,) for version_id in stale_version_ids),
        )
    return len(stale_version_ids)


def list_feishu_documents(workspace: Path) -> tuple[RegisteredFeishuDocument, ...]:
    """List Feishu documents previously imported through this workspace."""
    opened = Workspace.open(workspace)
    with _connect(opened.index_path) as connection:
        _backfill_feishu_documents(connection)
        rows = connection.execute(
            "SELECT document_id, category, tags_json FROM feishu_documents ORDER BY document_id"
        ).fetchall()
    documents: list[RegisteredFeishuDocument] = []
    for row in rows:
        tags = _registered_feishu_tags(row["tags_json"])
        documents.append(
            RegisteredFeishuDocument(
                document_id=str(row["document_id"]),
                category=SourceCategory(str(row["category"])),
                tags=tuple(tags),
            )
        )
    return tuple(documents)


def _backfill_feishu_documents(connection: sqlite3.Connection) -> None:
    """Register documents imported before manual refreshes existed."""
    rows = connection.execute(
        """
        SELECT s.source_path, v.category, v.tags_json
        FROM sources AS s
        JOIN source_versions AS v ON v.source_id = s.id
        WHERE v.is_current = 1
          AND s.source_path LIKE 'feishu/%.md'
          AND instr(substr(s.source_path, 8), '/') = 0
        """
    ).fetchall()
    for row in rows:
        match = _FEISHU_SOURCE_PATH.fullmatch(str(row["source_path"]))
        if match is None:
            continue
        tags = tuple(tag for tag in _registered_feishu_tags(row["tags_json"]) if tag != "feishu")
        connection.execute(
            """
            INSERT OR IGNORE INTO feishu_documents(document_id, category, tags_json, registered_at)
            VALUES (?, ?, ?, ?)
            """,
            (match["document_id"], str(row["category"]), json.dumps(tags), _now()),
        )


def _registered_feishu_tags(value: object) -> tuple[str, ...]:
    try:
        tags = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise WorkspaceIntegrityError("registered Feishu document tags are invalid") from exc
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise WorkspaceIntegrityError("registered Feishu document tags are invalid")
    return tuple(tags)


def sync_git_checkout(workspace: Path, repository_id: str) -> GitRepositorySyncResult:
    """Compatibility wrapper for :func:`memoryforge.adapters.git_sync.sync_git_checkout`."""
    from memoryforge.adapters.git_sync import sync_git_checkout as sync

    return sync(workspace, repository_id)


def _reconcile_git_snapshot_sources(
    workspace: Workspace,
    *,
    repository_id: str,
    current_paths: set[str],
) -> None:
    """Mark current Git sources absent from a completed snapshot as no longer current."""
    with _connect(workspace.index_path) as connection:
        rows = connection.execute(
            """
            SELECT versions.id, revisions.relative_path
            FROM source_versions AS versions
            JOIN git_source_revisions AS revisions
              ON revisions.source_version_id = versions.id
            WHERE revisions.repository_id = ? AND versions.is_current = 1
            """,
            (repository_id,),
        ).fetchall()
        stale_version_ids = [
            int(row["id"]) for row in rows if str(row["relative_path"]) not in current_paths
        ]
        connection.executemany(
            "UPDATE source_versions SET is_current = 0 WHERE id = ?",
            ((version_id,) for version_id in stale_version_ids),
        )


def register_folder_import(workspace: Workspace, folder_id: str) -> None:
    """Register one opaque local folder identity without persisting its absolute root."""
    if re.fullmatch(r"[a-f0-9]{64}", folder_id) is None:
        raise ValueError("folder_id must be a SHA256 digest")
    with _connect(workspace.index_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO folder_imports(folder_id, registered_at)
            VALUES (?, ?)
            """,
            (folder_id, _now()),
        )


def record_folder_source_version(
    workspace: Workspace,
    *,
    folder_id: str,
    source_id: str,
    relative_path: str,
) -> None:
    """Bind one current SourceVersion to its folder snapshot and relative path."""
    if re.fullmatch(r"[a-f0-9]{64}", folder_id) is None:
        raise ValueError("folder_id must be a SHA256 digest")
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in relative_path
    ):
        raise ValueError("folder source path must be a safe POSIX relative path")
    with _connect(workspace.index_path) as connection:
        version_row = connection.execute(
            """
            SELECT versions.id
            FROM source_versions AS versions
            JOIN sources ON sources.id = versions.source_id
            WHERE sources.source_id = ? AND versions.is_current = 1
            """,
            (source_id,),
        ).fetchone()
        if version_row is None:
            raise WorkspaceIntegrityError("imported folder SourceVersion could not be found")
        version_id = int(version_row["id"])
        connection.execute(
            "DELETE FROM folder_source_versions WHERE source_version_id = ?",
            (version_id,),
        )
        connection.execute(
            """
            INSERT INTO folder_source_versions(source_version_id, folder_id, relative_path)
            VALUES (?, ?, ?)
            """,
            (version_id, folder_id, path.as_posix()),
        )


def reconcile_folder_sources(
    workspace: Workspace,
    *,
    folder_id: str,
    current_paths: set[str],
) -> int:
    """Deactivate current folder sources absent from a completed folder snapshot."""
    if re.fullmatch(r"[a-f0-9]{64}", folder_id) is None:
        raise ValueError("folder_id must be a SHA256 digest")
    with _connect(workspace.index_path) as connection:
        rows = connection.execute(
            """
            SELECT versions.id, folder_versions.relative_path
            FROM folder_source_versions AS folder_versions
            JOIN source_versions AS versions
              ON versions.id = folder_versions.source_version_id
            WHERE folder_versions.folder_id = ? AND versions.is_current = 1
            """,
            (folder_id,),
        ).fetchall()
        stale_version_ids = [
            int(row["id"]) for row in rows if str(row["relative_path"]) not in current_paths
        ]
        connection.executemany(
            "UPDATE source_versions SET is_current = 0 WHERE id = ?",
            ((version_id,) for version_id in stale_version_ids),
        )
    return len(stale_version_ids)


def deactivate_current_source(
    workspace: Workspace,
    *,
    source_id: str,
    expected_source_path: str,
) -> bool:
    """Deactivate one explicitly identified current source while preserving history."""
    if re.fullmatch(r"[a-f0-9]{64}", source_id) is None:
        raise ValueError("source_id must be a SHA256 digest")
    expected_path = PurePosixPath(expected_source_path)
    if (
        expected_path.is_absolute()
        or not expected_path.parts
        or any(part in {"", ".", ".."} for part in expected_path.parts)
        or "\\" in expected_source_path
    ):
        raise ValueError("expected source path must be a safe POSIX relative path")
    with _connect(workspace.index_path) as connection:
        row = connection.execute(
            """
            SELECT versions.id, sources.source_path
            FROM sources
            LEFT JOIN source_versions AS versions
              ON versions.source_id = sources.id AND versions.is_current = 1
            WHERE sources.source_id = ?
            """,
            (source_id,),
        ).fetchone()
        if row is None or row["id"] is None:
            return False
        if str(row["source_path"]) != expected_path.as_posix():
            raise WorkspaceIntegrityError("source identity does not match its expected path")
        connection.execute(
            "UPDATE source_versions SET is_current = 0 WHERE id = ?",
            (int(row["id"]),),
        )
    return True


def _get_git_repository(workspace: Workspace, repository_id: str) -> GitRepositoryRecord:
    with _connect(workspace.index_path) as connection:
        row = connection.execute(
            "SELECT * FROM git_repositories WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown Git repository: {repository_id}")
    return _git_repository_record(row)


def _record_git_source_revision(
    workspace: Workspace,
    *,
    source_id: str,
    repository_id: str,
    relative_path: str,
    commit_sha: str,
) -> None:
    with _connect(workspace.index_path) as connection:
        version_row = connection.execute(
            """
            SELECT v.id
            FROM source_versions AS v
            JOIN sources AS s ON s.id = v.source_id
            WHERE s.source_id = ? AND v.is_current = 1
            """,
            (source_id,),
        ).fetchone()
        if version_row is None:
            raise WorkspaceIntegrityError("imported Git source version could not be found")
        current_version_id = int(version_row["id"])
        connection.execute(
            """
            DELETE FROM git_source_revisions
            WHERE source_version_id = ?
               OR (repository_id = ? AND relative_path = ? AND commit_sha = ?)
            """,
            (current_version_id, repository_id, relative_path, commit_sha),
        )
        connection.execute(
            """
            INSERT INTO git_source_revisions(
                source_version_id, repository_id, relative_path, commit_sha
            ) VALUES (?, ?, ?, ?)
            """,
            (current_version_id, repository_id, relative_path, commit_sha),
        )


def _current_git_paths(
    workspace: Workspace,
    repository_id: str,
    commit_sha: str,
    sensitivity: Sensitivity,
    *,
    code_wiki_version: str,
) -> set[str]:
    with _connect(workspace.index_path) as connection:
        rows = connection.execute(
            """
            SELECT revisions.relative_path
            FROM git_source_revisions AS revisions
            JOIN source_versions AS versions
              ON versions.id = revisions.source_version_id
            WHERE revisions.repository_id = ?
              AND revisions.commit_sha = ?
              AND versions.is_current = 1
              AND versions.sensitivity = ?
              AND (
                instr(versions.tags_json, '"code"') = 0
                OR instr(versions.tags_json, ?) > 0
                OR instr(versions.tags_json, '"code-module"') > 0
              )
            """,
            (
                repository_id,
                commit_sha,
                sensitivity.value,
                json.dumps(code_wiki_version),
            ),
        ).fetchall()
    return {str(row["relative_path"]) for row in rows}


def _git_repository_record(row: sqlite3.Row) -> GitRepositoryRecord:
    return GitRepositoryRecord(
        repository_id=str(row["repository_id"]),
        name=str(row["name"]),
        checkout_path=str(row["checkout_path"]),
        remote_name=str(row["remote_name"]) if row["remote_name"] is not None else None,
        remote_url=str(row["remote_url"]) if row["remote_url"] is not None else None,
        sensitivity=Sensitivity(str(row["sensitivity"])),
        registered_at=datetime.fromisoformat(str(row["registered_at"])),
        last_synced_commit=(
            str(row["last_synced_commit"]) if row["last_synced_commit"] is not None else None
        ),
    )


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
        Path(".memoryforge/capture/spool"),
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
        connection.execute(f"PRAGMA user_version = {FACT_SEARCH_TERMS_USER_VERSION}")
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
    try:
        with _connect(database_path) as connection:
            drain_capture_spool(root, connection)
    except Exception as exc:  # noqa: BLE001 - drain failure is non-fatal
        logging.warning("capture spool drain failed: %s", exc)
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
        with _connect_readonly(database_path) as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise WorkspaceError("MemoryForge workspace database is invalid") from exc
    tables = {str(row[0]) for row in rows}
    unified = {"sources", "blobs", "source_versions", "source_fts"} <= tables
    origin_main = {"source_documents", "source_fts"} <= tables
    if origin_main:
        with _connect_readonly(database_path) as connection:
            document_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(source_documents)").fetchall()
            }
            fts_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(source_fts)").fetchall()
            }
        origin_main = {
            "source_id",
            "uri",
            "content_sha256",
            "media_type",
            "category",
            "imported_at",
            "observed_at",
            "sensitivity",
            "tags_json",
        } <= document_columns and {"source_id", "title", "body"} <= fts_columns
    if not unified and not origin_main:
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


def search_sources(
    workspace: Path,
    query: str,
    *,
    limit: int = 10,
    repository_id: str | None = None,
    require_all_terms: bool = True,
) -> list[SearchResult]:
    if not query.strip():
        raise ValueError("search query must not be empty")
    if limit < 1 or limit > 100:
        raise ValueError("search limit must be between 1 and 100")

    opened = Workspace.open_readonly(workspace)
    root = opened.root
    match_query = _fts_query(query, require_all_terms=require_all_terms)
    with _connect_readonly(opened.index_path) as connection:
        parameters: list[object] = [match_query]
        repository_filter = ""
        if repository_id is not None:
            _validate_repository_scope(connection, repository_id)
            repository_filter = """
              AND EXISTS (
                SELECT 1
                FROM git_source_revisions AS scoped_revisions
                WHERE scoped_revisions.source_version_id = v.id
                  AND scoped_revisions.repository_id = ?
              )
            """
            parameters.append(repository_id)
        parameters.append(limit)
        rows = connection.execute(
            f"""
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
            {repository_filter}
            ORDER BY bm25(source_fts), v.observed_at DESC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()

    results: list[SearchResult] = []
    for row in rows:
        content_sha256 = str(row["content_sha256"])
        snapshot_relative = str(row["snapshot_path"])
        _verify_blob_hash(
            root,
            content_sha256,
            Path(snapshot_relative),
            repair_permissions=False,
        )
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


def find_applied_page_paths(
    workspace: Path,
    query: str,
    *,
    limit: int = 10,
    repository_id: str | None = None,
    require_all_terms: bool = True,
) -> tuple[str, ...]:
    """Use FTS5 to find Wiki pages backed by applied source revisions only."""
    if not query.strip():
        raise ValueError("search query must not be empty")
    if limit < 1 or limit > 100:
        raise ValueError("search limit must be between 1 and 100")

    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        parameters: list[object] = [_fts_query(query, require_all_terms=require_all_terms)]
        repository_filter = ""
        if repository_id is not None:
            _validate_repository_scope(connection, repository_id)
            repository_filter = """
              AND revisions.repository_id = ?
            """
            parameters.append(repository_id)
        rows = connection.execute(
            f"""
            SELECT ps.page_path
            FROM source_fts
            JOIN source_versions AS v ON v.id = source_fts.rowid
            JOIN sources AS s ON s.id = v.source_id
            JOIN applied_source_versions AS applied
             ON applied.source_id = s.source_id
             AND applied.source_version_id = v.id
            JOIN page_sources AS ps ON ps.source_id = s.source_id
            LEFT JOIN git_source_revisions AS revisions ON revisions.source_version_id = v.id
            WHERE source_fts MATCH ?
            {repository_filter}
            ORDER BY bm25(source_fts), ps.page_path
            """,
            tuple(parameters),
        ).fetchall()
    paths: list[str] = []
    for row in rows:
        path = str(row["page_path"])
        if path not in paths:
            paths.append(path)
        if len(paths) == limit:
            break
    return tuple(paths)


def find_applied_wiki_fact_page_paths(
    workspace: Path,
    terms: Iterable[str],
    *,
    limit: int = 10,
    repository_id: str | None = None,
) -> tuple[str, ...]:
    """Find applied pages by substring terms already stored as Wiki facts."""
    normalized = tuple(dict.fromkeys(term.strip() for term in terms if term.strip()))[:32]
    if not normalized:
        return ()
    if limit < 1 or limit > 100:
        raise ValueError("fact page result limit must be between 1 and 100")

    values = ", ".join("(?)" for _ in normalized)
    parameters: list[object] = list(normalized)
    repository_filter = ""
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        if repository_id is not None:
            _validate_repository_scope(connection, repository_id)
            repository_filter = "AND facts.repository_id = ?"
            parameters.append(repository_id)
        parameters.append(limit)
        rows = connection.execute(
            f"""
            WITH query_terms(term) AS (VALUES {values}),
            hits AS (
                SELECT DISTINCT facts.page_path, query_terms.term
                FROM wiki_facts AS facts
                JOIN applied_source_versions AS applied
                  ON applied.source_id = facts.source_id
                 AND applied.source_version_id = facts.source_version
                JOIN query_terms
                  ON instr(facts.quote, query_terms.term) > 0
                  OR instr(facts.routing_text, query_terms.term) > 0
                WHERE 1 = 1 {repository_filter}
            ),
            frequencies AS (
                SELECT term, COUNT(*) AS page_count
                FROM hits
                GROUP BY term
            )
            SELECT hits.page_path
            FROM hits
            JOIN frequencies USING (term)
            GROUP BY hits.page_path
            ORDER BY SUM(1.0 / frequencies.page_count) DESC,
                     COUNT(*) DESC,
                     hits.page_path
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
    return tuple(str(row["page_path"]) for row in rows)


def find_applied_code_symbol_facts(
    workspace: Path,
    identifiers: Iterable[str],
    *,
    limit: int = 100,
    repository_id: str | None = None,
) -> tuple[AppliedCodeSymbolMatch, ...]:
    """Find exact parser-derived Symbols from the applied Code Index projection."""
    normalized = tuple(dict.fromkeys(identifier.strip() for identifier in identifiers))
    if not normalized or any(not identifier for identifier in normalized):
        raise ValueError("code Symbol lookup requires at least one identifier")
    if any(_CODE_IDENTIFIER.fullmatch(identifier) is None for identifier in normalized):
        raise ValueError("code Symbol identifiers must be dotted programming identifiers")
    if limit < 1 or limit > 100:
        raise ValueError("code Symbol result limit must be between 1 and 100")

    unqualified = tuple(identifier for identifier in normalized if "." not in identifier)
    exact_placeholders = ", ".join("?" for _ in normalized)
    conditions = [f"facts.symbol IN ({exact_placeholders})"]
    parameters: list[object] = list(normalized)
    for identifier in unqualified:
        conditions.append("substr(facts.symbol, -(length(?) + 1)) = '.' || ?")
        parameters.extend((identifier, identifier))

    repository_filter = ""
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'wiki_facts'"
            ).fetchone()
            is None
        ):
            return ()
        if repository_id is not None:
            _validate_repository_scope(connection, repository_id)
            repository_filter = "AND facts.repository_id = ?"
            parameters.append(repository_id)
        rows = connection.execute(
            f"""
            SELECT
                facts.fact_id,
                facts.page_path,
                facts.repository_id,
                facts.source_id,
                facts.source_version,
                facts.locator,
                facts.section_path,
                facts.quote,
                facts.routing_text,
                facts.symbol,
                facts.relation_type
            FROM wiki_facts AS facts
            JOIN applied_source_versions AS applied
              ON applied.source_id = facts.source_id
             AND applied.source_version_id = facts.source_version
            WHERE facts.symbol IS NOT NULL
              AND ({" OR ".join(conditions)})
              {repository_filter}
            ORDER BY facts.symbol, facts.page_path, facts.id
            """,
            tuple(parameters),
        ).fetchall()

    matches: list[AppliedCodeSymbolMatch] = []
    for row in rows:
        symbol = str(row["symbol"])
        exact_identifier = next(
            (identifier for identifier in normalized if "." in identifier and symbol == identifier),
            None,
        )
        matched_identifier = exact_identifier or next(
            (
                candidate
                for candidate in unqualified
                if symbol == candidate or symbol.endswith(f".{candidate}")
            ),
            None,
        )
        if matched_identifier is None:
            continue
        matches.append(
            AppliedCodeSymbolMatch(
                fact_id=str(row["fact_id"]),
                page_path=str(row["page_path"]),
                repository_id=(
                    str(row["repository_id"]) if row["repository_id"] is not None else None
                ),
                source_id=str(row["source_id"]),
                source_version=int(row["source_version"]),
                locator=str(row["locator"]),
                section_path=str(row["section_path"]),
                quote=str(row["quote"]),
                routing_text=str(row["routing_text"]),
                symbol=symbol,
                relation_type=(
                    str(row["relation_type"]) if row["relation_type"] is not None else None
                ),
                identifier=matched_identifier,
                match_kind=("qualified_name" if exact_identifier is not None else "display_name"),
            )
        )
    return tuple(
        sorted(
            matches,
            key=lambda match: (
                match.match_kind != "qualified_name",
                match.identifier,
                match.symbol or "",
                match.page_path,
                match.fact_id,
            ),
        )[:limit]
    )


def search_wiki_facts(
    workspace: Path,
    query: str,
    *,
    limit: int = 10,
    repository_id: str | None = None,
    page_paths: Iterable[str] | None = None,
) -> tuple[WikiFactSearchResult, ...]:
    """Search grounded facts from applied Wiki pages with optional scopes."""
    if not query.strip():
        raise ValueError("fact search query must not be empty")
    if limit < 1 or limit > 100:
        raise ValueError("fact search limit must be between 1 and 100")
    paths = tuple(sorted(set(page_paths))) if page_paths is not None else ()
    if page_paths is not None:
        _validate_stable_page_paths(paths)
        if not paths:
            return ()

    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        fts_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(wiki_fact_fts)").fetchall()
        }
        parameters: list[object] = [
            _wiki_fact_fts_query(query, projected="search_terms" in fts_columns)
        ]
        repository_filter = ""
        if repository_id is not None:
            _validate_repository_scope(connection, repository_id)
            repository_filter = "AND facts.repository_id = ?"
            parameters.append(repository_id)
        page_filter = ""
        if paths:
            page_filter = "AND facts.page_path IN ({})".format(", ".join("?" for _ in paths))
            parameters.extend(paths)
        parameters.append(limit)
        rows = connection.execute(
            f"""
            SELECT
                facts.fact_id,
                facts.page_path,
                facts.repository_id,
                facts.source_id,
                facts.source_version,
                facts.locator,
                facts.section_path,
                facts.quote,
                facts.routing_text,
                facts.symbol,
                facts.relation_type,
                bm25(wiki_fact_fts) AS rank
            FROM wiki_fact_fts
            JOIN wiki_facts AS facts ON facts.id = wiki_fact_fts.rowid
            JOIN applied_source_versions AS applied
              ON applied.source_id = facts.source_id
             AND applied.source_version_id = facts.source_version
            WHERE wiki_fact_fts MATCH ?
              {repository_filter}
              {page_filter}
            ORDER BY rank, facts.page_path, facts.id
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
    return tuple(
        WikiFactSearchResult(
            fact_id=str(row["fact_id"]),
            page_path=str(row["page_path"]),
            repository_id=(str(row["repository_id"]) if row["repository_id"] is not None else None),
            source_id=str(row["source_id"]),
            source_version=int(row["source_version"]),
            locator=str(row["locator"]),
            section_path=str(row["section_path"]),
            quote=str(row["quote"]),
            routing_text=str(row["routing_text"]),
            symbol=str(row["symbol"]) if row["symbol"] is not None else None,
            relation_type=(str(row["relation_type"]) if row["relation_type"] is not None else None),
            rank=float(row["rank"]),
        )
        for row in rows
    )


def repository_page_paths(workspace: Path, repository_id: str) -> tuple[str, ...]:
    """Return applied Wiki pages backed by one registered Git repository."""
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        _validate_repository_scope(connection, repository_id)
        rows = connection.execute(
            """
            SELECT DISTINCT page_sources.page_path
            FROM page_sources
            JOIN sources ON sources.source_id = page_sources.source_id
            JOIN applied_source_versions AS applied
              ON applied.source_id = sources.source_id
            JOIN git_source_revisions AS revisions
              ON revisions.source_version_id = applied.source_version_id
            WHERE revisions.repository_id = ?
            ORDER BY page_sources.page_path
            """,
            (repository_id,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def read_source_version_text(
    workspace: Path,
    *,
    source_id: str,
    source_version: int,
) -> str:
    """Read and verify the complete UTF-8 text of one immutable SourceVersion."""
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        row = connection.execute(
            """
            SELECT b.content_sha256, b.snapshot_path
            FROM sources AS s
            JOIN source_versions AS v ON v.source_id = s.id
            JOIN blobs AS b ON b.id = v.blob_id
            WHERE s.source_id = ? AND v.id = ?
            """,
            (source_id, source_version),
        ).fetchone()
    if row is None:
        raise WorkspaceIntegrityError("Citation does not identify an imported SourceVersion")

    evidence = _read_blob_bytes(
        opened.root,
        str(row["content_sha256"]),
        Path(str(row["snapshot_path"])),
        repair_permissions=False,
    )
    try:
        return evidence.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceIntegrityError("Citation evidence is not valid UTF-8") from exc


def read_source_excerpt(
    workspace: Path,
    *,
    source_id: str,
    source_version: int,
    locator: str,
) -> str:
    """Read one cited range from immutable evidence on explicit user request."""
    locator_match = _CHAR_LOCATOR.fullmatch(locator)
    if locator_match is None:
        raise WorkspaceIntegrityError("Citation locator is invalid")

    text = read_source_version_text(
        workspace,
        source_id=source_id,
        source_version=source_version,
    )
    start = int(locator_match.group("start"))
    end = int(locator_match.group("end"))
    if end > len(text):
        raise WorkspaceIntegrityError("Citation locator is outside immutable evidence")
    return text[start:end]


def is_public_source_version(
    workspace: Path,
    *,
    source_id: str,
    source_version: int,
) -> bool:
    """Return whether one immutable source revision may be sent to an LLM."""
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM sources AS s
            JOIN source_versions AS v ON v.source_id = s.id
            WHERE s.source_id = ? AND v.id = ? AND v.sensitivity = ?
            """,
            (source_id, source_version, Sensitivity.PUBLIC.value),
        ).fetchone()
    return row is not None


def is_applied_source_version(
    workspace: Path,
    *,
    source_id: str,
    source_version: int,
) -> bool:
    """Return whether one immutable SourceVersion is currently applied."""
    if _CONTENT_SHA256.fullmatch(source_id) is None or source_version < 1:
        return False
    opened = Workspace.open_readonly(workspace)
    with _connect_readonly(opened.index_path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM applied_source_versions
            WHERE source_id = ? AND source_version_id = ?
            """,
            (source_id, source_version),
        ).fetchone()
    return row is not None


def _validate_repository_scope(connection: sqlite3.Connection, repository_id: str) -> None:
    if _CONTENT_SHA256.fullmatch(repository_id) is None:
        raise ValueError("repository_id must be a SHA-256 hex digest")
    row = connection.execute(
        "SELECT 1 FROM git_repositories WHERE repository_id = ?",
        (repository_id,),
    ).fetchone()
    if row is None:
        raise ValueError("unknown Git repository: " + repository_id)


def validate_candidate_page_evidence(
    workspace: Workspace,
    candidate_files: Mapping[str, str],
) -> None:
    """Verify candidate citations against immutable SourceVersions before use."""
    source_texts: dict[tuple[str, int], str] = {}
    for path, content in candidate_files.items():
        if not _is_stable_wiki_page_path(path):
            continue
        citations = parse_page_citations(content)
        declared_sources = _page_source_ids_from_frontmatter(content)
        represented_sources = {citation["source_id"] for citation in citations}
        if declared_sources and not set(declared_sources) <= represented_sources:
            raise WorkspaceIntegrityError(
                f"candidate Wiki page lacks evidence for a declared source: {path}"
            )
        for citation in citations:
            key = (citation["source_id"], citation["source_version"])
            text = source_texts.get(key)
            if text is None:
                text = read_source_version_text(
                    workspace.root,
                    source_id=key[0],
                    source_version=key[1],
                )
                source_texts[key] = text
            match = _CHAR_LOCATOR.fullmatch(citation["locator"])
            if match is None:
                raise WorkspaceIntegrityError("Citation locator is invalid")
            start = int(match.group("start"))
            end = int(match.group("end"))
            if start >= end or end > len(text):
                raise WorkspaceIntegrityError("Citation locator is outside immutable evidence")
            if citation.get("grounding", "exact") == "exact" and not citation_quote_matches_excerpt(
                citation["quote"], text[start:end]
            ):
                raise WorkspaceIntegrityError(
                    f"candidate Wiki fact does not match immutable evidence: {path}"
                )


def rebuild_applied_projection(workspace: Workspace) -> None:
    """Rebuild applied source, page, and fact indexes from the stable Wiki tree."""
    pages_root = workspace.root / "wiki/pages"
    candidate_files: dict[str, str] = {}
    if pages_root.is_dir() and not pages_root.is_symlink():
        for page in sorted(pages_root.rglob("*.md")):
            if page.is_symlink() or not page.is_file():
                raise WorkspaceSecurityError("Wiki projection rejects unsafe page paths")
            candidate_files[page.relative_to(workspace.root).as_posix()] = page.read_text(
                encoding="utf-8"
            )
    validate_candidate_page_evidence(workspace, candidate_files)
    page_sources = candidate_page_sources(candidate_files)
    page_facts = {
        path: parse_page_facts(path, content) for path, content in candidate_files.items()
    }
    source_versions: dict[str, int] = {}
    for facts in page_facts.values():
        for fact in facts:
            previous = source_versions.setdefault(fact.source_id, fact.source_version)
            if previous != fact.source_version:
                raise WorkspaceIntegrityError("Wiki projection mixes SourceVersions")
    if set(source_versions) != {
        source_id for source_ids in page_sources.values() for source_id in source_ids
    }:
        raise WorkspaceIntegrityError("Wiki projection sources do not match cited facts")

    with _connect(workspace.index_path) as connection:
        repositories: dict[tuple[str, int], str | None] = {}
        for source_id, source_version in source_versions.items():
            row = connection.execute(
                """
                SELECT revisions.repository_id
                FROM sources
                JOIN source_versions ON source_versions.source_id = sources.id
                LEFT JOIN git_source_revisions AS revisions
                  ON revisions.source_version_id = source_versions.id
                WHERE sources.source_id = ? AND source_versions.id = ?
                """,
                (source_id, source_version),
            ).fetchone()
            if row is None:
                raise WorkspaceIntegrityError("Wiki projection cites an unknown SourceVersion")
            repositories[(source_id, source_version)] = (
                str(row["repository_id"]) if row["repository_id"] is not None else None
            )
        connection.execute("DELETE FROM wiki_facts")
        connection.execute("DELETE FROM page_sources")
        connection.execute("DELETE FROM applied_source_versions")
        connection.executemany(
            "INSERT INTO applied_source_versions(source_id, source_version_id) VALUES (?, ?)",
            sorted(source_versions.items()),
        )
        connection.executemany(
            "INSERT INTO page_sources(page_path, source_id) VALUES (?, ?)",
            (
                (page_path, source_id)
                for page_path, source_ids in sorted(page_sources.items())
                for source_id in source_ids
            ),
        )
        connection.executemany(
            """
            INSERT INTO wiki_facts(
                fact_id, page_path, repository_id, source_id, source_version,
                locator, section_path, quote, routing_text, symbol, relation_type,
                search_terms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    fact.fact_id,
                    fact.page_path,
                    repositories[(fact.source_id, fact.source_version)],
                    fact.source_id,
                    fact.source_version,
                    fact.locator,
                    fact.section_path,
                    fact.quote,
                    fact.routing_text,
                    fact.symbol,
                    fact.relation_type,
                    _fact_search_terms(fact),
                )
                for path in sorted(page_facts)
                for fact in sorted(page_facts[path], key=lambda item: item.fact_id)
            ),
        )


def _fact_search_terms(fact: WikiFact | IndexedWikiFact) -> str:
    """Derive the FTS search-terms projection for one indexed wiki fact."""
    return _fact_search_terms_from_values(
        fact.routing_text,
        fact.quote,
        fact.section_path,
        fact.symbol,
    )


def _fact_search_terms_from_values(
    routing_text: object,
    quote: object,
    section_path: object,
    symbol: object,
) -> str:
    return index_terms_text(
        "\n".join(
            str(part) for part in (routing_text, quote, section_path, symbol) if part
        )
    )


def reindex_fact_search_terms(workspace: Path, *, dry_run: bool = False) -> dict[str, object]:
    """Recompute the ``wiki_facts.search_terms`` projection and rebuild its FTS.

    Derived-state migration (spec PROGRESSIVE_STRUCTURE_RETRIEVAL_SPEC §3/§4):
    runs in one transaction, is idempotent, and records the schema level in
    ``PRAGMA user_version``. Failure rolls back; a code rollback re-runs this
    command against the previous tokenizer.
    """
    opened = Workspace.open_readonly(workspace)
    with opened.exclusive_lock(), _connect(opened.index_path) as connection:
        version, migration_required, facts, fts_rows = _fact_search_projection_state(connection)
        database_bytes_before = _database_bytes(connection)
        if dry_run:
            return {
                "status": "dry_run",
                "migration_required": migration_required,
                "facts": facts,
                "fts_rows": fts_rows,
                "user_version": version,
                "database_bytes_before": database_bytes_before,
            }
        if not migration_required:
            return {
                "status": "up_to_date",
                "facts": facts,
                "fts_rows": fts_rows,
                "backfilled": 0,
                "user_version": version,
                "database_bytes_before": database_bytes_before,
                "database_bytes_after": database_bytes_before,
                "size_ratio": 1.0,
            }
        connection.execute("BEGIN IMMEDIATE")
        backfilled, indexed = _rebuild_fact_search_projection(connection)
        database_bytes_after = _database_bytes(connection)
        return {
            "status": "rebuilt",
            "facts": facts,
            "backfilled": backfilled,
            "fts_rows": indexed,
            "user_version": FACT_SEARCH_TERMS_USER_VERSION,
            "database_bytes_before": database_bytes_before,
            "database_bytes_after": database_bytes_after,
            "size_ratio": round(
                database_bytes_after / max(1, database_bytes_before),
                3,
            ),
        }


def _fact_search_projection_state(
    connection: sqlite3.Connection,
) -> tuple[int, bool, int, int]:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    fact_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(wiki_facts)").fetchall()
    }
    fts_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(wiki_fact_fts)").fetchall()
    }
    facts = int(connection.execute("SELECT COUNT(*) FROM wiki_facts").fetchone()[0])
    fts_rows = (
        int(connection.execute("SELECT COUNT(*) FROM wiki_fact_fts_docsize").fetchone()[0])
        if "search_terms" in fts_columns
        else facts
    )
    required = (
        version < FACT_SEARCH_TERMS_USER_VERSION
        or "search_terms" not in fact_columns
        or fts_columns != {"search_terms"}
        or fts_rows != facts
    )
    return version, required, facts, fts_rows


def _rebuild_fact_search_projection(connection: sqlite3.Connection) -> tuple[int, int]:
    for trigger in ("wiki_facts_ai", "wiki_facts_ad", "wiki_facts_au"):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(wiki_facts)").fetchall()
    }
    if "search_terms" not in columns:
        connection.execute(
            "ALTER TABLE wiki_facts ADD COLUMN search_terms TEXT NOT NULL DEFAULT ''"
        )
    rows = connection.execute(
        """
        SELECT id, routing_text, quote, section_path, symbol, search_terms
        FROM wiki_facts
        """
    ).fetchall()
    updates = [
        (
            derived,
            int(row["id"]),
        )
        for row in rows
        if (
            derived := _fact_search_terms_from_values(
                row["routing_text"],
                row["quote"],
                row["section_path"],
                row["symbol"],
            )
        )
        != str(row["search_terms"])
    ]
    connection.executemany(
        "UPDATE wiki_facts SET search_terms = ? WHERE id = ?",
        updates,
    )
    connection.execute("DROP TABLE IF EXISTS wiki_fact_fts")
    connection.execute(_WIKI_FACT_FTS_SCHEMA_STATEMENT)
    for statement in _SCHEMA_STATEMENTS:
        if statement.startswith("CREATE TRIGGER IF NOT EXISTS wiki_facts_"):
            connection.execute(statement)
    connection.execute("INSERT INTO wiki_fact_fts(wiki_fact_fts) VALUES ('rebuild')")
    connection.execute(f"PRAGMA user_version = {FACT_SEARCH_TERMS_USER_VERSION}")
    indexed = int(
        connection.execute("SELECT COUNT(*) FROM wiki_fact_fts_docsize").fetchone()[0]
    )
    return len(updates), indexed


def _database_bytes(connection: sqlite3.Connection) -> int:
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    return page_count * page_size


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
    root = database_path.parents[1]
    created_blob_hashes: list[str] = []
    try:
        with _connect(database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            }
            if "source_documents" in tables:
                _migrate_origin_main_schema(connection, root, created_blob_hashes)
            else:
                _migrate_unified_schema(connection, root)
    except Exception:
        for content_sha256 in created_blob_hashes:
            _unlink_blob(root, content_sha256)
        raise


def _migrate_unified_schema(connection: sqlite3.Connection, root: Path) -> None:
    repository_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(git_repositories)").fetchall()
    }
    if repository_columns and "sensitivity" not in repository_columns:
        connection.execute(
            "ALTER TABLE git_repositories ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'local_only'"
        )
    source_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(sources)").fetchall()
    }
    if source_columns and "legacy_source_id" not in source_columns:
        connection.execute("ALTER TABLE sources ADD COLUMN legacy_source_id TEXT")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_legacy_source_id
            ON sources(legacy_source_id)
            WHERE legacy_source_id IS NOT NULL
            """
        )
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(source_versions)").fetchall()
    }
    if not columns:
        _apply_schema(connection)
        connection.execute(f"PRAGMA user_version = {FACT_SEARCH_TERMS_USER_VERSION}")
        _backfill_wiki_facts(connection, root)
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
    if _fact_search_projection_state(connection)[1]:
        _rebuild_fact_search_projection(connection)
    _backfill_wiki_facts(connection, root)


def _migrate_origin_main_schema(
    connection: sqlite3.Connection,
    root: Path,
    created_blob_hashes: list[str],
) -> None:
    _apply_schema_without_source_fts(connection)
    rows = connection.execute(
        """
        SELECT
            d.source_id,
            d.uri,
            d.content_sha256,
            d.media_type,
            d.category,
            d.imported_at,
            d.observed_at,
            d.sensitivity,
            d.tags_json,
            f.title
        FROM source_documents AS d
        LEFT JOIN source_fts AS f ON f.source_id = d.source_id
        ORDER BY d.source_id
        """
    )
    for row in rows:
        legacy_source_id = str(row["source_id"])
        content_sha256 = str(row["content_sha256"])
        if _ORIGIN_MAIN_SOURCE_ID.fullmatch(legacy_source_id) is None:
            raise WorkspaceIntegrityError("legacy source identity is invalid")
        if _CONTENT_SHA256.fullmatch(content_sha256) is None:
            raise WorkspaceIntegrityError("legacy source content hash is invalid")
        content = _read_origin_main_raw(root, str(row["uri"]), content_sha256)
        relative_blob, created = _write_blob(root, content_sha256, content)
        if created:
            created_blob_hashes.append(content_sha256)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceIntegrityError("legacy Raw source is not valid UTF-8") from exc
        imported_at = _safe_legacy_timestamp(row["imported_at"], fallback=_now())
        observed_at = _safe_legacy_timestamp(row["observed_at"], fallback=imported_at)
        category_value = str(row["category"])
        category = (
            category_value if category_value in RAW_CATEGORIES else SourceCategory.NOTES.value
        )
        sensitivity_value = str(row["sensitivity"])
        sensitivity = (
            sensitivity_value
            if sensitivity_value in {item.value for item in Sensitivity}
            else Sensitivity.LOCAL_ONLY.value
        )
        tags_json = _safe_legacy_tags(row["tags_json"])
        canonical_source_id = hashlib.sha256(legacy_source_id.encode("utf-8")).hexdigest()
        title = str(row["title"]) if row["title"] else Path(str(row["uri"])).name
        source_cursor = connection.execute(
            """
            INSERT INTO sources(
                source_id, source_uri, source_path, legacy_source_id, source_kind, created_at
            ) VALUES (?, ?, ?, ?, 'local', ?)
            """,
            (
                canonical_source_id,
                f"mf://source/{canonical_source_id}",
                str(row["uri"]),
                legacy_source_id,
                imported_at,
            ),
        )
        blob_cursor = connection.execute(
            """
            INSERT INTO blobs(content_sha256, snapshot_path, size_bytes, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                content_sha256,
                relative_blob.as_posix(),
                len(content),
                imported_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO source_versions(
                source_id, blob_id, supersedes_version_id, media_type, category,
                title, observed_at, sensitivity, tags_json, legacy_category, is_current
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                _lastrowid(source_cursor),
                _lastrowid(blob_cursor),
                (
                    str(row["media_type"])
                    if str(row["media_type"]) in {"text/markdown", "text/plain"}
                    else "text/plain"
                ),
                category,
                title or Path(str(row["uri"])).name or "Untitled",
                observed_at,
                sensitivity,
                tags_json,
                category_value if category != category_value else None,
            ),
        )

    connection.execute("DROP TABLE source_fts")
    connection.execute(_SOURCE_FTS_SCHEMA_STATEMENT)
    _rebuild_origin_main_fts(connection, root)
    connection.execute("DROP TABLE source_documents")
    _rebuild_fact_search_projection(connection)
    _backfill_wiki_facts(connection, root)


def _apply_schema_without_source_fts(connection: sqlite3.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        if statement != _SOURCE_FTS_SCHEMA_STATEMENT:
            connection.execute(statement)
    for statement in EGRESS_SCHEMA:
        connection.execute(statement)
    for statement in CAPTURE_SCHEMA:
        connection.execute(statement)
    for statement in CONFLICT_SCHEMA:
        connection.execute(statement)


def _rebuild_origin_main_fts(connection: sqlite3.Connection, root: Path) -> None:
    rows = connection.execute(
        """
        SELECT v.id, v.title, b.content_sha256, b.snapshot_path
        FROM source_versions AS v
        JOIN blobs AS b ON b.id = v.blob_id
        ORDER BY v.id
        """
    )
    for row in rows:
        content = _read_blob_bytes(
            root,
            str(row["content_sha256"]),
            Path(str(row["snapshot_path"])),
        ).decode("utf-8")
        title = str(row["title"])
        connection.execute(
            """
            INSERT INTO source_fts(rowid, title, content, search_terms)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(row["id"]),
                title,
                content,
                _search_terms(f"{title}\n{content}"),
            ),
        )


def _read_origin_main_raw(root: Path, uri: str, content_sha256: str) -> bytes:
    relative = PurePosixPath(uri)
    if (
        relative.is_absolute()
        or len(relative.parts) < 2
        or relative.parts[0] != "raw"
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in uri
    ):
        raise WorkspaceIntegrityError("legacy Raw source path is invalid")
    path = root.joinpath(*relative.parts)
    _reject_symlink_components(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | _no_follow_flag())
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise WorkspaceIntegrityError("legacy Raw source must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                content = source.read()
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise WorkspaceIntegrityError("legacy Raw source could not be read safely") from exc
    if hashlib.sha256(content).hexdigest() != content_sha256:
        raise WorkspaceIntegrityError("legacy Raw source digest does not match its index")
    return content


def _safe_legacy_timestamp(value: object, *, fallback: str) -> str:
    if value is None:
        return fallback
    rendered = str(value)
    try:
        datetime.fromisoformat(rendered)
    except ValueError:
        return fallback
    return rendered


def _safe_legacy_tags(value: object) -> str:
    try:
        tags = json.loads(str(value))
    except json.JSONDecodeError:
        return "[]"
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return "[]"
    return json.dumps(tags)


def _apply_schema(connection: sqlite3.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)
    for statement in EGRESS_SCHEMA:
        connection.execute(statement)
    for statement in CAPTURE_SCHEMA:
        connection.execute(statement)
    for statement in CONFLICT_SCHEMA:
        connection.execute(statement)


def _backfill_wiki_facts(connection: sqlite3.Connection, root: Path) -> None:
    fact_count = int(connection.execute("SELECT COUNT(*) FROM wiki_facts").fetchone()[0])
    fts_count = int(connection.execute("SELECT COUNT(*) FROM wiki_fact_fts_docsize").fetchone()[0])
    if fact_count:
        if fts_count != fact_count:
            connection.execute("INSERT INTO wiki_fact_fts(wiki_fact_fts) VALUES ('rebuild')")
        return
    if fts_count:
        connection.execute("INSERT INTO wiki_fact_fts(wiki_fact_fts) VALUES ('delete-all')")
    pages_root = root / "wiki/pages"
    if not pages_root.exists():
        return
    if pages_root.is_symlink() or not pages_root.is_dir():
        raise WorkspaceSecurityError("Wiki fact backfill requires a real pages directory")
    page_facts: dict[str, tuple[WikiFact, ...]] = {}
    for page in sorted(pages_root.rglob("*.md")):
        if page.is_symlink() or not page.is_file():
            raise WorkspaceSecurityError("Wiki fact backfill rejects unsafe page paths")
        relative = page.relative_to(root).as_posix()
        page_facts[relative] = parse_page_facts(relative, page.read_text(encoding="utf-8"))
    normalized = _normalize_page_facts(page_facts)
    for path in sorted(normalized):
        for fact in normalized[path]:
            row = connection.execute(
                """
                SELECT revisions.repository_id
                FROM sources
                JOIN source_versions
                  ON source_versions.source_id = sources.id
                JOIN applied_source_versions
                  ON applied_source_versions.source_id = sources.source_id
                 AND applied_source_versions.source_version_id = source_versions.id
                LEFT JOIN git_source_revisions AS revisions
                  ON revisions.source_version_id = source_versions.id
                WHERE sources.source_id = ? AND source_versions.id = ?
                """,
                (fact.source_id, fact.source_version),
            ).fetchone()
            if row is None:
                raise WorkspaceIntegrityError(
                    "Wiki fact backfill found a non-applied SourceVersion"
                )
            connection.execute(
                """
                INSERT INTO wiki_facts(
                    fact_id, page_path, repository_id, source_id, source_version,
                    locator, section_path, quote, routing_text, symbol, relation_type,
                    search_terms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.fact_id,
                    fact.page_path,
                    str(row["repository_id"]) if row["repository_id"] is not None else None,
                    fact.source_id,
                    fact.source_version,
                    fact.locator,
                    fact.section_path,
                    fact.quote,
                    fact.routing_text,
                    fact.symbol,
                    fact.relation_type,
                    _fact_search_terms(fact),
                ),
            )


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
                s.legacy_source_id,
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
            legacy_source_id=(
                str(row["legacy_source_id"]) if row["legacy_source_id"] is not None else None
            ),
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


def _search_terms(text: str) -> str:
    return index_terms_text(text)


def _fts_query(query: str, *, require_all_terms: bool = True) -> str:
    terms = _search_terms(query).split()
    if not terms:
        raise ValueError("search query must contain a word or number")
    escaped = [term.replace('"', '""') for term in terms]
    operator = " AND " if require_all_terms else " OR "
    return "search_terms : (" + operator.join(f'"{term}"' for term in escaped) + ")"


def _wiki_fact_fts_query(query: str, *, projected: bool = True) -> str:
    terms = _search_terms(query).split()
    if not terms:
        raise ValueError("fact search query must contain a word or number")
    escaped = [term.replace('"', '""') for term in terms]
    column = "search_terms:" if projected else ""
    return " OR ".join(f'{column}"{term}"' for term in escaped)


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
