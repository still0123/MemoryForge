# Exact Symbol Routing Specification

## Status

`PREREGISTERED_DEVELOPMENT_ONLY`

## Frozen Inputs

- Base Commit:
  `650449eb735587f5e6cf8f6afd034af0ee46c639`
- Development suite:
  `demo/evaluation/learn_claude_code_qa_dev_v031.json`
- Development suite SHA256:
  `3085859283115b351ce1c38a6bf1c111b1ac7e669f96ed1e3aa4f9fc31610b7d`
- Frozen baseline Evidence:
  `demo/results/learn_claude_code_qa_dev_v031.json`
- Baseline Evidence SHA256:
  `65ae9959e64b67e45bdc2a4572548af4bffe98026fbb511687f108d32a2b099e`
- Confirmation suite SHA256:
  `b86fe1f7999c09af9bf7c6bed4951c1c4c565318c39192bea1831d942466c117`
- Confirmation status: `not_run`

The confirmation split must not run in this phase.

## Baseline

- Answer accuracy: 60.0%;
- page route recall@3: 100.0%;
- Source recall@3: 100.0%;
- Citation grounding: 88.9%;
- multi-source coverage: 100.0%;
- abstention accuracy: 0.0%;
- repository path isolation: 100.0%.

The four known failures remain immutable:

- exact selection for `check_permission`;
- exact selection for `run_todo_write`;
- two-source `agent_loop` fact selection;
- unsupported vector-database question does not abstain.

## Prior Candidate

`experiment/v031-codewiki-specificity@4afd69a66a120376eed00f2ebb93d7ecdea47e5d`
reached 100% on development by preserving snake_case terms and removing generic
code-kind terms. Confirmation was not run.

That production diff is not reused because the current Goal requires exact
Symbol questions to query the Code Index before generic text heuristics.
Its positive result remains retained evidence, not a release claim.

## Hypothesis

Three answerable failures share one root cause: page routing already finds the
correct pages, but generic term overlap cannot distinguish exact parser-derived
Symbols. Querying the applied Code Index Symbol projection before generic text
ranking should select the exact signature Facts without expanding page recall.

The unsupported vector-database failure has a different root cause:
insufficient evidence. It is deliberately deferred to the support-score phase.

## Candidate

1. Add a read-only exact Symbol lookup over applied `wiki_facts.symbol` rows.
2. Keep repository scope as a bound SQL parameter.
3. Match fully qualified names exactly.
4. Match unqualified identifiers only as a complete display-name suffix.
5. Fail closed when an unscoped qualified match spans repositories.
6. Route exact Symbol pages before generic Wiki and Source FTS.
7. Prioritize matched Symbol Facts during fact selection.
8. Record the Symbol route in the deterministic trace.

The candidate must not:

- change generic document tokenization;
- add question, repository, suite, or answer special cases;
- add a dependency, model call, vector store, graph store, or compatibility
  layer;
- expand more than three Wiki pages;
- modify any frozen suite, expected source, required term, or confirmation
  input.

## Typed Lookup Contract

Input:

- one or more explicit identifiers;
- optional repository ID;
- result limit from 1 to 100.

Output:

- deterministic exact matches with Fact ID, page path, repository ID,
  SourceVersion, locator, quote, and Symbol;
- exact qualified matches before suffix matches;
- ordering by match type, Symbol, page path, and Fact row ID.

Only rows joined to `applied_source_versions` are eligible.

## Development Gates

Accept this focused routing phase only if:

- all four `exact_symbol` overlay cases answer correctly;
- learn-claude-code development Answer accuracy is at least 90%;
- page route recall@3 is 100%;
- Source recall@3 is 100%;
- fact selection accuracy is 100% for answerable cases;
- Citation grounding is 100%;
- multi-source coverage is 100%;
- repository path isolation is 100%;
- the known unanswerable case is still reported honestly;
- all registered document QA, Code Wiki structure, Code Wiki QA, lifecycle,
  lint, type, test, coverage, Wheel, and sdist gates do not regress.

Any failed gate rejects the production integration while retaining the report.

## Deferred

- generic code-kind support penalties;
- explainable support score;
- abstention threshold;
- selective accuracy, coverage, and risk-coverage;
- confirmation and holdout.
