# Wiki Fact Index Specification

## Status

`IMPLEMENTED_DATA_LAYER`

## Goal

Index every grounded fact in applied `wiki/pages/**/*.md` files without
changing page routing or Answer behavior.

## Data Contract

Each fact stores:

- deterministic relational row identity;
- `page_path`;
- `repository_id` when the Source is from Git;
- `source_id`;
- `source_version`;
- `locator`;
- `section_path`;
- exact `quote`;
- `routing_text`;
- optional `symbol`;
- optional `relation_type`.

The exact quote remains the Citation evidence. Section, routing, Symbol, and
Relation fields are search metadata and must never be presented as evidence by
themselves.

## Storage

- `wiki_facts`: authoritative relational table;
- `wiki_fact_fts`: SQLite FTS5 external-content projection;
- tokenizer: built-in `unicode61`;
- no new dependency;
- no embedding or model call.

## Apply Contract

During approved apply:

1. parse facts from staged candidate pages;
2. validate every fact Source and SourceVersion;
3. replace facts for created or updated pages;
4. remove facts for archived pages;
5. update the FTS projection;
6. restore previous facts if file writes or Git commit fail.

No facts are indexed from unapproved ChangeSets.

## Query API

The read-only API accepts:

- non-empty query;
- result limit 1-100;
- optional repository scope;
- optional applied page-path scope.

Results are ordered by:

1. FTS5 rank;
2. `page_path`;
3. fact row ID.

The API returns structured metadata and exact Citation identity. It is not
called by `answer_question` in this phase.

## Acceptance Gates

- every parsed fact in an applied page has exactly one relational row;
- every relational row has exactly one FTS row;
- update replaces old page facts;
- archive removes page facts;
- failed apply restores prior fact and FTS rows;
- repository scope never returns another repository;
- exact quote, SourceVersion, section, Symbol, and Relation metadata round-trip;
- existing QA and structural benchmarks do not change;
- Ruff, format, strict Mypy, full pytest, coverage, Wheel/sdist, and clean-room
  checks pass.

## Public Evidence

`demo/results/wiki_fact_index_baseline.json` records two clean deterministic
replays against:

- `shareAI-lab/learn-claude-code@7b564c3ee6996039cb4e13a53024dfe2d4388d35`;
- `colinhacks/zod@912f0f51b0ced654d0069741e7160834dca742ee`.

The result contains 1,356 Facts across 39 pages, including 1,270 Symbol facts
and 86 Relation facts. Both runs produced canonical row SHA256
`9f909105161623b6cbcae96b5c2e7140daa119fadb3ec24f20c2e095fe51d40d`.
The Evidence artifact SHA256 is
`57343945d9f4c9db48a7e2990b4e0cf49ac5ac8623e4f40c7f763a4abca6f8ec`.

This Evidence proves the data-layer and replay contracts only. It does not
claim improved page routing, fact selection, or Answer accuracy.

## Deferred

- using Fact FTS5 in production answers;
- Code Index exact-symbol routing;
- multi-source coverage selection;
- support score and abstention threshold;
- confirmation and holdout execution.
