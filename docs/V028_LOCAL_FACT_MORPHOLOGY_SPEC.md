# MemoryForge v0.2.8 Local Fact Morphology SPEC

## Goal

Recover English facts whose wording differs from the question only by a simple
inflection, after the three candidate pages have already been selected.

## Data

- Development: frozen Watchfiles 10-question suite.
- Confirmation: frozen Structlog 12-question suite.
- Griffe and all earlier suites are consumed and may only be used for
  regression.

## Allowed Change

For English questions on non-CodeWiki pages:

1. compare small, deterministic term-form sets while admitting and ranking
   facts;
2. preserve original question terms in scores;
3. use the v0.2.7 English page-aware specificity order.

No question expansion before routing, page-route change, page-budget increase,
fact parser, model call, dependency, cache, schema, source filter, or
configuration.

Citation quotes remain exact source text.

## Gates

Development:

- Watchfiles page source recall@3 is at least 80%;
- selected-Citation source recall is at least 70%;
- grounded answer accuracy improves by at least 30 points;
- Citation grounding remains 100%;
- average pages read remains at most 3;
- page candidates remain byte-identical.

Confirmation:

- Structlog page source recall@3 is at least 75%;
- selected-Citation source recall is at least 60%;
- grounded answer accuracy is at least 50%;
- Citation grounding is 100%;
- average pages read remains at most 3;
- deterministic replay is byte-identical.

Regression:

- Griffe Citation grounding returns to 100% without lowering its 66.7%
  candidate grounded answer accuracy;
- AgentSkill-Eval remains 96.7% grounded answer accuracy and 96.2% source
  recall;
- Typer source recall does not fall below 58.3%;
- Uvicorn page source recall does not fall below 90%;
- Chinese and Code Wiki tests remain green.

Structlog confirmation may run once after the candidate commit is frozen.
Confirmation failures must not guide another candidate.
