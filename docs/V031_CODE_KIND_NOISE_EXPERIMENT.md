# v0.3.1 Code Kind Noise Experiment

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

The unsupported vector-database question matches `run_bash` only through the generic `function`
kind and the repository-wide `agent` path fragment. Two weak terms satisfy the current overlap
threshold and create a false answer.

## Candidate

Remove code kind words such as `function`, `method`, `class`, and `module` from overlap only while
matching Code Wiki pages. Do not preserve snake_case identifiers or change routing, thresholds,
ranking, labels, or dependencies.

## Acceptance

The candidate is accepted only if all frozen development gates pass. Any failed gate rejects the
candidate. Confirmation must not run.

## Result

- Candidate diff SHA256: `e8a0199a758f9ddab6de1b63341288c83ab740dec069df18b854cb77c5324149`
- Result:
  [`learn_claude_code_qa_dev_v031_code_kind_noise_candidate.json`](../demo/results/learn_claude_code_qa_dev_v031_code_kind_noise_candidate.json)
- Result SHA256: `d0742a69ff07dc92b20219f088658de4b6e01f7142685689bda0c39c2a186a4c`

| Metric | Baseline | Candidate |
| --- | ---: | ---: |
| Answer accuracy | 60.0% | 70.0% |
| Source recall@3 | 100.0% | 100.0% |
| Citation grounding | 88.9% | 88.9% |
| Multi-source coverage | 100.0% | 100.0% |
| Abstention accuracy | 0.0% | 100.0% |
| Repository path isolation | 100.0% | 100.0% |

The candidate fixed the unsupported vector-database question without changing page recall. The
three snake_case selection failures remained, so the candidate failed the Answer and Citation
gates. Production code was reverted and confirmation was not run.
