# Evidence Support Score Specification

## Status

`CANDIDATE_6_DEVELOPMENT_PASSED_REGRESSION_PENDING`

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

Candidate 3 also passed the full local gate at Commit
`14618ac3a626dc925375fb600727bca83b46cc0a`: 458 tests, 88% coverage,
Ruff, strict Mypy, registry validation, dependency checks, Wheel, and sdist all
passed. Static review then found seven P1 contract defects:

- distinct locators from one SourceVersion could satisfy multi-source support;
- conditional co-location only counted matching terms;
- explicit identifiers without a Symbol match received full coverage;
- Code Wiki pages were reread during support scoring;
- dirty worktrees could generate passed Evidence;
- per-case Support schema validation accepted incomplete objects;
- registry status requirements could self-delete negative Evidence.

Candidate 3 is retained as `accepted_development_superseded`.

## Candidate 4 Development Result

Candidate 4 fixes all seven review findings without changing the frozen
threshold, weights, suite, expected sources, required terms, or confirmation
input. A correct no-candidate rejection may expose `support: null`; selected
evidence must expose the complete Support contract.

- Candidate Commit:
  `80ac72c0fbfbf393c137aa1c25e5a44c91ae5325`;
- Evidence:
  `demo/results/support_score_development_candidate_4.json`;
- Evidence SHA256:
  `c4fab97ed96cdc35d61540052b49b5ab18764fd0d98e641ae3a11f3deb2c258e`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 4 retains all accepted development metrics. The unsupported case
scores 35.0 and returns `unknown`; answerable cases remain 85.0-91.2. Both
clean runs are deterministic. Confirmation remains `not_run`.

Candidate 4 passed the full local gate at Commit
`e86d23ef34260608e1dbca46047a812c479454f1`: 463 tests, 88% coverage,
Ruff, strict Mypy, registry validation, dependency checks, Wheel, and sdist all
passed. Follow-up static review found four remaining P1 contract defects:

- CamelCase suffixes could satisfy an exact identifier;
- dirty source checkouts could generate passed Evidence;
- Support schema validation did not recompute scores or enforce semantic
  consistency;
- status-set validation could not preserve every individual negative Evidence.

Candidate 4's full local gate is retained in
`demo/results/support_score_candidate_4_local_gate.json`. Candidate 4 is
superseded.

## Candidate 5 Development Result

Candidate 5 uses full identifier tokens and requires every explicit identifier
to be covered. It rejects dirty or moving source checkouts, recomputes Support
scores from the frozen components, validates status semantics, and binds each
experiment to its complete Evidence history.

- Candidate Commit:
  `655df04b1b90a0892c1a815a503548014d10d8ee`;
- Evidence:
  `demo/results/support_score_development_candidate_5.json`;
- Evidence SHA256:
  `618f856f02ab077f63e3ca31cc6fb614f4c1b985ae0eecaee1f9d54c8060c42f`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 5 retains all accepted development metrics and the unsupported score
of 35.0. Both clean runs are deterministic. Confirmation remains `not_run`.

## Full Local Gate

- Gate Commit:
  `e13b3515189a8428344399fc36c0df7622e4a7f0`;
- acceptance Evidence:
  `demo/results/support_score_candidate_5_local_gate.json`;
- acceptance Evidence SHA256:
  `94ec66a56ae56ebd9c60b2cc30a9784f899b04cb4b7e983bc1a3ac7f321a4123`;
- Ruff check and format: passed;
- strict Mypy: passed;
- registry: 12 release suites, two experiments, 29 Evidence artifacts, and 121
  QA cases;
- dependency check: passed;
- pytest: 466 passed;
- coverage: 88%;
- Wheel clean-room: passed;
- sdist clean-room: passed;
- `pip check` and CLI version smoke test: passed.

The registry now requires machine-readable local-gate Evidence for each final
`accepted_development` artifact. Confirmation remains `not_run`.

Follow-up review found two P1 production defects after Candidate 5's local
gate:

- the citation budget was also treated as the minimum required source count;
- page-level identifier fallback compared case-folded names even though Python,
  Go, and TypeScript identifiers are case-sensitive.

Candidate 5 is superseded.

## Candidate 6 Development Result

Candidate 6 separates `max_citations` from explicit `min_source_count` and
preserves identifier case during exact support checks.

- Candidate Commit:
  `f080b3a71132a453b61420e1fae1ab333824bc46`;
- Evidence:
  `demo/results/support_score_development_candidate_6.json`;
- Evidence SHA256:
  `f872d3e3a7fe1cf2a18f9740734c0d560e2413188d9b8984c89b6d4ecb31af2b`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 6 retains all accepted development metrics and the unsupported score
of 35.0. Both clean runs are deterministic. Confirmation remains `not_run`.

## Forbidden

- LLM judge or model confidence;
- question, repository, suite, or expected-answer special cases;
- modifying frozen labels or required terms;
- embeddings, vector stores, graph stores, or new dependencies;
- confirmation or holdout execution;
- increasing the three-page default budget.
