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

The repository tests verify the structural and incremental contracts with a
deterministic fake provider. The 20 manually labelled questions are frozen in
`demo/evaluation/code_module_narrative_development.json`. No real local model
or human fact-coverage run has been executed yet, so the `80%` target is not
reported as achieved.
