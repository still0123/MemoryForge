# MemoryForge v0.3.0 External Validity & Interview-Ready Goal

## Status

`IN_PROGRESS`

This document owns v0.3.0 work. `NEXT_PHASE_SPEC.md` remains historical and
must not receive additional phases.

## Frozen Baseline

- Main baseline before this Goal: `fefa1d7f261684be001df42671fa30b8b865d10e`
- PR #24 head: `e5c5ce8710c27a89b55c6e9c7a4c5122db54ae7f`
- PR #24 merge Commit: `3bc0c93198975b59b2ddfc5472b65ff2a9301d2b`
- Public source: `shareAI-lab/learn-claude-code`
- Public source Commit: `7b564c3ee6996039cb4e13a53024dfe2d4388d35`
- GitHub Actions: disabled

PR #24 is baseline-only. Its production `src/` diff is empty. Exact replay
confirmed:

- the 10-case development Evidence is byte-identical at SHA256
  `65ae9959e64b67e45bdc2a4572548af4bffe98026fbb511687f108d32a2b099e`;
- the structural replay is identical after excluding the expected
  `memoryforge_commit` field;
- structural labels pass at 100%;
- QA remains a negative result: Answer 60.0%, Source Recall@3 100.0%,
  Citation Grounding 88.9%, and Abstention 0.0%.

All existing negative results are immutable evidence. They must not be deleted,
rewritten, relabeled, or hidden.

## Version Contract

Three version concepts are independent:

1. `package_version`: installable MemoryForge release. This Goal targets
   `0.3.0`.
2. `suite_id` and `suite_revision`: stable benchmark identity and positive
   integer label revision. They do not contain a package version.
3. `evidence_revision`: positive integer for a generated result under a fixed
   suite revision. Evidence also records its generating MemoryForge Commit.

Legacy benchmark filenames are retained because their SHA256 values are already
published. New benchmark files must use stable purpose-based names, never an
unreleased package version.

## Hard Constraints

- local-first and zero-cost;
- no paid API, hosted model, vector database, graph database, or LLM judge in
  release gates;
- no GitHub-hosted Actions;
- no private source, Workspace content, absolute private path, credential, or
  prompt upload;
- no question-, repository-, or expected-answer-specific production branch;
- default query budget remains at most three Wiki pages;
- development and confirmation freeze before production tuning;
- confirmation runs once; holdout runs once after release-candidate freeze;
- one focused PR per phase;
- every new module starts with two fixed-Commit GitHub references documented
  under `docs/research/`.

## Phase 1: Registry And Baseline Governance

Deliver:

- `demo/evaluation/registry.json`;
- deterministic registry validation;
- stable suite IDs and revisions;
- package/suite/evidence version documentation;
- fixed repository, Commit, license, source paths, split paths, expected
  metrics, and evidence SHA256 for every release suite;
- all PR #24 and prior public negative evidence retained.

Gate:

- registry has no duplicate IDs;
- every referenced local artifact exists and matches SHA256;
- every public repository Commit is 40 lowercase hex characters;
- registered QA case count is 100-140;
- default evaluator is deterministic.

## Phase 2: Unified Benchmark Semantics

Deliver four release benchmark types:

1. document Wiki QA;
2. Code Wiki structural evaluation;
3. Code Wiki QA;
4. source update, deletion, staleness, and cross-repository isolation.

QA cases must cover:

- `single_hop`;
- `multi_source`;
- `paraphrase`;
- `unanswerable`;
- `exact_symbol`;
- `code_behavior`;
- `temporal_update`;
- `cross_repository`.

Every failed case receives one deterministic primary classification:

- `page_route_miss`;
- `fact_selection_miss`;
- `insufficient_support`;
- `citation_stale`;
- `multi_source_incomplete`;
- `repository_isolation_failure`;
- `wrong_answer`;
- `wrong_abstention`.

Report both macro metrics and per-suite metrics. Citation grounding and page
recall remain separate from Answer accuracy and fact-selection accuracy.

## Phase 3: Routing And Fact Selection

