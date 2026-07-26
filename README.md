# MemoryForge

MemoryForge aims to become a Git-backed, auditable LLM Wiki agent for personal
developer knowledge.

It compiles immutable source material into a versioned Wiki, stages every AI-generated change for review, and answers questions with source citations, temporal validity, and conflict awareness.

## Status

The Phase 1 trusted-storage foundation is implemented:

- initialize a private workspace with immutable `raw/` storage, SQLite FTS5,
  schema/config files, and a clean Git baseline commit;
- import local Markdown and UTF-8 text files;
- track stable Sources and auditable SourceVersions while globally reusing
  content-addressed Blob snapshots;
- write one append-only, Pydantic-validated Manifest per SourceVersion;
- make repeated imports idempotent and retain old versions outside normal search;
- search English and Chinese titles/body text with immutable snapshot URIs and
  content hashes;
- stage immutable ChangeSets with hashed candidate Wiki files without touching
  the stable `wiki/` tree;
- reject unsupported, oversized, symlinked, and out-of-root files;
- reject common sensitive file names and high-confidence secret patterns.

LLM compilation, vector retrieval, Feishu integration, Wiki write-back, and
ChangeSet review/apply are intentionally not implemented in this phase.

## Quickstart

Requires Python 3.11+ on Linux or macOS. Windows is not supported in Phase 1a
because secure local file operations depend on POSIX directory file descriptors.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

memoryforge init ./demo-workspace
memoryforge import ./demo/fixtures/public_project_note.md \
  --category design \
  --workspace ./demo-workspace
memoryforge search 'cache design' --workspace ./demo-workspace
```

One workspace corresponds to one source root. Always run MemoryForge from that
source root; Source IDs are the full SHA-256 of the canonical relative source
path, so they remain stable when the repository moves. Importing the same
relative path from a different root into the same workspace intentionally refers
to the same Source.

The `origin/main` legacy schema is the one compatibility exception: its
`src_<16hex>` identity is migrated to `sha256(legacy_source_id)`, while the
original value is retained as `legacy_source_id` in the database and Manifest
for auditing.

Imports are restricted to files under the current working directory. Symlinks
are rejected, and only `.md`, `.markdown`, and `.txt` UTF-8 files up to 5 MiB
are accepted. Run the command from the root of the source repository you intend
to import. Search results cite immutable `mf://blob/<sha256>` evidence URIs and
expose only workspace-relative snapshot paths rather than mutable original files
or private absolute paths.

An optional `.memoryforgeignore` in the source root excludes files before they
are read. Phase 1 supports blank lines, `#` comments, root-anchored `/patterns`,
directory patterns ending in `/`, and shell-style `*`, `?`, and `[]` wildcards.
Patterns without `/` match any path component. Negation (`!`) and parent
traversal (`..`) are rejected instead of being interpreted ambiguously. The
ignore file itself must be a regular UTF-8 file and may not be a symbolic link.

`memoryforge init` creates a workspace `.gitignore` that protects Raw blobs,
SQLite, manifests, staging, traces, and vectors while leaving the stable Wiki
and workspace contract available for version control.
Secret detection is deliberately conservative and is not a replacement for a
dedicated secret-scanning tool.

A Blob is written to a same-directory, private temporary file, fsynced, and then
atomically published. A hard process termination can leave a recognizable
unreferenced temporary file; the next import of the same hash removes it.
Unreferenced complete Blobs can still remain if the process terminates after
publication but before the database transaction commits; re-import verifies and
reuses them.

Phase 1a uses a local, trusted single-user threat model. It protects against
accidental exposure, common symlink escapes, and evidence tampering. It does not
claim to resist a malicious process running concurrently as the same account and
replacing the SQLite directory. Concurrent Git writes by an external process
that does not honor MemoryForge's staging lock are also outside this single-user
threat model.

ChangeSet candidates, metadata, and a metadata digest are published durably
before the staging directory becomes visible. New records may only be
`PROPOSED`; review/apply will use separate immutable transition events in a later
phase. Every proposal load, reuse, and review verifies that its `base_commit`
still matches the current Git `HEAD`; a future apply implementation must repeat
that check immediately before writing or committing the stable Wiki. The digest
detects accidental or one-sided metadata edits, but Phase 1 does not claim to
resist an attacker who can rewrite both a record and its digest.

## Core workflow

```text
Raw sources
  -> Wiki compilation
  -> staged ChangeSet
  -> validation and human review
  -> versioned Wiki
  -> cited query and knowledge lint
```

## Design principles

- Raw source material is immutable.
- Stable Wiki content is never modified without an approved ChangeSet.
- Verified claims must be traceable to source evidence.
- Knowledge changes are versioned and reversible through Git.
- The MVP focuses only on personal developer knowledge.

## Project specification

See [SPEC.md](./SPEC.md) for the complete product and engineering specification, including:

- goals and non-goals;
- user journeys and functional requirements;
- architecture and data models;
- CLI and tool interfaces;
- security and privacy boundaries;
- evaluation design;
- a four-week MVP roadmap.

## Current CLI

```bash
memoryforge init <workspace>
memoryforge import <path> --workspace <workspace> [--category <category>]
memoryforge search <query> --workspace <workspace>
```

The lifecycle commands `ingest`, `review`, `apply`, `reject`, `ask`, `lint`,
`history`, `rollback`, and `eval` are registered as stable CLI contracts and
return an explicit “not enabled” error until their milestone is implemented.

The public demo fixture contains fictional data only.
