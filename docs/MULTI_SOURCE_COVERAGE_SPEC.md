# Multi-Source Coverage Selection Specification

## Status

`PREREGISTERED_BASELINE_PENDING`

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
