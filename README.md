# MemoryForge

MemoryForge is a Git-backed, auditable LLM Wiki agent for personal developer knowledge.

It compiles immutable source material into a versioned Wiki, stages every AI-generated change for review, and answers questions with source citations, temporal validity, and conflict awareness.

## Status

The trusted-storage foundation is implemented. It creates a local workspace,
archives UTF-8 Markdown and text sources immutably, deduplicates by SHA-256,
writes Pydantic-validated manifests, and indexes Raw content with SQLite FTS5.

Each workspace initialization also creates a clean Git baseline commit. The
internal ChangeSet store persists a proposal as immutable metadata plus hashed
candidate files under `.memoryforge/staging/`; it does not write stable Wiki
content.

Wiki compilation, ChangeSet review, cited answers, lint, rollback, and
evaluation are registered CLI contracts for later milestones. They deliberately
return a clear "not enabled" error instead of pretending to work.

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

## Quickstart

MemoryForge requires Python 3.11 or later.

```bash
git clone https://github.com/ranmaoxia0123/MemoryForge.git
cd MemoryForge
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

.venv/bin/memoryforge init ./demo-wiki
printf '# Cache design\n\nUse namespaced cache keys.\n' > ./cache-design.md
.venv/bin/memoryforge import ./cache-design.md \
  --category design \
  --workspace ./demo-wiki
```

The import command prints a structured manifest record. A repeated import of
the same byte content returns `duplicate` and does not create a second Raw file
or FTS entry.

## Current CLI surface

Implemented in the first milestone:

```bash
memoryforge init <workspace>
memoryforge import <path> [--category design|postmortem|summary|notes|refs]
```

Registered for the following milestones:

```bash
memoryforge ingest
memoryforge review
memoryforge apply
memoryforge reject
memoryforge ask
memoryforge lint
memoryforge history
memoryforge rollback
memoryforge eval
```

## Repository layout

```text
src/memoryforge/   CLI, Git baseline, ChangeSet staging, importer, manifest, FTS
tests/             deterministic foundation and CLI tests
SPEC.md            product and engineering contract
```
