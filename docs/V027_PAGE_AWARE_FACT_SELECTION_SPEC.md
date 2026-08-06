# MemoryForge v0.2.7 Page-Aware Fact Selection SPEC

## Goal

Improve grounded answer accuracy when the expected source is already inside the
three-page candidate budget, without changing page routing.

## Data

- Development: frozen Textual 12-question suite.
- Confirmation: frozen Griffe 12-question suite.
- Ruff, attrs, Rich, Typer, Uvicorn, Click, MkDocs, and AgentSkill-Eval are
  consumed and may only be used for regression.
- Pydantic was rejected before query because its source triggered the existing
  secret scanner.
- HTTP Core was rejected during compile preflight because multiline Markdown
  list continuations were not represented in single compiled facts.

## Allowed Change

Pass the existing candidate page rank into the deterministic fact ranker.
Compare direct fact overlap first, then page rank, before using the existing
summary fallback.

No page route, page-budget increase, fact parser, model call, vector database,
dependency, cache, schema, source filter, or configuration.

## Gates

Development:

- Textual page source recall@3 is at least 75%;
- selected-Citation source recall remains at least 90%;
- grounded answer accuracy improves by at least 30 points;
- Citation grounding remains 100%;
- average pages read remains at most 3;
- page candidates remain byte-identical.

Confirmation:

- Griffe page source recall@3 is at least 75%;
- selected-Citation source recall is at least 60%;
- grounded answer accuracy is at least 50%;
- Citation grounding is 100%;
- average pages read remains at most 3;
- deterministic replay is byte-identical.

Regression:

- AgentSkill-Eval grounded answer accuracy remains 96.7% and source recall
  remains 96.2%;
- Typer grounded answer accuracy remains 0.0% and source recall does not fall
  below 58.3%;
- Uvicorn page source recall does not fall below 90.0%;
- Chinese and Code Wiki query tests remain green.

Griffe confirmation may run once after the candidate commit is frozen.
Confirmation failures must not guide another candidate.
