# MemoryForge v0.2.2 Deterministic Page Routing SPEC

## Goal

Improve page-level expected-source recall for unrelated English technical
documentation without adding a vector database, model call, service, or second
query framework.

## Scope

- Development: frozen Click development split already consumed in v0.2.1.
- Confirmation: frozen MkDocs split, 12 answered cases.
- Primary metric: page-level expected-source recall@3.
- Secondary checks: average page count, deterministic replay, old 30-question
  regression.

Fact selection, multi-fact composition, and unanswerable confidence are not
part of this iteration. Answer accuracy may remain low even when routing
improves.

## Allowed Change

One deterministic page-rank fusion inside the existing `_candidate_pages`
boundary. It may reuse:

- current INDEX candidates;
- current SQLite FTS candidates;
- standard-library lexical features over already compiled Wiki pages.

No new dependency, index, cache, configuration surface, or storage schema.

## Development Gate

Integrate only if Click development page recall@3 improves by at least 20
percentage points and average pages remain at or below 3.

## Confirmation Gate

After implementation freeze, run MkDocs confirmation once.

- expected-source recall@3 at least 75%;
- average pages at or below 3;
- two clean-workspace runs byte-identical;
- old AgentSkill-Eval page recall does not regress;
- no tuning from MkDocs failures.

If confirmation fails, keep the experiment and negative evidence but do not
publish the routing change.

## Frozen Evidence

- Source manifest:
  `demo/evaluation/v022_document_routing_source.json`
- Confirmation suite:
  `demo/evaluation/mkdocs_docs_confirm_v022.json`

HTTPX 0.28.1 was rejected before query execution because its authentication
documentation triggered the existing public-source secret scanner. That source
did not consume confirmation and the security boundary was not relaxed.
