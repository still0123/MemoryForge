# Benchmark Taxonomy Research

## Scope

This research covers deterministic page-routing metrics, fact-selection
metrics, and failure classification. It does not select a retrieval backend or
change production query ranking.

## BEIR

- Repository: `beir-cellar/beir`
- Commit: `ef83d29307061c65d04b035b4f4e7c18bd8374af`
- License: Apache-2.0
- File reviewed: `beir/retrieval/evaluation.py`

Relevant design:

- retrieval is evaluated against explicit query relevance labels;
- retrieval metrics are calculated independently from downstream answer logic;
- metrics are reported at fixed cutoffs and aggregated across queries.

Adopt:

- page routing is scored before fact selection;
- the three-page cutoff stays explicit;
- macro results are derived from per-case booleans.

Reject:

- `pytrec_eval`;
- dense retrieval, embedding generation, and large cutoff ranges;
- NDCG/MAP as release gates for the current binary source labels.

## Haystack

- Repository: `deepset-ai/haystack`
- Commit: `1346dcfa7a48e4df2498dcb04ce29364be409110`
- License: Apache-2.0
- Files reviewed:
  - `haystack/components/evaluators/document_recall.py`
  - `test/components/evaluators/test_document_recall.py`

Relevant design:

- single-hit and multi-hit recall have different contracts;
- every query retains an individual score;
- empty evidence has an explicit zero result;
- the comparison identity is selected explicitly rather than inferred.

Adopt:

- single-source cases require any expected source;
- multi-source cases require every expected source;
- per-case routing and fact-selection results are retained;
- missing routed pages or selected facts are explicit failures.

Reject:

- framework components and serialization;
- content-based document identity;
- extra runtime dependencies.

## MemoryForge Decision

Evaluation now reports:

1. `page_route_recall_at_3`: expected Source identity exists in the three routed
   Wiki pages;
2. `fact_source_recall`: selected Citation Source identity is expected;
3. `fact_selection_accuracy`: selected facts contain required terms, exclude
   forbidden terms, and cite the expected Sources;
4. `answer_accuracy`: status, facts, and expected Sources all pass.

Failure classification uses one deterministic primary label:

- `page_route_miss`;
- `fact_selection_miss`;
- `insufficient_support`;
- `citation_stale`;
- `multi_source_incomplete`;
- `repository_isolation_failure`;
- `wrong_answer`;
- `wrong_abstention`;
- `none`.

Expected improvement:

- page routing and fact selection are never represented by one metric;
- every failed case has one primary classification;
- historical result files remain unchanged;
- no model call, embedding backend, or dependency is added.
