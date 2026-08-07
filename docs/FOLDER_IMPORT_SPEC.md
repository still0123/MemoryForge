# Recursive Folder Import Specification

## Status

`ACCEPTED_DEVELOPMENT_AND_LOCAL_REGRESSION`

## Base

- MemoryForge Commit:
  `3b622488a15897a20abb98099e46cd943b1f3807`
- Package target: `0.3.0`
- Runtime: Python 3.11
- New runtime dependencies: none

## Frozen Inputs

- Development suite:
  `demo/evaluation/folder_import_development.json`
- Development SHA256:
  `d818b64079f9fb4136aa53180f7f0f6f49a433b7f658ee5f083f5da6362abbec`
- Development test:
  `tests/test_folder_import.py`
- Development test SHA256:
  `b66dc3c314ff7933fc3916b24b8b276acba5b4f143f67fba12ab5534e9dd2fd0`
- Development cases: 5
- Confirmation suite:
  `demo/evaluation/folder_import_confirmation.json`
- Confirmation SHA256:
  `099d8a49892e9f0b2e6203891bbeeb6ccac53042be2e86fea110180c426cf45d`
- Confirmation cases: 3
- Confirmation status: `not_run`

The development test and both suite files are frozen before production code
changes. Confirmation must not run during development.

## Baseline Result

- Evidence:
  `demo/results/folder_import_baseline_rejected.json`
- MemoryForge Commit:
  `9242478a7108e001bf6fafbd5567a01718f51716`
- Evidence SHA256:
  `6dee68b6723d483fc308a6f09dbc5a1e06e58b7d5793eca92ec5ab08c53481ec`
- Result: `REJECTED`
- Development pass rate: 0.0%
- Failed cases: 5
- Deterministic replay: passed
- Confirmation status: `not_run`

All five cases fail because the preregistered connector, command, result
models, and folder membership schema do not exist at the baseline Commit.

## Candidate 1 Result

- Evidence:
  `demo/results/folder_import_development_candidate_1.json`
- MemoryForge Commit:
  `3990df2bd69988793629bcb6985cf013049c90ab`
- Evidence SHA256:
  `9fe364c14008054f670c8ca5db6cf4489f582367db1cc55a51d7e66afb6eb435`
- Result: `DEVELOPMENT_PASS_SUPERSEDED`
- Development pass rate: 100.0%
- Failed cases: 0
- Deterministic evaluation SHA256:
  `cfb38cb602d52af5f46f9ebb3dd820e5cfcc91ca425ccf3583903c45bed2c788`
- Confirmation status: `not_run`

Candidate 1 proved the connector contract but did not serialize the multi-step
folder write and deletion-reconciliation sequence with the Workspace lock.

## Candidate 2 Result

- Evidence:
  `demo/results/folder_import_development_candidate_2.json`
- MemoryForge Commit:
  `3d056c9d71a4caf6a625449ac3057f74ff98148c`
- Evidence SHA256:
  `d6a2c7ee8d9d74f75eea88b276db09c9dd2846e143702a3e30ee0ca858f2780d`
- Result: `DEVELOPMENT_PASS_REGRESSION_PENDING`
- Development pass rate: 100.0%
- Failed cases: 0
- Deterministic evaluation SHA256:
  `cfb38cb602d52af5f46f9ebb3dd820e5cfcc91ca425ccf3583903c45bed2c788`
- Confirmation status: `not_run`

Candidate 2 preserves Candidate 1 output while serializing folder membership
writes and deletion reconciliation under the existing Workspace lock.

## Candidate 2 Full Local Gate

- Gate Commit:
  `63e34ec0b22c6aee7e7a17426b984ffb205b4188`
- Acceptance Evidence:
  `demo/results/folder_import_candidate_2_local_gate.json`
- Acceptance Evidence SHA256:
  `e4fa0230d4d84d4a428dc95e6732e0e0e3ce6c6823884274a70bc65b761f8997`
- Ruff check and format: passed
- Strict Mypy: passed
- Registry validation: passed
- Dependency check: passed
- Pytest: 492 passed
- Coverage: 89%
- Wheel clean-room: passed
- sdist clean-room: passed
- `pip check`: passed
- CLI version smoke: passed
- Confirmation status: `not_run`

