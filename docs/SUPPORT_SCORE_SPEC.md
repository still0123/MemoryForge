# Evidence Support Score Specification

## Status

`ACCEPTED_DEVELOPMENT_AND_LOCAL_REGRESSION`

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

Candidate 6 passed the full local gate at Commit
`350fc0883c4b818fa9d57f3acbeb07e23920fdf5`: 467 tests, 88% coverage,
Ruff, strict Mypy, registry validation, dependency checks, Wheel, and sdist all
passed. Evidence review then found three remaining validator gaps:

- per-case validation did not reconstruct expected hard gates;
- accepted development validation trusted recorded gates without checking
  their complete true state and per-case payloads;
- local-gate validation did not require the complete registry, pytest, and
  artifact schema.

Candidate 6 is superseded.

## Candidate 7 Development Result

Candidate 7 reconstructs hard gates from each case and validates exact
development and local-gate schemas, including retained artifact hashes.

- Candidate Commit:
  `e8c4c6587051ee83e8c42be0604281814cbcc6d6`;
- Evidence:
  `demo/results/support_score_development_candidate_7.json`;
- Evidence SHA256:
  `49b789f110684b9f0e6331fe74b2cb186c1394c16e0d970ca750b5b6d04497b0`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 7 retains all accepted development metrics and the unsupported score
of 35.0. Both clean runs are deterministic. Confirmation remains `not_run`.

## Candidate 7 Full Local Gate

- Gate Commit:
  `3b5447a9f8fa592adc2e5ac48930f9da9a56be5f`;
- acceptance Evidence:
  `demo/results/support_score_candidate_7_local_gate.json`;
- acceptance Evidence SHA256:
  `1e0ad1a190d5051f6c9ebdc4dcd71cf14bde007e12fef7289ab6af6d8b7733e1`;
- Ruff check and format: passed;
- strict Mypy: passed;
- registry: 12 release suites, two experiments, 31 Evidence artifacts, and 121
  QA cases;
- dependency check: passed;
- pytest: 467 passed;
- coverage: 88%;
- Wheel clean-room: passed;
- sdist clean-room: passed;
- `pip check` and CLI version smoke test: passed.

Candidate 7 is the accepted development and local-regression result.
Confirmation remains `not_run`.

Final release review found four P1 gaps after Candidate 7:

- conditional co-location also required a page-level identifier in the same
  Citation;
- support validation silently ignored identifiers after the routing limit of
  eight;
- Agent final answers were not revalidated against the original question and
  selected Citation Facts;
- accepted Evidence could self-declare a threshold different from the frozen
  source manifest.

Candidate 7 is superseded.

## Candidate 8 Development Result

Candidate 8 separates page-level identifier support from conditional
co-location, validates all explicit identifiers, checks Agent final answers
against original-question Citations and Fact terms, and reads the frozen
threshold from the validated source manifest.

- Candidate Commit:
  `9667b0585a865ba6af9ef13f5eafb674b0c52026`;
- Evidence:
  `demo/results/support_score_development_candidate_8.json`;
- Evidence SHA256:
  `e8c3da42d39ab2ce3ff471761cd32a06f79a5e0ad330589c97da509cc8457ed7`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 8 retains all accepted development metrics and the unsupported score
of 35.0. Both clean runs are deterministic. Confirmation remains `not_run`.

## Candidate 8 Full Local Gate

- Gate Commit:
  `dc6c70efa3edec8782b9514764efbf37cf20e22b`;
- acceptance Evidence:
  `demo/results/support_score_candidate_8_local_gate.json`;
- acceptance Evidence SHA256:
  `2396ca6b9b4bfd401d313e96d1551bf24dd94aabd3a4c24d37048fc0f8c61ae0`;
- Ruff check and format: passed;
- strict Mypy: passed;
- registry: 12 release suites, two experiments, 34 Evidence artifacts, and 121
  QA cases;
- dependency check: passed;
- pytest: 470 passed;
- coverage: 88%;
- Wheel clean-room: passed;
- sdist clean-room: passed;
- `pip check` and CLI version smoke test: passed.

Candidate 8 is the accepted development and local-regression result.
Confirmation remains `not_run`.

Last static review found four P1 gaps after Candidate 8:

