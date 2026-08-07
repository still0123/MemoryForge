# Static Showcase Specification

Status: ACCEPTED_DEVELOPMENT_BROWSER_PENDING

## Goal

Export a validated MemoryForge Workspace as a read-only, server-free,
interview-ready static artifact.

## Command

```text
memoryforge showcase build \
  --workspace <workspace> \
  --output <directory> \
  [--evidence <public-evidence.json>] \
  [--include-local]
```

`--include-local` is an explicit disclosure switch. The default build must not
export local-only source, page, ChangeSet, query, or Citation details.

## Output Contract

The output directory contains exactly:

```text
.memoryforge-showcase
index.html
showcase.json
```

The ownership marker is `memoryforge-showcase-v1\n`. A rebuild may replace an
absent, empty, or correctly marked output directory. It must reject non-empty
foreign directories and symbolic links.

`showcase.json` records:

- `schema_version`;
- fixed Workspace Commit;
- public source list and SourceVersion history;
- redacted local-only counts;
- public Wiki page tree;
- latest public ChangeSet diff and lifecycle receipts;
- one answered query route and Citation trace when public evidence is supplied;
- one deterministic unknown response;
- benchmark summary, failures, and abstention cases from supplied evidence;
- one public Code Wiki Mermaid block and parsed dependency summary.

`index.html` renders these eight sections:

1. sources and versions;
2. Wiki page tree;
3. ChangeSet diff;
4. review, approve, and apply lifecycle;
5. query routing and Citation trace;
6. benchmark metrics;
7. failures and abstentions;
8. Code Wiki Mermaid architecture.

All dynamic text is HTML-escaped. The site contains no remote script, remote
stylesheet, form, mutation control, or network request.

## Privacy Contract

Default builds:

- include details only for SourceVersions marked `public`;
- include a Wiki page only when every applied source backing it is public;
- include a ChangeSet only when every declared source is public;
- accept evidence only through an explicit path;
- never include absolute Workspace or output paths;
- never include environment variables, credentials, raw source blobs, prompts,
  sessions, or private Git checkout paths.

## Read-Only Contract

The builder:

- opens the Workspace with `Workspace.open_readonly`;
- does not migrate the database;
- does not create a Workspace lock;
- does not change Git HEAD, index, worktree, SourceVersion state, or receipts;
- writes only below the explicit output directory.

## Frozen Development Cases

`tests/test_showcase.py` and
`demo/evaluation/static_showcase_development.json` freeze:

- required static files and all eight sections;
- source/version visibility and local-only redaction;
- public ChangeSet diff and complete lifecycle;
- query, Citation, benchmark failure, abstention, and Mermaid visibility;
- Workspace byte identity before and after build;
- foreign-output and symlink rejection;
- marker-owned deterministic rebuild;
- nested CLI command and zero-key operation.

The frozen confirmation file is
`demo/evaluation/static_showcase_confirmation.json`. Its status remains
`not_run` until the release-candidate confirmation gate opens.

Revision 1 omitted the existing `code-add` setup command, so every test failed
before exercising Showcase behavior with `cannot plan modules for an empty
code index`. That negative result remains in
`demo/results/static_showcase_candidate_0_fixture_rejected.json`. Revision 2
adds only the missing setup command; assertions and expected metrics are
unchanged.

Revision 2 then used the single-file `import` command for a temporary file
outside its allowed source root. Its four setup failures remain in
`demo/results/static_showcase_candidate_1_fixture_rejected.json`. Revision 3
uses the existing local-only `folder-import` command on an isolated temporary
directory. Assertions and expected metrics remain unchanged.

Frozen revision 3 identities:

- development SHA256:
  `e3839b86256b8cb6275f4afb5ac3bf1da5a4e0adb66e18fc1c1fcc89d0721e17`;
- confirmation SHA256:
  `4c64d073a2a1af46a068219af158ed77bc1e99a1b4de6601e01634d63140166e`;
- test SHA256:
  `11ca2dd0dbe3bac7b9e8fedbda0a40655e6a76269cf48feb003ce6470ffa33f5`.

## Acceptance Threshold

- development pass rate: 100%;
- failed cases: 0;
- required sections: 8/8;
- local-only detail leakage: 0;
- Workspace mutations: 0;
- external network/model calls: 0;
- deterministic replay SHA256: identical across two builds from one fixed
  Workspace and evidence file.

No confirmation input may be executed or changed during development.

## Candidate Results

Candidate 1:

- MemoryForge Commit:
  `649f1ca35cf363f6eda9e4cd563c2ba6e29fecff`;
- Evidence:
  `demo/results/static_showcase_development_candidate_1.json`;
- result: 4/4 development cases passed;
- deterministic evaluation SHA256:
  `8aa4853a0642d91f0756ca02679995e7f85836789a28c28cf53606913b4f40ad`;
- status: superseded by exact SourceVersion privacy and receipt hardening.

Candidate 2:

- MemoryForge Commit:
  `1962f19a399ba0805a9c107fd14741fdfcb34759`;
- Evidence:
  `demo/results/static_showcase_development_candidate_2.json`;
- result: 4/4 development cases passed;
- exact SourceVersion privacy, receipt binding, no-follow reads, and
  failure-safe output replacement added.

Candidate 3:

- MemoryForge Commit:
  `c07d2d275caa4449ffc6cc20f3a709ebf4c59b0b`;
- Evidence:
  `demo/results/static_showcase_development_candidate_3.json`;
- Evidence SHA256:
  `3cb6113516a5d6b3e0b5e2c357bde6470350c0a8be0aecfaa563253135ed7da7`;
- result: 4/4 development cases passed;
- required sections: 8/8;
- local-only detail leaks: 0;
- Workspace mutations: 0;
- deterministic evaluation SHA256:
  `8aa4853a0642d91f0756ca02679995e7f85836789a28c28cf53606913b4f40ad`;
- confirmation status: `not_run`.

The zero-key public command `demo/run_showcase_demo.py` was also run from a
clean Candidate 3 worktree. Browser inspection found all eight navigation
sections, a rendered SVG architecture, no console errors, and no script or
stylesheet references in generated HTML. The browser harness injected one
failed `/@vite/client` request; that path is absent from the generated files.
