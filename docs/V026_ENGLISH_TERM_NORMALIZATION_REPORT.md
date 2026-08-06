# MemoryForge v0.2.6 English Term Normalization Report

## Decision

`REJECTED`

Candidate `e6172faf178f97f5f0d5b8b986279052e56e7af5` expanded English question
terms with deterministic `s`, `es`, `ed`, and `ing` variants. It was reverted
by `6d670d7` after failing confirmation and regression gates.

## Results

| Suite | Role | Page source recall@3 | Grounded answer accuracy | Citation grounding |
| --- | --- | ---: | ---: | ---: |
| Ruff baseline | Development | 60.0% | 20.0% | 100.0% |
| Ruff candidate | Development | 90.0% | 20.0% | 100.0% |
| attrs | Confirmation | 75.0% | 16.7% | 100.0% |
| Typer | Regression | not rerun | 0.0% | 100.0% |
| AgentSkill-Eval | Regression | not rerun | 96.7% | 100.0% |
| Uvicorn | Regression | 100.0% | not rerun | not rerun |

All page-level runs kept the three-page budget.

The candidate passed the Ruff page-recall development gate and the attrs
page-recall confirmation gate. It failed the pre-registered attrs grounded
answer gate of 50.0%. Typer selected-Citation source recall also fell from
58.3% to 50.0%, so the regression gate failed.

## Interpretation

Question-only inflection expansion is sufficient to expose more correct pages,
but page availability does not guarantee that the deterministic fact selector
chooses a fact from that page. The candidate therefore does not improve the
end-to-end contract.

No attrs or Typer failure was used to tune another candidate. Production query
behavior remains unchanged after the revert.

## Reproducibility

Ruff page replay was byte-identical:

- SHA256:
  `a61d40248ac2be9e5bd46c068e3b9897b1a1a6e3027abae241d1f13f2766aa16`

attrs replay was byte-identical:

- end-to-end SHA256:
  `68a43fa4ccbf34afacad1e7c201e7854ddfad14508ac72c968e933a1537e6cd9`
- page-level SHA256:
  `7f38c9ce5eea96efec4a5af821428e5d57ab2fd64f9e0a543ec2ad13a65770e3`

Evidence:

- `demo/results/ruff_terms_page_baseline_v026.json`
- `demo/results/ruff_terms_page_development_v026.json`
- `demo/results/ruff_terms_development_v026.json`
- `demo/results/attrs_terms_page_confirm_v026.json`
- `demo/results/attrs_terms_confirm_v026.json`
- `demo/results/agent_skill_eval_terms_regression_v026.json`
- `demo/results/typer_terms_regression_v026.json`
- `demo/results/uvicorn_terms_page_regression_v026.json`
