# MemoryForge v0.2.3 Document-Frequency Normalization SPEC

## Goal

Test whether document-frequency weighting improves deterministic page recall
across unrelated English documentation.

## Frozen Data

- Development: Uvicorn, 10 answered cases.
- Confirmation: Typer, 12 answered cases.
- Both repositories passed the public-source secret scanner before suite
  execution.

## Candidate

Rank complete applied Wiki pages using query terms weighted by inverse document
frequency. Use only standard-library math and existing compiled pages.

No vector database, model call, dependency, cache, schema, or configuration.

## Gates

Development:

- source recall@3 gain at least 20 points;
- average pages at most 3.

Confirmation, run once after candidate freeze:

- source recall@3 at least 75%;
- average pages at most 3;
- byte-identical clean-workspace replay;
- AgentSkill-Eval source recall remains at least 96.2%.

Confirmation failure rejects and reverts the candidate. Typer failures must not
guide tuning.

## Result

Accepted. Uvicorn development page recall reached 100.0%. Frozen Typer
confirmation reached 83.3% through the production route and 91.7% through the
isolated DF scorer, above the 75% gate. See
[`V023_DOCUMENT_FREQUENCY_NORMALIZATION_REPORT.md`](V023_DOCUMENT_FREQUENCY_NORMALIZATION_REPORT.md).

## Artifacts

- `demo/evaluation/v023_df_routing_sources.json`
- `demo/evaluation/uvicorn_docs_dev_v023.json`
- `demo/evaluation/typer_docs_confirm_v023.json`
