# MemoryForge

MemoryForge is a Git-backed, auditable LLM Wiki agent for personal developer knowledge.

It compiles immutable source material into a versioned Wiki, stages every AI-generated change for review, and answers questions with source citations, temporal validity, and conflict awareness.

## Status

The project is currently in the specification stage.

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

## Planned MVP

The initial release will provide:

```bash
memoryforge init
memoryforge import
memoryforge ingest
memoryforge review
memoryforge ask
memoryforge lint
memoryforge rollback
```

No implementation has been published yet.
