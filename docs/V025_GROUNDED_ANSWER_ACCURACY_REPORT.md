# MemoryForge v0.2.5 Grounded Answer Accuracy Report

## Decision

`ACCEPTED`

Commit `e00fb8f766f1d7186f5abe7ea8ed045e702b525b` changes answer accuracy from
text-only matching to a joint contract:

1. answer status matches;
2. required terms are present and forbidden terms are absent;
3. the selected Citation matches the frozen expected source.

Citation grounding remains a separate metric. A quote may be grounded in the
selected source while still failing the expected-source contract.

## Results

| Suite | Previous answer accuracy | Grounded answer accuracy | Source recall | Citation grounding |
| --- | ---: | ---: | ---: | ---: |
| AgentSkill-Eval | 100.0% | 96.7% | 96.2% | 100.0% |
| Typer regression | 16.7% | 0.0% | 58.3% | 100.0% |

The AgentSkill-Eval change removes one answer that cited
`docs/dashboard-mvp.md` instead of the frozen
`docs/evolution-timeline-dashboard.md`.

Both previous Typer passes were false positives under the stricter contract.
They contained required terms but cited the wrong source. No query, selector,
dataset, or source expectation changed.

## Reproducibility

The AgentSkill-Eval replay preserves the frozen source identity:

- remote: `https://github.com/ranmaoxia0123/AgentSkill-Eval.git`
- commit: `93f5dc05229da250b041850ad8deeeec886ef304`
- repository ID: `db29dfd06f0a2853c9764f1393b680d6a592b1a5771a012b6e1387fc17aac597`

Evidence:

- `demo/results/agent_skill_eval_public.json`
  - SHA256: `3f3e9a885f1634fc87e9db98803d20b9c71307f563452f1fbce06e6f4330a43e`
- `demo/results/typer_grounded_accuracy_audit_v025.json`
  - SHA256: `4d54c2d69078ff567867a8d392854a37ef91a643935206f655e59162937fb8e6`

Normalized evaluation replay was byte-identical:

- AgentSkill-Eval SHA256: `5a0a84415d5d44f2a8fb8972df143813df028d9ede27b815aefa241a8d20feb1`
- Typer SHA256: `7d6b59e91ba68529a1aa17ebb7e7fdb11b72b7d0fa119fafd96b90b9fce209f1`
