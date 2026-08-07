# Evidence Support Score Specification

## Status

`CANDIDATE_3_DEVELOPMENT_PASSED_REGRESSION_PENDING`

## Frozen Inputs

- Base Commit:
  `42fe0e8fff8d543c4e28053f15458ff8f2138329`
- Development suite:
  `demo/evaluation/learn_claude_code_qa_dev_v031.json`
- Development suite SHA256:
  `3085859283115b351ce1c38a6bf1c111b1ac7e669f96ed1e3aa4f9fc31610b7d`
- Exact-Symbol development Evidence:
  `demo/results/exact_symbol_routing_development_final.json`
- Evidence SHA256:
  `a72494d69964a1c93b228fb73ffa1d8608bf714153cc54efa1096984addc0fc7`
- Confirmation suite SHA256:
  `b86fe1f7999c09af9bf7c6bed4951c1c4c565318c39192bea1831d942466c117`
- Confirmation status: `not_run`

The confirmation split must not run in this phase.

## Baseline

- Answer accuracy: 90.0%;
- page route recall@3: 100.0%;
- Source recall@3: 100.0%;
- fact selection accuracy: 100.0%;
- Citation grounding: 100.0%;
- multi-source coverage: 100.0%;
- abstention accuracy: 0.0%;
- repository path isolation: 100.0%.

The sole failure is
`dev-unknown-vector-database`, classified `wrong_abstention`.

## Hypothesis

The unsupported question passes current matching because a generic code-kind
term and one topical term are enough to select a signature Fact. Its evidence
does not cover the core conclusion: storage of embeddings in a vector
database.

A fixed support threshold over explicit Fact properties should reject this
answer without changing page routing or any answerable fact selection.

## Support Contract

Every selected answer receives:

- `score`: 0-100;
- `threshold`: 75;
- `sufficient`: boolean;
- six named components from 0-1;
- a deterministic list of failed hard gates.

Component weights:

- exact identifier coverage: 0.20;
- core question-term coverage: 0.35;
- conclusion and condition co-location: 0.15;
- negation alignment: 0.10;
- multi-source coverage: 0.10;
- current SourceVersion grounding: 0.10.

Rules:

1. generic code-kind words do not count as core support;
2. exact identifiers are complete tokens, never substring fragments;
3. a conditional question requires its condition terms in the same selected
   Fact as the conclusion;
4. a negated question requires aligned negation in selected evidence;
5. a multi-source request requires the requested Citation count;
6. every selected Citation must identify the currently applied
   SourceVersion;
7. Code Wiki file-page answers require score >= 75 and no failed hard gate;
8. insufficient support returns `unknown`;
9. non-Code Wiki answers expose the score but are not rejected in this focused
   candidate.

## Evaluation Contract

Add deterministic outputs:

- per-case support score and components;
- selective accuracy among answered cases;
- answer coverage;
- risk at the frozen operating point;
- threshold and per-suite operating point;
- existing Answer, route, fact, Citation, multi-source, abstention, and
  repository metrics unchanged.

## Development Gates

Accept only if:

- Answer accuracy is 100%;
- selective accuracy is 100%;
- coverage is 90%;
- risk is 0%;
- abstention accuracy is 100%;
- page route recall@3 is 100%;
- Source recall@3 is 100%;
- fact selection accuracy is 100%;
- Citation grounding is 100%;
- multi-source coverage is 100%;
- repository path isolation is 100%;
- the unsupported vector-database case returns `unknown`;
- all registered query, Relation, structural, lifecycle, lint, type, coverage,
  Wheel, and sdist gates pass.

Any failed gate rejects production integration and retains its Evidence.

## Candidate 1 Development Result

- Candidate Commit:
  `99d20a259350693292ada852f3b18ab98aa1c172`;
- Evidence:
  `demo/results/support_score_development.json`;
- Evidence SHA256:
  `d6699dd57109b5bbc573fdf17355084a3ac045e43f5766f4c539c42621208a75`;
- deterministic evaluation SHA256:
  `a6c1fd8685dc4877a4f828da4eafba32344ee8cb3833a28434467da816e0e2ef`.