- final-answer support could drop a negation and invert the conclusion;
- common English negation contractions did not trigger the hard gate;
- superseded local-gate Evidence could be removed from registry history;
- development and local-gate Evidence lacked enforced top-level schemas.

Candidate 8 is superseded.

## Candidate 9 Development Result

Candidate 9 aligns final-answer negation polarity, recognizes common English
negation contractions, binds every recorded local gate, and validates exact
Evidence schemas.

- Candidate Commit:
  `63972b0eee534c258a4054f40876de4da7f54880`;
- Evidence:
  `demo/results/support_score_development_candidate_9.json`;
- Evidence SHA256:
  `e3eb0f0568b5688442be314abe3b81b3d9a69f48787c78afea92f61fd993640a`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 9 retains all accepted development metrics and the unsupported score
of 35.0. Both clean runs are deterministic. Confirmation remains `not_run`.

## Candidate 9 Full Local Gate

- Gate Commit:
  `fc2f15f7da52703061b31705d553e211deaa4e97`;
- acceptance Evidence:
  `demo/results/support_score_candidate_9_local_gate.json`;
- acceptance Evidence SHA256:
  `2939506d21e3ffd42777ebc1ac7cfb0f4ae3f13bbcbe51cdcf2415f5ab141d9c`;
- Ruff check and format: passed;
- strict Mypy: passed;
- registry: 12 release suites, two experiments, 38 Evidence artifacts, and 121
  QA cases;
- dependency check: passed;
- pytest: 470 passed;
- coverage: 88%;
- Wheel clean-room: passed;
- sdist clean-room: passed;
- `pip check` and CLI version smoke test: passed.

Candidate 9 is the accepted development and local-regression result.
Confirmation remains `not_run`.

## Candidate 10 Audit Fixes

The final Candidate 9 static review found eight additional P1 contract gaps:

- a page-level identifier could support an unrelated selected Fact;
- Agent final answers could combine terms from separate Citations into an
  unsupported relationship;
- benchmark output inside either repository invalidated the recorded clean
  worktree claim after the claim was computed;
- deterministic replay covered evaluation output but not structural output;
- accepted Evidence did not bind the frozen development case identities;
- accepted and superseded statuses could be reassigned between revisions;
- regression Evidence paths could be replaced while retaining equivalent
  payloads;
- Evidence Commits, local-gate outer SHA256 values, and their contained
  artifact SHA256 values were not pinned outside the mutable registry.

Candidate 10 restricts page-level identifier fallback to field Facts, requires
each final-answer clause to be supported by one Citation, requires external
benchmark output, hashes both structural and evaluation runs, validates frozen
case identities, and pins all experiment, regression, and local-gate Evidence
identities in the validator.

The review warning about Code Wiki module-overview pages was rejected: the
frozen contract deliberately enforces refusal only on Code Wiki file pages in
this focused candidate.

## Candidate 10 Development Result

- Candidate Commit:
  `2729e37529fd7918f2591e5340dcaba2e90f8267`;
- rejected Evidence:
  `demo/results/support_score_development_candidate_10_rejected.json`;
- rejected Evidence SHA256:
  `f040c4760a301143e79c8919e174f34d04d689c0f173738b0d2b7ffa8b5f9c37`.

Candidate 10 failed the frozen development gate:

- Answer accuracy: 90.0%;
- selective accuracy: 100.0%;
- coverage: 80.0%;
- risk: 0.0%;
- Source recall@3, fact selection, and Citation grounding: 88.9%;
- failed case: `dev-s12-task-class`;
- failure classification: `insufficient_support`;
- support score: 68.3.

Both runs were structurally and evaluatively deterministic. MemoryForge and
source Commits remained stable and both worktrees remained clean. The root
cause is semantic: `in s12_task_system.code` is routing context, not the
identifier of the requested answer. Treating it as an answer hard gate rejects
the correctly selected `Task` class Fact.

Candidate 10 is rejected and retained. Candidate 11 excludes identifiers used
as explicit `in ...` or Chinese `... 中/内` routing context from the exact
answer-identifier hard gate. The identifier remains available to routing and
core-term scoring.

## Candidate 11 Development Result

- Candidate Commit:
  `c1cbd94d0a92e3b346afeb32895a4901cb249951`;
- Evidence:
  `demo/results/support_score_development_candidate_11.json`;
