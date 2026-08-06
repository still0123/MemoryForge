# MemoryForge v0.2.7 Page-Aware Fact Selection Report

## Decision

`REJECTED`

Candidate `891668b339d603f44a16c7cd600cfd69e080d0a0` ranked English facts by
direct overlap, candidate page rank, and then the existing summary fallback.
It was reverted by `8fe4630` after one confirmation gate failed.

## Dataset Preflight

- Pydantic was rejected before query because its source triggered the existing
  high-confidence secret scanner.
- HTTP Core was rejected during compile preflight because multiline list
  continuations did not form complete individual facts.
- Textual development and Griffe confirmation were checked to ensure every
  required answer was expressible by one compiled fact before evaluation.

## Results

| Suite | Role | Page recall@3 | Selected-source recall | Grounded answer | Citation grounding |
| --- | --- | ---: | ---: | ---: | ---: |
| Textual baseline | Development | 91.7% | 91.7% | 8.3% | 100.0% |
| Textual candidate | Development | 91.7% | 91.7% | 66.7% | 100.0% |
| Griffe | Confirmation | 91.7% | 75.0% | 66.7% | 91.7% |
| Typer | Regression | not rerun | 75.0% | 8.3% | 100.0% |
| AgentSkill-Eval | Regression | not rerun | 96.2% | 96.7% | 100.0% |
| Uvicorn | Regression | 100.0% | not rerun | not rerun | not rerun |

All runs kept the three-page budget. Textual page candidates were
byte-identical before and after the candidate.

The candidate passed all Textual development gates. On Griffe it passed page
recall, selected-source recall, and grounded-answer gates, but failed the
pre-registered 100.0% Citation grounding gate. The `griffe-loader-cache` case
returned `unknown`, producing 91.7% Citation grounding.

Typer improved relative to its strict v0.2.5 baseline, and the other consumed
regression suites did not regress. The confirmation failure was not used to
tune another candidate.

## Interpretation

Page-aware specificity fixes the main summary-selection failure and
generalizes strongly across two repositories. The remaining Griffe failure is
an abstention caused by insufficient direct fact overlap, not an ungrounded
quote. It still violates the frozen confirmation contract, so the candidate
cannot be promoted.

Production query behavior remains unchanged after the revert.

## Reproducibility

- Griffe end-to-end replay SHA256:
  `af715f45b55e5b08772e9950eb49b25aec24a520dba0c9be987a7931db205ff6`
- Griffe page replay SHA256:
  `415d378177b96e5d9c87d9bfaf0c2b16d2b939c144eead904adb4a815c14ccba`
- Textual page candidates SHA256:
  `d93a1dbca54f3526e108e03b4a31b008806efcac1dc21be0d3df749674695e56`

Evidence:

- `demo/results/textual_page_aware_baseline_v027.json`
- `demo/results/textual_page_aware_development_v027.json`
- `demo/results/textual_page_candidates_v027.json`
- `demo/results/griffe_page_aware_confirm_v027.json`
- `demo/results/griffe_page_candidates_v027.json`
- `demo/results/agent_skill_eval_page_aware_regression_v027.json`
- `demo/results/typer_page_aware_regression_v027.json`
- `demo/results/uvicorn_page_aware_regression_v027.json`
