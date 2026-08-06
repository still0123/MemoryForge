# MemoryForge v0.2.8 Local Fact Morphology Report

## Decision

`ACCEPTED`

Candidate `e72b41454b9ca620437b3a687ae9552930e1aa7d` combines two bounded
English fact-selection behaviors:

1. direct fact specificity followed by existing candidate page order;
2. small, symmetric inflection forms used only inside already selected
   non-CodeWiki pages.

Question terms used by page routing are unchanged. Citation quotes remain exact
source text.

## Results

| Suite | Role | Page recall@3 | Selected-source recall | Grounded answer | Citation grounding |
| --- | --- | ---: | ---: | ---: | ---: |
| Watchfiles baseline | Development | 100.0% | 70.0% | 20.0% | 100.0% |
| Watchfiles candidate | Development | 100.0% | 100.0% | 80.0% | 100.0% |
| Structlog | Confirmation | 100.0% | 100.0% | 66.7% | 100.0% |
| Griffe | Regression | not rerun | 83.3% | 66.7% | 100.0% |
| Typer | Regression | not rerun | 58.3% | 8.3% | 100.0% |
| AgentSkill-Eval | Regression | not rerun | 96.2% | 96.7% | 100.0% |
| Uvicorn | Regression | 100.0% | not rerun | not rerun | not rerun |

Every page-level run kept the three-page budget. Watchfiles page candidates
were byte-identical before and after the candidate.

The previously failing Griffe abstention now returns a grounded Citation from
the expected source. Its overall grounded answer accuracy stays at the v0.2.7
candidate level while Citation grounding rises from 91.7% to 100.0%.

## Scope

Local morphology is enabled only when:

- the question is English;
- the page is one of the already selected candidates;
- the page is not a CodeWiki page.

A single overlap may admit a fact only when morphology adds evidence beyond
exact matching. Exact one-term overlap remains insufficient. Chinese and
CodeWiki ranking remain unchanged.

## Reproducibility

- Structlog end-to-end replay SHA256:
  `bf758227f42952c55f39a4fdec33b94f205dcb72a2f51c3b1bb4044081f0e1fb`
- Structlog page replay SHA256:
  `60de6e8d8f574c4b5cd4319be2a170a77cd80970c81f43169368b9fdfdf0207c`
- Watchfiles page candidates SHA256:
  `05cfe73ec9b6e5b33e6180ae477d1697f2c57dc5527824e5416cc3bd411dc2ab`

Evidence:

- `demo/results/watchfiles_local_morphology_baseline_v028.json`
  - SHA256:
    `7c19a08a8b95e41e2fa9a09e8c8a4dd0eded05aae1eb6aa7d2fdd7f1cc71bc55`
- `demo/results/watchfiles_local_morphology_development_v028.json`
  - SHA256:
    `40bf9664f6bf25513a8b39affe04a125d93a7bdfa79dddde554afa657c68aa4f`
- `demo/results/structlog_local_morphology_confirm_v028.json`
- `demo/results/griffe_local_morphology_regression_v028.json`
- `demo/results/agent_skill_eval_local_morphology_regression_v028.json`
- `demo/results/typer_local_morphology_regression_v028.json`
- `demo/results/uvicorn_local_morphology_regression_v028.json`