- Evidence SHA256:
  `aa4f6625025038e6aac097a510fb7ef1773cdf8168c3ade03870423b1806fe44`;
- structural SHA256:
  `a611b3559bc3f08f5a9b270eb7e377e0b06612b324586e2380a59cef20d8cfe7`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 11 restores all accepted development metrics:

- Answer accuracy: 100.0%;
- selective accuracy: 100.0%;
- coverage: 90.0%;
- risk: 0.0%;
- abstention accuracy: 100.0%;
- route, Source, fact, Citation, multi-source, and repository-isolation
  metrics: 100.0%.

Both structural and evaluation payloads are deterministic across the two clean
runs.

## Candidate 11 Full Local Gate

- Gate Commit:
  `ad67aa09341d451a7661634c0bf714291f0ad452`;
- acceptance Evidence:
  `demo/results/support_score_candidate_11_local_gate.json`;
- acceptance Evidence SHA256:
  `686f4b799d2f7a07bc7bff0a732ddc538d8a3584e2c8962e17f2da39ac9fbe71`;
- Ruff check and format: passed;
- strict Mypy: passed;
- registry: 12 release suites, two experiments, 40 Evidence artifacts, and 121
  QA cases;
- dependency check: passed;
- pytest: 479 passed;
- coverage: 88%;
- Wheel clean-room: passed;
- sdist clean-room: passed;
- `pip check` and CLI version smoke test: passed.

Candidate 11 is the accepted development and local-regression result.
Candidate 10 remains registered as rejected Evidence. Confirmation remains
`not_run`.

## Candidate 12 Audit Fix

Final static review found one remaining P1: clause validation required all
terms to occur in one Citation but did not preserve their order. Evidence
stating `Administrators revoke active sessions` could therefore support the
inverted claim `Active sessions revoke administrators`.

Parallel review then found five related P1 gaps:

- splitting on conjunctions could assign a subjectless predicate to a different
  Citation;
- Agent final validation accepted a strict subset of the Citations required by
  the original question;
- snake_case identifiers were split into reusable component terms;
- ignored work directories inside a repository bypassed clean-worktree gates;
- registry validation trusted the deterministic replay gate without comparing
  the recorded run hashes.

Candidate 12 requires answer terms to form an ordered, identifier-preserving,
morphology-aware subsequence of one Citation; requires the complete verified
Citation set; keeps coordinated claims intact; requires both work and output
paths outside source repositories; and independently compares structural and
evaluation hashes in the accepted Evidence. Cross-language translation remains
conservative: without a deterministic local translation proof, the Agent must
quote or paraphrase in the Citation language instead of bypassing the evidence
gate.

## Candidate 12 Development Result

- Candidate Commit:
  `3b7065856de66b9cf1c74b8f99637a07bfca387f`;
- Evidence:
  `demo/results/support_score_development_candidate_12.json`;
- Evidence SHA256:
  `9f0aa5793ad9cc846795b838922a1c0c9fe91ccce3a856adc4057f4ab38313c6`;
- structural SHA256:
  `b41a7727c82f6115345bd028bd34bc375f20119f11fcef3016917db897c6c21b`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 12 retains 100.0% Answer, route, Source, fact, Citation,
multi-source, abstention, selective, and repository-isolation metrics with
90.0% coverage and 0.0% risk. Both structural and evaluation payloads are
deterministic across clean runs. Confirmation remains `not_run`.

## Candidate 12 Full Local Gate

- Gate Commit:
  `83f39183e68c9d7ebd4c3f8e80e063555cfcb460`;
- acceptance Evidence:
  `demo/results/support_score_candidate_12_local_gate.json`;
- acceptance Evidence SHA256:
  `633021bf534670cb78aca392815799d1d1ca60624049fba09fae0796c4122de9`;
- Ruff check and format: passed;
- strict Mypy: passed;
- registry: 12 release suites, two experiments, 43 Evidence artifacts, and 121
  QA cases;
- dependency check: passed;
- pytest: 481 passed;
- coverage: 88%;
- Wheel clean-room: passed;
- sdist clean-room: passed;
- `pip check` and CLI version smoke test: passed.

Candidate 12 is the accepted development and local-regression result.
Confirmation remains `not_run`.

## Candidate 13 Audit Fix

