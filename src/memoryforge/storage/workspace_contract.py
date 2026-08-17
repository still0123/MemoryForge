"""Static filesystem and SQLite contract for a MemoryForge Workspace."""

from __future__ import annotations

from pathlib import Path

from memoryforge.compiler.egress_policy import SCHEMA_SQL as _EGRESS_SCHEMA
from memoryforge.compiler.knowledge_conflicts import SCHEMA_SQL as _CONFLICT_SCHEMA
from memoryforge.storage.capture_inbox import SCHEMA_SQL as _CAPTURE_SCHEMA

CAPTURE_SCHEMA = _CAPTURE_SCHEMA
CONFLICT_SCHEMA = _CONFLICT_SCHEMA
EGRESS_SCHEMA = _EGRESS_SCHEMA

DATABASE_RELATIVE_PATH = Path(".memoryforge/index.sqlite")
RAW_CATEGORIES = ("design", "postmortem", "summary", "notes", "refs")
WIKI_DIRECTORIES = ("pages",)
_GITIGNORE_RULES = (
    "/raw/",
    "/.memoryforge/index.sqlite*",
    "/.memoryforge/manifests/",
    "/.memoryforge/staging/",
    "/.memoryforge/workspace.lock",
    "/.memoryforge/rejected/",
    "/.memoryforge/traces/",
    "/.memoryforge/vectors/",
    "/.memoryforge/sessions/",
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
  - entity
  - concept
  - synthesis
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
_PROMPT_CONTEXT_LIMIT = 8000

_SOURCE_FTS_SCHEMA_STATEMENT = """
CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
    title,
    content,
    search_terms,
    tokenize='unicode61'
)"""
_WIKI_FACT_FTS_SCHEMA_STATEMENT = """
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fact_fts USING fts5(
    search_terms,
    content='wiki_facts',
    content_rowid='id',
    tokenize='unicode61'
)"""

_SCHEMA_STATEMENTS = (
    """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    source_uri TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    legacy_source_id TEXT UNIQUE,
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
CREATE TABLE IF NOT EXISTS applied_source_versions (
    source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id)
)""",
    """
CREATE TABLE IF NOT EXISTS page_sources (
    page_path TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    PRIMARY KEY(page_path, source_id)
)""",
    """
CREATE INDEX IF NOT EXISTS idx_page_sources_source_id
ON page_sources(source_id, page_path)""",
    """
CREATE TABLE IF NOT EXISTS wiki_facts (
    id INTEGER PRIMARY KEY,
    fact_id TEXT NOT NULL UNIQUE,
    page_path TEXT NOT NULL,
    repository_id TEXT REFERENCES git_repositories(repository_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_version INTEGER NOT NULL REFERENCES source_versions(id),
    locator TEXT NOT NULL,
    section_path TEXT NOT NULL,
    quote TEXT NOT NULL,
    routing_text TEXT NOT NULL,
    symbol TEXT,
    relation_type TEXT,
    search_terms TEXT NOT NULL DEFAULT '',
    UNIQUE(page_path, fact_id)
)""",
    """
CREATE INDEX IF NOT EXISTS idx_wiki_facts_page
ON wiki_facts(page_path, id)""",
    """
CREATE INDEX IF NOT EXISTS idx_wiki_facts_source
ON wiki_facts(source_id, source_version)""",
    """
CREATE INDEX IF NOT EXISTS idx_wiki_facts_repository
ON wiki_facts(repository_id, page_path)""",
    """
CREATE INDEX IF NOT EXISTS idx_wiki_facts_symbol
ON wiki_facts(symbol, repository_id, page_path)
WHERE symbol IS NOT NULL""",
    """
CREATE TABLE IF NOT EXISTS git_repositories (
    repository_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    checkout_path TEXT NOT NULL UNIQUE,
    remote_name TEXT,
    remote_url TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'local_only',
    registered_at TEXT NOT NULL,
    last_synced_commit TEXT
)""",
    """
CREATE TABLE IF NOT EXISTS feishu_documents (
    document_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    registered_at TEXT NOT NULL
)""",
    """
CREATE TABLE IF NOT EXISTS git_source_revisions (
    source_version_id INTEGER PRIMARY KEY REFERENCES source_versions(id),
    repository_id TEXT NOT NULL REFERENCES git_repositories(repository_id),
    relative_path TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    UNIQUE(repository_id, relative_path, commit_sha)
)""",
    """
CREATE TABLE IF NOT EXISTS git_code_modules (
    repository_id TEXT NOT NULL REFERENCES git_repositories(repository_id),
    relative_path TEXT NOT NULL,
    PRIMARY KEY(repository_id, relative_path)
)""",
    """
CREATE TABLE IF NOT EXISTS folder_imports (
    folder_id TEXT PRIMARY KEY,
    registered_at TEXT NOT NULL
)""",
    """
CREATE TABLE IF NOT EXISTS folder_source_versions (
    source_version_id INTEGER PRIMARY KEY REFERENCES source_versions(id),
    folder_id TEXT NOT NULL REFERENCES folder_imports(folder_id),
    relative_path TEXT NOT NULL,
    UNIQUE(folder_id, relative_path, source_version_id)
)""",
    """
CREATE INDEX IF NOT EXISTS idx_folder_source_versions_path
ON folder_source_versions(folder_id, relative_path)""",
    """
CREATE TABLE IF NOT EXISTS source_policies (
    source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
    trust TEXT NOT NULL,
    profile TEXT,
    allow_single_source_archive INTEGER NOT NULL DEFAULT 0
        CHECK (allow_single_source_archive IN (0, 1)),
    updated_at TEXT NOT NULL
)""",
    """
CREATE TABLE IF NOT EXISTS automation_decisions (
    changeset_id TEXT PRIMARY KEY,
    proposal_sha256 TEXT NOT NULL,
    validation_sha256 TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    decision TEXT NOT NULL,
    risk TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL
)""",
    """
CREATE TABLE IF NOT EXISTS automation_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    changeset_id TEXT,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL
)""",
    """
CREATE INDEX IF NOT EXISTS idx_automation_events_changeset
ON automation_events(changeset_id, id)""",
    """
CREATE TABLE IF NOT EXISTS page_protection (
    page_path TEXT PRIMARY KEY,
    protected INTEGER NOT NULL CHECK (protected IN (0, 1)),
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
)""",
    _SOURCE_FTS_SCHEMA_STATEMENT,
    _WIKI_FACT_FTS_SCHEMA_STATEMENT,
    """
CREATE TRIGGER IF NOT EXISTS wiki_facts_ai AFTER INSERT ON wiki_facts BEGIN
  INSERT INTO wiki_fact_fts(rowid, search_terms) VALUES (new.id, new.search_terms);
END""",
    """
CREATE TRIGGER IF NOT EXISTS wiki_facts_ad AFTER DELETE ON wiki_facts BEGIN
  INSERT INTO wiki_fact_fts(wiki_fact_fts, rowid, search_terms)
  VALUES ('delete', old.id, old.search_terms);
END""",
    """
CREATE TRIGGER IF NOT EXISTS wiki_facts_au AFTER UPDATE ON wiki_facts BEGIN
  INSERT INTO wiki_fact_fts(wiki_fact_fts, rowid, search_terms)
  VALUES ('delete', old.id, old.search_terms);
  INSERT INTO wiki_fact_fts(rowid, search_terms) VALUES (new.id, new.search_terms);
END""",
)
