# MemoryForge v0.2.4 Deterministic Fact Selector Report

## Decision

`REJECTED`

Candidate `37460501cc1f61a0c133c789a429baf17b30b031` ranked pure-English facts by
direct query overlap and inverse document frequency before preferring the first
summary fact. It was reverted by `ee7062a` after failing confirmation and
regression gates.

## Results

| Suite | Role | Answer accuracy | Source recall | Citation grounding |
| --- | --- | ---: | ---: | ---: |
| Uvicorn | Development | 60.0% | 100.0% | 100.0% |
| Rich | Confirmation | 8.3% | 8.3% | 91.7% |
| Typer | Regression | 8.3% | 66.7% | 100.0% |
| AgentSkill-Eval | Regression | 100.0% | 96.2% | 100.0% |

The Uvicorn answer accuracy improved from 10.0% to 60.0%, exceeding the
development gain gate. Rich failed all three confirmation metric gates. Typer
also fell below its 16.7% answer-accuracy floor. AgentSkill-Eval did not
regress.

Rich evaluation replay was byte-identical. Both normalized evaluation files
had SHA256
`680934485194851c1dfe1fa518c58191b30345b2c0b23062cac246e4a619fc73`.

## Interpretation

The candidate fixed one development-specific ranking failure but did not
generalize. Rich failures were dominated by page source recall, which is
outside this fact-selector experiment. The Rich and Typer failures were not
used to tune another candidate.

Production behavior remains unchanged after the revert.

## Evidence

- `demo/results/uvicorn_fact_development_v024.json`
  - SHA256: `b90cb68c360c2fff2b2ec9f347997e3566d0b6b56f0cfbb51b0fc48d3451b69b`
- `demo/results/rich_fact_confirm_v024.json`
  - SHA256: `4ea8d765fa8ecb41bcb7297a0439b1e5fcbaf6db67fe35438c6ff453b6ac8792`
- `demo/results/typer_fact_regression_v024.json`
  - SHA256: `1f8015ddf42a3b931c64b49040de331329ce635585682918e95107c6a4d41f2f`
- `demo/results/agent_skill_eval_fact_regression_v024.json`
  - SHA256: `5b187f614b9e04b82c32fb813207759bdcb813d1c38feea84c9bf2a69aff153f`
