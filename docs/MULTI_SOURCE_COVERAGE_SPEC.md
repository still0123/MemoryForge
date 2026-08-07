# Multi-Source Coverage Selection Specification

## Status

`ACCEPTED_DEVELOPMENT_AND_LOCAL_REGRESSION`

## Frozen Inputs

- Base Commit:
  `e3f11aaa3658b338d3cc66096fe9fd926f1ce763`
- Development suite:
  `demo/evaluation/multi_source_coverage_development.json`
- Development SHA256:
  `0815f3a2230fbc2b094310f03ce23dd7bf1bf51f362707dd32cd0ae0cd04fb73`
- Development cases: 6
- Confirmation suite:
  `demo/evaluation/multi_source_coverage_confirmation.json`
- Confirmation SHA256:
  `2759e7f3cc34e97b575e59a128a21e7c56e07d5d3afe08d8c80d02f4dc5a5a1a`
- Confirmation cases: 4
- Confirmation status: `not_run`

The confirmation split must not run during development.

## Baseline Result

- Evidence:
  `demo/results/multi_source_coverage_baseline_rejected.json`
- MemoryForge Commit:
  `79c41bc5fdb08de18351546f7869b8083da3a1b2`
- Evidence SHA256:
  `6fa99baefed3cfa2b50bb044b3293c24ee90e697f0ff1a7f6636e91b3c548f81`
- Result: `REJECTED`
- Selection accuracy: 33.3%
- Source coverage accuracy: 33.3%
- Term coverage accuracy: 100.0%
- Single-source rank preservation: 100.0%
- Duplicate source rate: 33.3%
- Deterministic replay: passed
- Confirmation status: `not_run`

The baseline failed four source-quota cases:
`new-source-wins-rank-tie`, `source-quota-precedes-extra-terms`,
`three-source-quota`, and `source-version-is-part-of-identity`.

## Candidate 1 Result

- Evidence:
  `demo/results/multi_source_coverage_development_candidate_1.json`
- MemoryForge Commit:
  `4303c159caac6c8bded2eeb9e0e3cba625f61dc2`
- Evidence SHA256:
  `ef584ff97687ff4a09644f203b9b8d135c420918e4a19e27a8f7354a3e9b5197`
- Result: `DEVELOPMENT_PASS_REGRESSION_PENDING`
- Selection accuracy: 100.0%
- Source coverage accuracy: 100.0%
- Term coverage accuracy: 100.0%
- Single-source rank preservation: 100.0%
- Duplicate source rate: 0.0%
- Deterministic replay: passed
- Confirmation status: `not_run`

## Support-Score Regression

- Evidence:
  `demo/results/support_score_multi_source_coverage_regression.json`
- MemoryForge Commit:
  `1fb2a1263f82ac9720b49543d177345567505c6d`
- Evidence SHA256:
  `631d6aace75de30fa7c68badd8f040163f8480db7b7a40a1eb60eae5fabc0b88`
- Evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`
- Accepted Candidate 14 evaluation SHA256:
  `1403431c27d6e1928699b868a285a932ed3a3ee84961c83f1f5e1ff8016eaa96`
- Answer accuracy: 100.0%
- Citation grounding accuracy: 100.0%
- Multi-source coverage: 100.0%
- Selective accuracy: 100.0%
- Coverage: 90.0%
- Risk: 0.0%
- Support-score confirmation status: `not_run`
- Multi-source confirmation status: `not_run`

## Candidate 1 Full Local Gate

- Gate Commit:
  `73fa41087d222833b5025f5406ea3089b3f4519a`
- Acceptance Evidence:
  `demo/results/multi_source_coverage_candidate_1_local_gate.json`
- Acceptance Evidence SHA256:
  `6762a919accee61979507842bd912c9c9921259eeeeaeb0751e905fe63ef4bf6`
- Ruff check and format: passed
- Strict Mypy: passed
- Registry validation: passed
- Dependency check: passed
- Pytest: 485 passed
- Coverage: 88%
- Wheel clean-room: passed
- sdist clean-room: passed
- `pip check`: passed
- CLI version smoke: passed
- Multi-source confirmation status: `not_run`
- Support-score confirmation status: `not_run`

Candidate 1 is accepted for development and local regression. Its production
change remains ineligible for a confirmation run until the release-candidate
confirmation gate is explicitly opened.

## Problem

`_top_matches` greedily selects Facts that cover uncovered question terms.
It does not know the query's required distinct-source count. A higher-ranked
Fact from an already-selected `(source_id, source_version)` can therefore
displace a lower-ranked Fact from a new source. Support scoring detects the
missing source only after selection and returns `unknown`.

The failure is selection-time Evidence Gap, not support-threshold failure.

## Hypothesis

Adding the frozen source quota to the existing greedy selector will fill the
quota before spending remaining Citation budget on repeated sources. Within
the same source-coverage class, uncovered question terms and existing ranks
remain deterministic tie-breakers.

## Selection Contract

1. keep the highest-ranked first Citation;
2. deduplicate exact `(source_id, source_version, locator)` Citations before
   consuming result budget;
3. source identity is `(source_id, source_version)`;
4. while distinct selected sources are below `required_sources`, prefer a
   candidate from a new source;
5. then prefer the candidate covering the most uncovered question terms;
6. preserve existing rank fields as deterministic tie-breakers;
7. after the source quota is met, uncovered terms lead selection as before;
8. `required_sources=1` preserves current single-source ranking;
9. do not increase the three-page Wiki expansion budget;
10. do not add embeddings, model calls, dependencies, question cases, or
    repository-specific production branches.

## Metrics

The component benchmark reports:

- `selection_accuracy`: expected source versions and expected terms both
  selected;
- `source_coverage_accuracy`: every expected `(source_id, source_version)` is
  selected;
- `term_coverage_accuracy`: every expected term appears in selected quotes;
- `single_source_rank_preservation`: single-source cases keep the top-ranked
  source;
- `duplicate_source_rate`: selected Citations beyond the number of distinct
  selected source versions, divided by selected Citations;
- deterministic replay SHA256;
- selector source-quota capability;
- confirmation status.

## Development Gates

Accept only if:

- selection accuracy is 100.0%;
- source coverage accuracy is 100.0%;
- term coverage accuracy is 100.0%;
- single-source rank preservation is 100.0%;
- duplicate source rate is 0.0% for cases whose Citation budget equals their
  source quota;
- both clean development runs are byte-deterministic;
- MemoryForge Commit remains stable;
- MemoryForge worktree remains clean;
- all existing query, support-score, Agent, evaluation, registry, lint, type,
  coverage, Wheel, and sdist gates pass;
- support-score development metrics do not regress;
- support-score confirmation remains `not_run`;
- multi-source confirmation remains `not_run`.

Any failed development or regression gate retains immutable Evidence.

## Research

Fixed references and design decisions are recorded in:

`docs/research/MULTI_SOURCE_COVERAGE.md`.

## Forbidden

- running either frozen confirmation suite during development;
- modifying frozen candidates, ranks, expected source versions, or expected
  terms to improve metrics;
- embedding or model-based diversity;
- repository, suite, case ID, or expected-answer special cases;
- increasing page or Citation budgets;
- deleting negative or superseded Evidence.
