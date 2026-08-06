# MemoryForge v0.2.2 Deterministic Page Routing Report

## Decision

`REJECTED`

The English page-routing candidate passed development but failed the frozen
confirmation gate. The production change was reverted.

## Candidate

The candidate used existing INDEX and relaxed FTS rankings only:

- retrieve at most five candidates from each route;
- combine ranks with equal reciprocal-rank scores;
- return the existing three-page budget;
- apply fusion only to English questions;
- keep strict FTS and exact code routes unchanged.

No dependency, index, cache, model call, configuration, or storage schema was
added.

## Development

Click development page recall@3:

| Path | Recall | Average pages |
| --- | ---: | ---: |
| Existing route | 12.5% | 3.0 |
| Character n-gram proxy | 62.5% | 3.0 |
| INDEX + FTS rank fusion candidate | 62.5% | 3.0 |

The candidate exceeded the pre-registered 20-point development gain.

## Confirmation

HTTPX 0.28.1 was rejected before query execution because
`docs/advanced/authentication.md` triggered the existing public-source secret
scanner. The scanner was not relaxed and HTTPX did not consume confirmation.

MkDocs at `2862536793b3c67d9d83c33e0dd6d50a791928f8` then became the frozen
eligible confirmation source. Twelve answered cases were run once after
implementation freeze.

| Metric | Result | Gate |
| --- | ---: | ---: |
| Page source recall@3 | 58.3% | at least 75% |
| Average pages | 3.0 | at most 3 |
| Byte-identical replay | yes | yes |

The source-recall gate failed. No MkDocs failure was used to modify the
candidate.

## Regression

After reverting the candidate, the frozen AgentSkill-Eval suite remained:

- answer accuracy: 100.0%
- source recall@3: 96.2%
- Citation grounding: 100.0%
- multi-source coverage: 100.0%
- abstention accuracy: 100.0%

## Evidence

- Candidate implementation: `6a2031b`
- Candidate revert: `db98460`
- Confirmation execution commit: `a9dcddb`
- [`click_routing_development_v022.json`](../demo/results/click_routing_development_v022.json)
  SHA256 `e7b3e17bcd524d9c15c8fce426e78a134a122025d751ccebf01bf4d3905dc2b0`
- [`click_routing_candidate_v022.json`](../demo/results/click_routing_candidate_v022.json)
  SHA256 `e9b5c8311b145d90e5dc9e51a589a4099adf4e5237c4e31712aacfb1c8bd8b26`
- [`mkdocs_routing_confirm_v022.json`](../demo/results/mkdocs_routing_confirm_v022.json)
  SHA256 `6a54ff30299dbd26e173065564c7eb686db711885f1ab9b21507d21a60bba3b2`
- [`mkdocs_routing_page_recall_v022.json`](../demo/results/mkdocs_routing_page_recall_v022.json)
  SHA256 `afad8d09641b58deeec2765b9cf82877a3c11c63acde6610610d972bdfd55fe8`
- [`agent_skill_eval_routing_regression_v022.json`](../demo/results/agent_skill_eval_routing_regression_v022.json)
  SHA256 `493f8c9914487f197708cc27baafc2cea4757adb2814104249b680c71a8c855e`

## Next Iteration

Do not tune against the consumed MkDocs confirmation. Freeze a new development
and confirmation pair. The next hypothesis should address document-frequency
normalization before adding another rank source.
