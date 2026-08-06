# MemoryForge v0.2.4 Deterministic Fact Selector SPEC

## Goal

Improve answer accuracy after the correct page has been retrieved, without
changing page routing or using a model.

## Data

- Development: frozen Uvicorn 10-question suite.
- Confirmation: frozen Rich 12-question suite.
- Typer is consumed confirmation and may only be used for regression.

## Allowed Change

One deterministic ranking change inside the existing fact selector.

No new page route, model call, dependency, index, cache, schema, or
configuration.

## Gates

Development:

- Uvicorn answer accuracy gain at least 30 points;
- Citation grounding remains 100%;
- page source recall remains at least 90%.

Confirmation:

- Rich answer accuracy at least 75%;
- Citation grounding 100%;
- source recall at least 75%;
- byte-identical replay.

Regression:

- Typer answer accuracy does not fall below 16.7%;
- AgentSkill-Eval answer accuracy and Citation grounding remain 100%.

Rich failures must not guide candidate changes.
