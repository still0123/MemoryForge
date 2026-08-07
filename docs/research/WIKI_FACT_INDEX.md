# Wiki Fact Index Research

## Scope

This research covers a local SQLite FTS5 index for already grounded Wiki
facts. It does not justify embeddings, a vector database, a graph database, an
LLM call, or replacing the three-page route.

## sqlite-utils

- Repository: `simonw/sqlite-utils`
- Commit: `6a456830ca33eb5edaa634a9b0febe5d71bea2be`
- License: Apache-2.0
- Files reviewed:
  - `sqlite_utils/db.py`
  - `tests/test_fts.py`

Relevant design:

- a relational content table remains authoritative;
- an external-content FTS table is keyed by the relational row ID;
- insert, delete, update, rebuild, rank, and metadata filtering are tested;
- search query parameters are bound rather than interpolated.

Adopt:

- authoritative `wiki_facts` rows plus an FTS5 search projection;
- explicit replace/remove operations during approved apply;
- repository filtering in SQL;
- deterministic rank and path tie-breaking;
- rebuild and integrity tests.

Reject:

- generic table-management APIs;
- configurable FTS versions and tokenizers;
- automatic triggers hidden from the existing apply rollback flow.

## LlamaIndex

- Repository: `run-llama/llama_index`
- Commit: `5c0e64eee28ae0f55601cdf29ae3cc03380fea75`
- License: MIT
- Files reviewed:
  - `llama-index-core/llama_index/core/schema.py`
  - `llama-index-integrations/retrievers/llama-index-retrievers-bm25/llama_index/retrievers/bm25/base.py`
  - `llama-index-core/tests/schema/test_node.py`

Relevant design:

- retrievable text is stored separately from flat metadata;
- source relationships and character ranges stay attached to a node;
- metadata filtering is applied before ranking;
- node identity includes content and metadata.

Adopt:

- exact quote plus separate routing text;
- Source identity, SourceVersion, section, Symbol, and Relation metadata;
- repository scope as structured metadata;
- deterministic fact identity.

Reject:

- embeddings and vector stores;
- `bm25s`, NumPy, stemmer, and persistence dependencies;
- UUID node IDs;
- metadata text injected into answer evidence.

## MemoryForge Decision

Add:

- `wiki_facts` as authoritative relational rows;
- `wiki_fact_fts` using built-in SQLite FTS5;
- approved-apply replacement, archive removal, and rollback restoration;
- a read-only fact search API.

The first PR does not call the fact search API from `answer_question`. It proves
index integrity and replay first. Query experiments remain single-variable and
must use frozen development labels.
