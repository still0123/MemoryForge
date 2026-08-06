# v0.3.1 Snake Case Identifier Experiment

## Status

`REJECTED`

## Frozen baseline

- Dataset: `learn_claude_code_qa_dev_v031.json`
- Dataset SHA256: `3085859283115b351ce1c38a6bf1c111b1ac7e669f96ed1e3aa4f9fc31610b7d`
- Answer accuracy: 60.0%
- Source recall@3: 100.0%
- Citation grounding: 88.9%
- Multi-source coverage: 100.0%
- Abstention accuracy: 0.0%
- Repository path isolation: 100.0%

## Hypothesis

`_terms` splits snake_case identifiers into common fragments and discards the complete identifier.
Consequently, `check_permission` ties with earlier `check_*` functions, `run_todo_write` ties with
`run_bash`, and `agent_loop` ties with the containing module.

## Candidate

Preserve each complete snake_case token while retaining its existing component terms. Do not change
page routing, ranking tuple order, matching thresholds, abstention logic, labels, or dependencies.

## Acceptance

The candidate is accepted only if all frozen development gates pass:

- Answer accuracy >= 80%
- Source recall@3 = 100%
- Citation grounding = 100%
- Multi-source coverage = 100%
- Abstention accuracy = 100%
- Repository path isolation = 100%

Any failed gate rejects the candidate. Confirmation must not run.

## Result

- Candidate diff SHA256: `d064745b1f31bcf49508217d868f0d68186a95d80ede13981d9ae228b5fb71b8`
- Result:
  [`learn_claude_code_qa_dev_v031_snake_case_candidate.json`](../demo/results/learn_claude_code_qa_dev_v031_snake_case_candidate.json)
- Result SHA256: `92076a5d8aa006cd627f4e948ddf91ec7675f8c57b128e1d6555e172f9edd6b2`

| Metric | Baseline | Candidate |
| --- | ---: | ---: |
| Answer accuracy | 60.0% | 90.0% |
| Source recall@3 | 100.0% | 100.0% |
| Citation grounding | 88.9% | 100.0% |
| Multi-source coverage | 100.0% | 100.0% |
| Abstention accuracy | 0.0% | 0.0% |
| Repository path isolation | 100.0% | 100.0% |

The candidate fixed `check_permission`, `run_todo_write`, and the two-source `agent_loop`
cases. The unsupported vector-database question still produced an answer, so the candidate
failed the abstention gate. Production code was reverted and confirmation was not run.
