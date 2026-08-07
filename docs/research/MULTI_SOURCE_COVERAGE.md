# Multi-Source Coverage Selection Research

## Scope

This note records fixed-Commit references for the v0.3.0 multi-source
selection phase. The target is deterministic selection from already-ranked
Wiki Facts. It does not add semantic models, embeddings, or external services.

## LangChain

- Repository: `langchain-ai/langchain`
- Commit: `f48fa9478de0dfc51604d5fbe9e6ed4f0143eaac`
- License: MIT
- Relevant code:
  `libs/core/langchain_core/vectorstores/utils.py`
- Relevant integration:
  `libs/core/langchain_core/vectorstores/in_memory.py`
- Relevant test:
  `libs/core/tests/unit_tests/vectorstores/test_utils.py`

`maximal_marginal_relevance` first selects the candidate most relevant to the
query. Later iterations score each unselected candidate using query relevance
minus redundancy against the selected set. The in-memory vector store also
separates the candidate-fetch budget from the final result budget.

MemoryForge adopts:

- preserve the best-ranked first Fact;
- iterate over a bounded candidate pool;
- score marginal contribution against already-selected Facts;
- keep deterministic tie-breaking.

MemoryForge does not adopt:

- NumPy or vector embeddings;
- a tunable lambda;
- a larger unbounded retrieval stage;
- semantic similarity as release-gate evidence.

## Haystack Core Integrations

- Repository: `deepset-ai/haystack-core-integrations`
- Commit: `29a5daae1cc5ed42f7ef9b015cc1d984114bcacd`
- License: Apache-2.0
- Relevant code:
  `integrations/sentence_transformers/src/haystack_integrations/components/`
  `rankers/sentence_transformers/sentence_transformers_diversity.py`
- Relevant tests:
  `integrations/sentence_transformers/tests/`
  `test_sentence_transformers_diversity.py`

`SentenceTransformersDiversityRanker` deduplicates documents, starts from the
most query-relevant document, and then selects candidates that add diversity.
Its tests cover deduplication, bounded `top_k`, deterministic ordering, greedy
diversity, and MMR relevance/diversity extremes.

MemoryForge adopts:

- deduplicate before consuming the result budget;
- explicitly validate the requested result count;
- test diversity with adversarial duplicate candidates;
- retain the top-ranked first result.

MemoryForge does not adopt:

- Sentence Transformers, PyTorch, model downloads, or GPU paths;
- model-based document similarity;
- clustering or configurable ranking strategies;
- serialization machinery for a standalone ranker component.

## Local Design

The existing selector already greedily maximizes uncovered lexical question
terms. The missing constraint is source coverage. When a query requires more
than one source, a higher-ranked second Fact from an already-selected
`(source_id, source_version)` can displace an equally relevant Fact from a new
source. Support scoring then rejects the answer after selection.

The proposed selector keeps the current first result and current bounded
complexity. During later iterations:

1. if the distinct-source quota is not met, candidates from a new
   `(source_id, source_version)` rank before same-source candidates;
2. within that class, candidates covering more uncovered question terms win;
3. existing rank fields remain deterministic tie-breakers;
4. after the source quota is met, uncovered terms again lead selection.

This is a greedy constraint, not a new abstraction. It reuses the existing
selector and Citation identity.

## Expected Improvement

- multi-source selection accuracy: below 100% to 100% on frozen development;
- distinct-source coverage: below 100% to 100%;
- lexical term coverage: no regression;
- single-source rank preservation: 100%;
- default Wiki page budget: unchanged at three;
- dependencies and paid services: unchanged at zero.
