# Exact Symbol Routing Research

## Scope

This research covers exact code-symbol routing and fact selection only. It does
not cover generic semantic retrieval, support thresholds, or model-based
ranking.

## Aider

- Repository: `Aider-AI/aider`
- Commit: `5dc9490bb35f9729ef2c95d00a19ccd30c26339c`
- License: Apache-2.0
- License SHA256:
  `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`
- Reviewed:
  - `aider/coders/base_coder.py`
  - `aider/repomap.py`
  - `tests/basic/test_repomap.py`
  - `aider/website/docs/repomap.md`

Relevant design:

- identifier mentions preserve underscores because they are split on
  non-word characters;
- parser-derived definitions and references remain separate from free text;
- an exact mentioned identifier receives an explicit ranking multiplier;
- file and symbol identity influence routing before context is rendered.

Adopt:

- preserve explicit qualified and snake_case identifiers as complete routing
  keys;
- match those keys against parser-derived Symbol metadata before generic text
  scoring;
- keep the exact-match signal inspectable in the query trace.

Reject:

- PageRank, dependency-graph ranking, token budgets, and LLM context assembly;
- rebuilding a second repository map;
- Aider dependencies or source code.

Reason: MemoryForge already has a deterministic Code Index and an applied Wiki
Fact projection. Graph ranking would add cost and an architecture not required
for exact lookup.

## Zoekt

- Repository: `sourcegraph/zoekt`
- Commit: `a3af895d493fe19ed648a2b3db863e62757d0199`
- License: Apache-2.0
- License SHA256:
  `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`
- Reviewed:
  - `query/parse.go`
  - `query/query.go`
  - `query/parse_test.go`
  - `index/matchtree.go`
  - `index/index_test.go`
  - `doc/query_syntax.md`

Relevant design:

- `sym:` is a distinct query type rather than a generic content boost;
- symbol matches must align with indexed Symbol sections;
- malformed empty Symbol queries fail closed;
- tests distinguish matching text inside a Symbol from identical text outside
  a Symbol;
- repository and other scopes compose with the Symbol query.

Adopt:

- make exact Symbol lookup a separate typed path;
- only accept applied rows with non-null Symbol metadata;
- parameterize repository scope;
- distinguish exact qualified-name matching from display-name suffix matching;
- fail closed on ambiguous cross-repository matches.

Reject:

- trigram indexes, regex Symbol queries, ctags, shard infrastructure, scoring
  constants, and search servers.

Reason: MemoryForge needs deterministic exact routing over at most three Wiki
pages, not a general code-search engine.

## MemoryForge Decision

Use `wiki_facts.symbol` as the applied relational projection of the existing
parser-derived Code Index. This avoids persisting a second Code Index snapshot
while retaining SourceVersion, repository, page, and Citation identity.

The query path will:

1. extract explicit code identifiers without changing generic document terms;
2. find applied exact qualified-name matches first;
3. use exact display-name suffix matches only when the question supplies an
   unqualified identifier;
4. route matching pages before generic Wiki and Source FTS;
5. prioritize the matching Symbol Fact during fact selection;
6. preserve the three-page budget and repository scope.

No code is copied from either reference.

## Expected Metrics

- exact-symbol development cases: 100% Answer accuracy;
- exact-symbol Citation grounding: 100%;
- learn-claude-code development Answer accuracy: at least 90%;
- page route recall@3: 100%;
- fact selection accuracy: 100% on answerable development cases;
- multi-source coverage: 100%;
- repository path isolation: 100%;
- known unanswerable case remains a visible failure until the support-score
  phase.
