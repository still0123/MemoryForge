# MemoryForge v0.2.3 Document-Frequency Normalization Report

## Decision

`ACCEPTED`

Binary query-term inverse document frequency improved page routing on a new
development repository and passed a separately frozen confirmation repository.

## Method

For each non-code Wiki page:

1. extract deterministic query/page terms;
2. count how many pages contain each term;
3. score a page by the sum of inverse document frequency for shared query terms;
4. place the top pages before broad INDEX/FTS fallbacks.

The route applies only to English questions when the existing compiled-Wiki
route is active. Exact code routes, strict FTS, and Chinese routing remain
unchanged.

No dependency, vector database, model call, cache, schema, or configuration was
added.

## Results

| Dataset | Existing page recall@3 | Production candidate | DF scorer |
| --- | ---: | ---: | ---: |
| Uvicorn development | 70.0% | 100.0% | 100.0% |
| Typer confirmation | not used for tuning | 83.3% | 91.7% |

Both datasets kept an average three-page budget. The Typer confirmation
Evidence reproduced byte-for-byte from a second empty workspace.

End-to-end answer accuracy remains outside this iteration. Typer answer
accuracy was 16.7% and selected-Citation source recall was 58.3%, confirming
that fact selection and composition remain separate work.

## Regression

AgentSkill-Eval remained:

- answer accuracy: 100.0%
- source recall@3: 96.2%
- Citation grounding: 100.0%
- multi-source coverage: 100.0%
- abstention accuracy: 100.0%

## Evidence

- Candidate commit: `868fa7d`
- Uvicorn suite SHA256:
  `3cdb4e1efe7aa88c9d4561acecfda45900ccd3c7113b7024fc6567e717e12f05`
- Typer suite SHA256:
  `d6104cbb32c64678612ff376bdfa34d2813b17036dff4cdedaf3a340df5aade4`
- [`uvicorn_df_page_recall_v023.json`](../demo/results/uvicorn_df_page_recall_v023.json)
  SHA256 `6e951ba837a89f355b20d55f0ecfe134b4ed5f03148dd8eef303eb0d1e9adfb6`
- [`typer_df_page_recall_v023.json`](../demo/results/typer_df_page_recall_v023.json)
  SHA256 `56a84181f9ea87b313f04dc1cf85c99afc8d78cb4ad5b4ba5dc0b4261d0ea472`
- [`typer_df_confirm_v023.json`](../demo/results/typer_df_confirm_v023.json)
  SHA256 `f32e91b81e2facef3904377be472b5bdb11084b4a55d5138b6093348955e6b7f`
- [`agent_skill_eval_df_regression_v023.json`](../demo/results/agent_skill_eval_df_regression_v023.json)
  SHA256 `9f1211f1cbfda7b04cad849e5e9455d07e3b4d4c8c865fb42918bc0da5218e45`
