# MemoryForge v0.2.6 English Term Normalization SPEC

## Goal

Improve English page routing when questions and source facts use different
inflections, without changing the page budget or adding semantic infrastructure.

## Data

- Development: frozen Ruff 10-question suite.
- Confirmation: frozen attrs 12-question suite.
- Rich, Typer, Uvicorn, Click, MkDocs, and AgentSkill-Eval are consumed and may
  only be used for regression.

## Allowed Change

One deterministic question-term expansion inside the existing lexical query
path.

No page-budget increase, model call, vector database, dependency, cache,
schema, source filter, or configuration.

## Gates

Development:

- Ruff page source recall@3 improves by at least 20 points;
- average pages read remains at most 3;
- deterministic replay is byte-identical.

Confirmation:

- attrs page source recall@3 is at least 75%;
- attrs grounded answer accuracy is at least 50%;
- Citation grounding is 100%;
- average pages read remains at most 3;
- deterministic replay is byte-identical.

Regression:

- AgentSkill-Eval grounded answer accuracy remains 96.7%;
- AgentSkill-Eval source recall remains 96.2%;
- Typer grounded answer accuracy remains 0.0% and source recall does not fall
  below 58.3%;
- Uvicorn page source recall does not fall below 90.0%;
- Chinese and Code Wiki query tests remain green.

The attrs confirmation may run once after the candidate commit is frozen.
Confirmation failures must not guide another candidate.