Candidate 12 final re-review found one remaining P1: ordered subsequence
matching could cross coordinated subject boundaries inside one Citation.
`Administrators revoke sessions and users clear caches` could support the
incorrect claim `Administrators clear caches`.

Candidate 13 accepts an exact same-polarity evidence sentence directly.
Otherwise, it splits the Citation at sentence and coordination boundaries and
requires the full answer clause to be supported within one evidence segment.

## Candidate 13 Development Result

- Candidate Commit:
  `3e20ad8ca0d54fa06fa8ca0aaed4dad959a63009`;
- Evidence:
  `demo/results/support_score_development_candidate_13.json`;
- Evidence SHA256:
  `8b1d45c721b4ecf55a25510ee8851b7891474739186f0c428eb7098b0a894b34`;
- structural SHA256:
  `be5f3707fbd19145e184f6a98597fbc12e05633af32cd466977e035448685e7d`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 13 retains all accepted development metrics: 100.0% Answer,
selective, route, Source, fact, Citation, multi-source, abstention, and
repository-isolation metrics; coverage remains 90.0% and risk remains 0.0%.

## Candidate 13 Full Local Gate

- Gate Commit:
  `d950911524962e92e050e6a20734fe20d25c7b2a`;
- acceptance Evidence:
  `demo/results/support_score_candidate_13_local_gate.json`;
- acceptance Evidence SHA256:
  `636ca38fce142e379f2686d2f0be629666eb8221898ea4d1c9e54c22123e0bd6`;
- Ruff check and format: passed;
- strict Mypy: passed;
- registry: 12 release suites, two experiments, 45 Evidence artifacts, and 121
  QA cases;
- dependency check: passed;
- pytest: 481 passed;
- coverage: 88%;
- Wheel clean-room: passed;
- sdist clean-room: passed;
- `pip check` and CLI version smoke test: passed.

Candidate 13 is the accepted development and local-regression result.
Confirmation remains `not_run`.

## Candidate 14 Fail-Closed Contract

Candidate 13 re-review showed that heuristic entailment remained open-ended:
substring matching accepted truncated identifiers, while unenumerated
coordination boundaries could still reassign semantic roles.

Candidate 14 removes heuristic paraphrase entailment. An Agent answer clause is
accepted only when its normalized text equals one complete Citation sentence or
coordination segment with matching negation polarity. This deliberately trades
free paraphrasing for deterministic, local, auditable final-answer grounding.

## Candidate 14 Development Result

- Candidate Commit:
  `4f2f15ddc06882612a547aaad81d4b67b4ffbf8b`;
- Evidence:
  `demo/results/support_score_development_candidate_14.json`;
- Evidence SHA256:
  `7159992bf66e87e4cfd4e6fde70e65fa50d169002276d444bd6255223a93254a`;
- structural SHA256:
  `84aff8ba7c4012847522e04de42d599fa65a02526646065b41c038079a4ff24e`;
- deterministic evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`.

Candidate 14 retains all accepted development metrics: 100.0% Answer,
selective, route, Source, fact, Citation, multi-source, abstention, and
repository-isolation metrics; coverage remains 90.0% and risk remains 0.0%.

## Candidate 14 Full Local Gate

- Gate Commit:
  `8d5b9cbf297b8f45f209096021360f6c8e37c5bf`;
- acceptance Evidence:
  `demo/results/support_score_candidate_14_local_gate.json`;
- acceptance Evidence SHA256:
  `6f36800dbb453fec0b066fae3ecf72d16f1e5ce87822e53c248257e6b26b9b83`;
- Ruff check and format: passed;
- strict Mypy: passed;
- registry: 12 release suites, two experiments, 47 Evidence artifacts, and 121
  QA cases;
- dependency check: passed;
- pytest: 481 passed;
- coverage: 88%;
- Wheel clean-room: passed;
- sdist clean-room: passed;
- `pip check` and CLI version smoke test: passed.

Candidate 14 is the accepted development and local-regression result.
Confirmation remains `not_run`.

## Forbidden

- LLM judge or model confidence;
- question, repository, suite, or expected-answer special cases;
- modifying frozen labels or required terms;
- embeddings, vector stores, graph stores, or new dependencies;
- confirmation or holdout execution;
- increasing the three-page default budget.