Development metrics:

- Answer accuracy: 100.0%;
- selective accuracy: 100.0%;
- coverage: 90.0%;
- risk: 0.0%;
- abstention accuracy: 100.0%;
- page route recall@3: 100.0%;
- Source recall@3: 100.0%;
- fact selection accuracy: 100.0%;
- Citation grounding: 100.0%;
- multi-source coverage: 100.0%;
- repository path isolation: 100.0%.

The unsupported vector-database case scores 55.0 against threshold 75.0 and
returns `unknown`. All answerable cases score from 85.0 to 91.2.

Confirmation remains `not_run`.

## Regression Result

Candidate 1 failed the full local gate at Commit
`e41bb48a63d40e9bcecab74c25a8a7061f2464a5`:

- 457 tests passed and one failed;
- failed node:
  `tests/test_git_sync.py::test_code_symbol_queries_answer_methods_and_struct_fields`;
- the valid `FileSystem` field answer scored 72.8 against threshold 75.0 and
  returned `unknown`;
- Wheel and sdist checks did not run after the pytest failure.

Regression Evidence:
`demo/results/support_score_candidate_1_regression_rejected.json`.

The root cause is generic support accounting, not the frozen threshold:
expanded field-kind synonyms and Chinese query scaffolding dilute the
identifier's page-local support. Candidate 1 is rejected. Confirmation remains
`not_run`.

## Candidate 2 Development Result

Candidate 2 counts an explicit identifier as core support when it appears in
the selected Code Wiki page's verified Facts. Predicate and condition terms
still require selected Citation coverage. No threshold, weight, route, frozen
suite, or confirmation input changed.

- Candidate Commit:
  `807e8e76e0b787b7c7eae08f405afbeca6c9a783`;
- Evidence:
  `demo/results/support_score_development_final.json`;
- Evidence SHA256:
  `4592fb565ae53596dd69b7ca80a4870f2b7430b03f9a3a2e5d9fa4660902aa29`;
- deterministic evaluation SHA256:
  `a6c1fd8685dc4877a4f828da4eafba32344ee8cb3833a28434467da816e0e2ef`.

Candidate 2 retains all Candidate 1 development metrics. Both clean runs are
byte-deterministic at the evaluation layer. The unsupported case remains 55.0
and returns `unknown`. Confirmation remains `not_run`.

## Candidate 2 Regression Result

Candidate 2 failed the full local gate at Commit
`dd7ac2df2af8da205f46044dd39cc4d2e1e41604`:

- 457 tests passed and one failed;
- failed node:
  `tests/test_query_workflow.py::test_ask_expands_no_more_than_the_page_budget`;
- support scoring reread `b.md`, producing three page reads for a two-page
  route;
- Wheel and sdist checks did not run after the pytest failure.

Regression Evidence:
`demo/results/support_score_candidate_2_regression_rejected.json`.

Candidate 2 is rejected. Candidate 3 must reuse code-page Fact terms collected
during the bounded expansion loop and perform no additional page read.
Confirmation remains `not_run`.

## Candidate 3 Development Result

Candidate 3 reuses code-page Fact terms collected during the existing bounded
page-expansion loop. It performs no support-specific page read.

- Candidate Commit:
  `ec83a05c46bef6882658b314fd3aedf8ab2cc161`;
- Evidence:
  `demo/results/support_score_development_candidate_3.json`;
- Evidence SHA256:
  `a04876a4565cc3fe715dcfa3a479a1b662b3c2050dc81186deedbbea58ca43c0`;
- deterministic evaluation SHA256:
  `a6c1fd8685dc4877a4f828da4eafba32344ee8cb3833a28434467da816e0e2ef`.

Candidate 3 retains all accepted development metrics and passes both focused
regression tests. Confirmation remains `not_run`.

## Forbidden

- LLM judge or model confidence;
- question, repository, suite, or expected-answer special cases;
- modifying frozen labels or required terms;
- embeddings, vector stores, graph stores, or new dependencies;
- confirmation or holdout execution;
- increasing the three-page default budget.