Evaluate page routing and fact selection as separate stages.

First candidate: SQLite FTS5 Wiki Fact index with:

- `page_path`;
- `repository_id`;
- `source_id`;
- `source_version`;
- `section_path`;
- exact `quote`;
- `routing_text`;
- optional Symbol and Relation metadata.

Exact Symbol questions query the existing Code Index before generic text
heuristics. Multi-source selection optimizes expected sub-question coverage,
not repeated topical similarity.

No production candidate is accepted until:

- development passes its preregistered gates;
- all historical suites stay within their frozen regression bounds;
- confirmation has not been inspected.

## Phase 4: Evidence Sufficiency

Define an explainable support score using:

- exact identifier coverage;
- core question-term coverage;
- conclusion and condition co-location;
- aligned negation;
- multi-source sub-question coverage;
- current SourceVersion grounding.

Freeze the threshold on development. Then run confirmation once.

Required outputs:

- selective accuracy;
- coverage;
- risk-coverage points;
- abstention accuracy;
- per-case support components.

Insufficient evidence returns `unknown`.

## Phase 5: Source Adapters

### Recursive Folder Import

- Markdown, TXT, and saved HTML;
- `.memoryforgeignore`;
- relative path retained as classification context;
- updates and deletions enter existing SourceVersion lifecycle.

### Public GitHub Thread Import

- one public Issue or PR URL;
- title, body, comments, timestamps, and original locators;
- saved JSON import for offline replay;
- no organization-wide or repository-wide crawl.

Each adapter needs update, duplicate import, deletion, privacy, and Citation
tests. PDF remains a stretch goal until core metrics pass.

## Phase 6: Static Showcase

Command:

```text
memoryforge showcase build --workspace <workspace> --output <directory>
```

The generated static site shows:

- source list and versions;
- Wiki tree;
- ChangeSet diff and review/approve/apply flow;
- one query route and Citation trace;
- benchmark metrics;
- failure and abstention cases;
- Code Wiki Mermaid architecture.

No full admin UI, server dependency, model key, or private-data default.

README first screen must include value, 30-60 second visual walkthrough, core
workflow, distinction from ordinary RAG, positive and negative metrics, and a
three-step public demo.

## Phase 7: Cross-Platform Delivery

- isolate Workspace locking behind a small module;
- POSIX uses `fcntl`;
- Windows uses `msvcrt` or another verified standard-library primitive;
- add `scripts/check_local.ps1`;
- run Linux full local gate;
- run native Windows CLI, Workspace, and Demo smoke tests;
- install Wheel in a clean environment;
- keep hosted Actions disabled.

## Release Candidate

Before holdout:

- all development and confirmation inputs are frozen and hashed;
- all focused PRs are merged in dependency order;
- package version is `0.3.0`;
- Ruff, formatting, strict Mypy, dependency checks, full pytest, coverage,
  Wheel/sdist, `pip check`, CLI, replay, and deterministic registry validation
  pass;
- clean-room build runs twice with matching artifacts;
- isolated Workspace refresh, review, approve, apply, lint, no-pending ingest,
  backup, and restore drills pass.

Holdout runs once after this freeze.

## Definition Of Done

v0.3.0 is complete only when:

- registry validation proves every public claim's repository, Commit, suite
  SHA256, result SHA256, and generating MemoryForge Commit;
- registered QA count is 100-140 with all eight required case types;
- routing and fact-selection metrics are distinct;
- deterministic failure classifications exist for every failed case;
- Fact FTS5, Code Index routing, coverage selection, and support score pass
  frozen confirmation gates;
- Folder and GitHub Thread adapters pass lifecycle and privacy tests;
- static Showcase builds from a public zero-key demo;
- Linux, Windows, and Wheel clean-room gates pass;
- all negative results remain visible;
- Wheel, sdist, SHA256SUMS, provenance, benchmark summary, README, CHANGELOG,
  Evidence Claims, known limits, and three-minute interview script agree;
- tag `v0.3.0` points to the verified release Commit.
