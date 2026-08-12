# Code Module Narrative Research

## Scope

This note covers the optional, cited semantic narrative added to the existing
deterministic Code Wiki compiler. The deterministic Symbol, Relation,
ModulePlan, ArchitectureGraph, Wiki path, and ChangeSet contracts remain
authoritative.

## References

### AsyncFuncAI/deepwiki-open

- Commit: `36b349d21e8275e29bb42857a14abb581c19f756`
- License: MIT
- Reviewed:
  - `api/services/codemap.py`
  - `api/schemas/codemap.py`
  - `api/prompts.py`
  - `tests/backend/services/test_wiki_task.py`

Useful design:

- require structured model output before rendering;
- give the model bounded source context with numbered locations;
- ground citations against real source after generation;
- keep an earlier structured result when a later enrichment step fails.

Not adopted:

- best-effort JSON repair and retry;
- RAG retrieval as the source of module identity;
- model-authored diagrams or code structure.

MemoryForge instead rejects malformed JSON, validates every citation index
against a fixed input list, and never lets the model change ModulePlan or
ArchitectureGraph.

### FSoft-AI4Code/CodeWiki

- Commit: `67f27e65a3b416d57775345ab9101cf8dedb722a`
- License: no repository license detected; design was read only and no code is
  copied.
- Reviewed:
  - `codewiki/src/be/documentation_generator.py`
  - `codewiki/mcp/tools/module_tree.py`
  - `codewiki/mcp/tools/analysis.py`
  - `tests/test_module_tree_validation.py`
  - `skills/codewiki-wiki-generator/SKILL.md`

Useful design:

- process modules in post-order so children exist before parent summaries;
- build parent context from direct child documentation rather than rereading
  the whole repository;
- map changed leaf components to affected modules and refresh only ancestor
  summaries;
- validate that generated module references belong to the known component set.

Not adopted:

- model-driven module clustering;
- per-leaf Agent loops;
- mutable module trees;
- full-document rewrites driven by unbounded source input;
- multi-Agent orchestration.

MemoryForge keeps its deterministic module tree and leaf pages, calls one
existing provider only for parent narratives, and emits the result through the
existing review, approve, and apply workflow.

## Acceptance targets

- cited module-purpose and key-flow question grounding: `100%`;
- manually labelled narrative fact coverage: at least `80%`;
- existing Symbol, Relation, Citation, and architecture metrics: no regression;
- one changed leaf refreshes only its narrative ancestors;
- no `--llm` run remains byte-identical to the current deterministic output.

The 20 manually labelled questions are frozen in
`demo/evaluation/code_module_narrative_development.json` and executed by
`demo/run_code_module_narrative_benchmark.py`. The controlled-provider gate
covers the full compile, review/apply, projection rebuild, and evaluation path
at 20/20 with 100% Citation grounding. Provider fallback is recorded as failed
coverage instead of aborting the evidence run.

Run a real configured provider with:

```bash
python demo/run_code_module_narrative_benchmark.py \
  --workdir /private/tmp/memoryforge-narrative-benchmark \
  --output local-evidence/code-module-narrative.json
```

On 2026-08-12, a clean-worktree run at commit `354b07ac9192d659d9e264b15fb42f7560c376d7`
used the local OpenAI-compatible `seed-2.1-turbo` provider. All three parent
modules synthesized, projection rebuild and lint passed, manually labelled fact
coverage was 90%, and Citation grounding was 100%. The two uncovered cases were
`py-purpose` and `py-normalize-flow`: their expected sources were cited, but the
generated text omitted one required evaluation term. This is a development-set
result, not a claim about unseen repositories.