Candidate 2 is accepted for development and local regression. Confirmation
remains closed until the release-candidate confirmation gate is explicitly
opened.

## Goal

Import a local folder as one deterministic snapshot of Markdown, TXT, and
browser-saved HTML files. Preserve every file as an independent immutable
SourceVersion while tracking folder membership for updates and deletions.

## Command

```text
memoryforge folder-import <folder> --workspace <workspace>
```

Local-only sensitivity is the default. `--public` is an explicit opt-in.

## Discovery Contract

1. the folder root must be an existing real directory, not a symbolic link;
2. the managed MemoryForge workspace must be outside the imported root;
3. traversal is recursive, sorted, and does not follow symbolic links;
4. hidden files and hidden directories are excluded;
5. supported suffixes are `.md`, `.markdown`, `.txt`, `.html`, and `.htm`;
6. unsupported files are ignored without being opened;
7. root-relative POSIX paths are the only returned or persisted file paths;
8. `.memoryforgeignore` is read from the imported root and uses the existing
   safe Phase 1 subset;
9. every ignore rule is validated before any source mutation;
10. an empty snapshot is valid and can deactivate previously imported files.

## Import Contract

1. all candidate files pass secure-open, UTF-8, size, and secret validation
   before the first source is stored;
2. Markdown and TXT keep their validated text;
3. saved HTML is converted locally through the existing readable-HTML parser;
4. no browser state, network request, model, or API key is used;
5. file identity reuses the existing local identity:
   `SHA256("local:" + root_sha256 + ":" + relative_path)`;
6. a folder ID is an opaque SHA256 of the canonical root;
7. user tags are normalized and augmented with `folder` and
   `folder-path:<relative-directory>`;
8. absolute root paths are not stored in SQLite, manifests, result payloads,
   generated Wiki pages, or logs.

## Lifecycle Contract

1. first observation returns `created`;
2. same bytes and metadata return `unchanged` without a new SourceVersion;
3. changed bytes or metadata return `updated` with one new SourceVersion;
4. each current SourceVersion is bound to its folder ID and relative path;
5. a previously bound file absent from a completed snapshot becomes
   non-current;
6. an ignored file is treated as absent from the folder snapshot;
7. deletion preserves historical SourceVersions and immutable blobs;
8. source search, pending compilation, lint, and Citation replay use only the
   current SourceVersion;
9. failed preflight does not import, update, or deactivate any source.

## Result Contract

The JSON result contains:

- `folder_id`;
- `created`;
- `updated`;
- `unchanged`;
- `deleted`;
- sorted document entries with `source_id`, `relative_path`, and `status`.

It does not contain the absolute folder root or workspace path.

## Frozen Development Cases

The development suite will freeze these cases before production code changes:

1. recursive Markdown/TXT/HTML discovery, ignore behavior, and folder-path
   context;
2. duplicate import idempotency;
3. update and deletion lifecycle;
4. privacy preflight with no partial writes;
5. exact Citation replay through the normal review/approve/apply flow.

## Frozen Confirmation Cases

Confirmation remains `not_run` during development:

1. adding an ignore rule after a successful sync deactivates the matching
   source;
2. a directory symbolic link is never traversed;
3. an HTML update retains source identity and creates one new SourceVersion.

## Development Gates

- all frozen development cases pass;
- deterministic replay yields the same case results;
- existing importer, HTML, compiler, Citation, lifecycle, lint, and security
  tests pass;
- no absolute local path appears in public payloads or manifests;
- full local Ruff, format, strict Mypy, dependency, pytest, Wheel, sdist,
  `pip check`, and CLI smoke gates pass;
- confirmation status remains `not_run`;
- GitHub Actions remains disabled.

## Research

Fixed references and design decisions are recorded in:

`docs/research/FOLDER_IMPORT.md`.

## Forbidden

- PDF or Office dependencies;
- following file or directory symbolic links;
- partial best-effort import after privacy or integrity failure;
- hidden-file import by default;
- automatic network access;
- absolute source-root persistence;
- model calls or LLM Judge;
- modifying confirmation cases during development;
- deleting failed or superseded Evidence.
