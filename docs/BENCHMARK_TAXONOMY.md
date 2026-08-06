# Benchmark Taxonomy Baseline

## Decision

`ACCEPTED_EVALUATION_ONLY`

Commit `1d025e6a3aa56a7ed70d4a3fba1959dafdd95b72` separates page routing,
selected-source recall, fact selection, and final Answer accuracy. It does not
change query ranking, page budget, facts, labels, or thresholds.

## Scope

- Registered QA cases: 121
- Evaluated consumed cases: 116
- Frozen learn-claude-code confirmation: not run
- Evaluated suites/splits: 9
- Maximum Wiki pages: 3
- Model calls: 0

## Macro Results

| Metric | Macro | Suite denominator |
| --- | ---: | ---: |
| Answer accuracy | 54.3% | 9 |
| Page route recall@3 | 87.1% | 9 |
| Fact source recall | 78.4% | 9 |
| Fact selection accuracy | 55.0% | 9 |
| Citation grounding | 98.8% | 9 |
| Repository path isolation | 100.0% | 6 |

Repository isolation excludes three legacy suites without repository labels.
It does not convert N/A to zero.

## Failure Flow

| Classification | Cases |
| --- | ---: |
| `page_route_miss` | 11 |
| `fact_selection_miss` | 29 |
| `multi_source_incomplete` | 1 |
| `wrong_abstention` | 4 |
| Passing (`none`) | 71 |

The full 45-case failure list is stored in
[`benchmark_taxonomy_baseline.json`](../demo/results/benchmark_taxonomy_baseline.json).
Each row retains suite, split, case ID, category, route outcome, fact outcome,
and one primary deterministic classification.

## Known Gaps

- immutable legacy categories remain `single_hop`, `multi_source`,
  `paraphrase`, and `unanswerable`;
- a separate frozen case-type overlay maps existing cases to `exact_symbol`,
  `code_behavior`, `temporal_update`, and `cross_repository` without rewriting
  legacy labels;
- support score, selective accuracy, coverage, and risk-coverage remain future
  phases;
- historical Evidence remains immutable and therefore does not gain the new
  fields retroactively.

## Evidence

- Evidence SHA256:
  `fe103345252782f7ba4929bfa502c46509741067b53b17979d19427cc3d01cef`
- Frozen confirmation SHA256:
  `b86fe1f7999c09af9bf7c6bed4951c1c4c565318c39192bea1831d942466c117`
