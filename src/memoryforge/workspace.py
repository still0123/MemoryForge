"""Workspace creation and paths for MemoryForge's three-layer storage model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from memoryforge.errors import WorkspaceError
from memoryforge.index import SourceIndex
from memoryforge.version_store import GitVersionStore

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

DEFAULT_CONFIG = {
    "workspace_version": 1,
    "schema_version": 1,
    "provider": {
        "enabled": False,
        "allowed_environment_variables": [],
    },
    "index": {
        "fts": True,
        "embeddings": False,
    },
}

DEFAULT_SCHEMA = {
    "source_categories": list(RAW_CATEGORIES),
    "page_types": list(WIKI_DIRECTORIES),
    "claim_rules": {
        "verified_claim_requires_citation": True,
        "allow_raw_mutation": False,
        "unresolved_high_conflict_blocks_apply": True,
    },
}

DEFAULT_AGENTS_MD = """# MemoryForge Workspace

## Knowledge boundary

This workspace contains personal developer knowledge only. Do not add company,
customer, credential, or production-secret material.

## Raw sources

`raw/` stores immutable imported material. Treat it as read-only after import.
Use `design/`, `postmortem/`, `summary/`, `notes/`, and `refs/` categories.

## Wiki discipline

Stable Wiki content is changed only through an approved ChangeSet. Verified
claims require citations with a source hash and locator. Mark uncertain content
as `UNVERIFIED` or `TODO`; never turn it into a fact without evidence.

## Privacy

Respect `.memoryforgeignore`. Sources marked `local_only` must not be sent to
remote providers.
"""

DEFAULT_IGNORE = """# Files and directories that must never enter MemoryForge Raw.
.env
.env.*
*.pem
*.key
id_rsa
.ssh/
.aws/
.git/
"""

DEFAULT_INTERNAL_GITIGNORE = """# Rebuildable local artifacts.
index.sqlite
index.sqlite-*
vectors/
traces/
staging/
rejected/
"""

BASELINE_PATHS = (
    "AGENTS.md",
    ".memoryforgeignore",
    "wiki/INDEX.md",
    ".memoryforge/config.yaml",
    ".memoryforge/schema.yaml",
    ".memoryforge/.gitignore",
)


@dataclass(frozen=True)
class Workspace:
    """Filesystem coordinates for a valid MemoryForge workspace."""

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
    def internal_ignore_path(self) -> Path:
        return self.internal_dir / ".gitignore"

    @property
    def index_path(self) -> Path:
        return self.internal_dir / "index.sqlite"

    @property
    def source_index(self) -> SourceIndex:
        return SourceIndex(self.index_path)

    @property
    def version_store(self) -> GitVersionStore:
        return GitVersionStore(self.root)

    def current_commit(self) -> str:
        """Return the Git revision against which a ChangeSet must be staged."""

        commit = self.version_store.head()
        if commit is None:
            raise WorkspaceError(f"Workspace is missing its Git baseline commit: {self.root}")
        return commit

    @classmethod
    def initialize(cls, root: Path) -> Workspace:
        """Create a workspace without overwriting an existing configuration."""

        workspace = cls(root.expanduser().resolve())
        if workspace.config_path.exists():
            raise WorkspaceError(f"Workspace already initialized: {workspace.root}")
        _validate_initialization_targets(workspace)

        workspace.root.mkdir(parents=True, exist_ok=True)
        for category in RAW_CATEGORIES:
            (workspace.raw_dir / category).mkdir(parents=True, exist_ok=True)
        for page_type in WIKI_DIRECTORIES:
            (workspace.wiki_dir / page_type).mkdir(parents=True, exist_ok=True)
        for directory in (
            workspace.manifest_dir,
            workspace.staging_dir,
            workspace.rejected_dir,
            workspace.internal_dir / "traces",
            workspace.internal_dir / "vectors",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        _write_new(workspace.root / "AGENTS.md", DEFAULT_AGENTS_MD)
        _write_new(workspace.root / ".memoryforgeignore", DEFAULT_IGNORE)
        _write_new(workspace.wiki_dir / "INDEX.md", "# Knowledge Index\n")
        _write_new(workspace.internal_ignore_path, DEFAULT_INTERNAL_GITIGNORE)
        _write_new(
            workspace.config_path,
            yaml.safe_dump(DEFAULT_CONFIG, allow_unicode=False, sort_keys=False),
        )
        _write_new(
            workspace.schema_path,
            yaml.safe_dump(DEFAULT_SCHEMA, allow_unicode=False, sort_keys=False),
        )

        workspace.source_index.initialize()
        workspace.version_store.initialize()
        workspace.version_store.ensure_baseline(BASELINE_PATHS)
        return workspace

    @classmethod
    def open(cls, root: Path) -> Workspace:
        """Return a workspace only after verifying the required metadata exists."""

        workspace = cls(root.expanduser().resolve())
        if not workspace.config_path.is_file():
            raise WorkspaceError(
                f"Not a MemoryForge workspace: {workspace.root}. "
                "Run `memoryforge init <workspace>` first."
            )
        if not (workspace.root / ".git").is_dir():
            raise WorkspaceError(f"Workspace is missing its Git repository: {workspace.root}")
        workspace.current_commit()
        return workspace


def _write_new(path: Path, content: str) -> None:
    """Write template files only during first-time initialization."""

    if path.exists():
        raise WorkspaceError(f"Refusing to overwrite existing file: {path}")
    path.write_text(content, encoding="utf-8")


def _validate_initialization_targets(workspace: Workspace) -> None:
    """Fail before writing when a reserved workspace path already exists."""

    reserved_paths = (
        workspace.raw_dir,
        workspace.wiki_dir,
        workspace.internal_dir,
        workspace.root / ".git",
        workspace.root / "AGENTS.md",
        workspace.root / ".memoryforgeignore",
    )
    conflicts = [path for path in reserved_paths if path.exists()]
    if conflicts:
        listed = ", ".join(str(path) for path in conflicts)
        raise WorkspaceError(f"Workspace paths already exist; refusing to merge: {listed}")
