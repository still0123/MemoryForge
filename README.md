# MemoryForge

MemoryForge is a Git-backed, auditable LLM Wiki agent for personal developer knowledge.

It compiles immutable source material into a versioned Wiki, stages every AI-generated change for review, and answers questions with source citations, temporal validity, and conflict awareness.

## Status

The trusted-storage and reviewed-publication workflow are implemented. The
system creates a local workspace, archives UTF-8 Markdown and text sources
immutably, deduplicates by SHA-256, writes Pydantic-validated manifests, and
indexes Raw content with SQLite FTS5.

Each workspace initialization also creates a clean Git baseline commit. The
internal ChangeSet store persists a proposal as immutable metadata plus hashed
candidate files under `.memoryforge/staging/`. A separate lifecycle record
advances a proposal through `VALIDATED`, human review, `APPROVED`, and
`APPLIED` without rewriting the immutable proposal.

The current deterministic compiler creates evidence-backed Source pages and an
updated Wiki index. It does not pretend to perform LLM Claim or Concept
extraction. Cited answers, lint, rollback, and evaluation remain registered CLI
contracts for later milestones and return a clear "not enabled" error.

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

CHANGESET_ID=$(.venv/bin/memoryforge ingest --workspace ./demo-wiki \
  | python -c 'import json,sys; print(json.load(sys.stdin)["changeset_id"])')
.venv/bin/memoryforge review "$CHANGESET_ID" --workspace ./demo-wiki
.venv/bin/memoryforge approve "$CHANGESET_ID" --workspace ./demo-wiki
.venv/bin/memoryforge apply "$CHANGESET_ID" --workspace ./demo-wiki
```

The import command prints a structured manifest record. A repeated import of
the same byte content returns `duplicate` and does not create a second Raw file
or FTS entry. `review` must run before approval, and `apply` refuses stale
ChangeSets or uncommitted edits to the target Wiki paths.

## Current CLI surface

Implemented:

```bash
memoryforge init <workspace>
memoryforge import <path> [--category design|postmortem|summary|notes|refs]
memoryforge ingest [--source <source-id>]
memoryforge review <changeset-id>
memoryforge approve <changeset-id>
memoryforge apply <changeset-id>
```

Registered for the following milestones:

```bash
memoryforge reject
memoryforge ask
memoryforge lint
memoryforge history
memoryforge rollback
memoryforge eval
```

## Repository layout

```text
src/memoryforge/   CLI, compiler, lifecycle, Git, ChangeSet, importer, manifest, FTS
tests/             deterministic foundation and CLI tests
SPEC.md            product and engineering contract
```
