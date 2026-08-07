# Recursive Folder Import Research

## Scope

This note records fixed-Commit references for the v0.3.0 recursive folder
connector. The connector imports a bounded set of local text formats while
preserving relative paths and the existing SourceVersion lifecycle.

## LlamaIndex

- Repository: `run-llama/llama_index`
- Commit: `5c0e64eee28ae0f55601cdf29ae3cc03380fea75`
- License: MIT
- Relevant code:
  `llama-index-core/llama_index/core/readers/file/base.py`
- Relevant tests:
  `llama-index-core/tests/readers/file/test_base.py`

`SimpleDirectoryReader` separates deterministic file discovery from loading.
It supports recursive traversal, hidden-file exclusion, required extensions,
explicit exclusions, empty-directory handling, and sorted output. Its tests
cover recursion, exclusion, file limits, missing inputs, and error policy.

MemoryForge adopts:

- enumerate files before importing them;
- sort discovered paths deterministically;
- filter by an explicit extension allowlist;
- exclude hidden paths by default;
- reject an invalid root before reading files;
- test recursion, exclusion, duplicate import, and read failures separately.

MemoryForge does not adopt:

- fsspec or remote filesystem abstraction;
- pluggable extractors or automatic binary formats;
- multiprocessing, async loading, or progress-bar dependencies;
- ignore-on-error behavior for privacy or integrity failures;
- PDF, Office, image, audio, or video readers.

## MkDocs

- Repository: `mkdocs/mkdocs`
- Commit: `2862536793b3c67d9d83c33e0dd6d50a791928f8`
- License: BSD-2-Clause
- Relevant code: `mkdocs/structure/files.py`
- Relevant tests: `mkdocs/tests/structure/file_tests.py`

MkDocs walks a documentation root, sorts directories and filenames, stores a
POSIX relative `src_uri`, and applies ignore-style exclusions to that relative
identity. Its tests verify stable ordering, relative paths, hidden-file
exclusion, and recursive file collection.

MemoryForge adopts:

- POSIX relative paths as the portable source and classification identity;
- sorted directory and filename traversal;
- ignore matching against the root-relative path;
- hidden-file and hidden-directory exclusion;
- path metadata that remains independent of the machine's absolute root.

MemoryForge does not adopt:

- `followlinks=True`;
- site destination paths, navigation state, or README/index conflict rules;
- the `pathspec` dependency;
- static asset copying;
- theme and plugin abstractions.

## Local Design

The existing local importer already provides:

- no-follow secure file opens beneath an allowed root;
- `.memoryforgeignore` matching;
- Markdown/TXT validation;
- UTF-8 and size limits;
- high-confidence secret rejection;
- stable source IDs derived from source root and relative path;
- immutable blobs, SourceVersions, and manifests.

The existing saved-HTML adapter already converts readable HTML to Markdown
without a network request. The folder connector reuses these boundaries and
adds only deterministic discovery, folder membership tracking, deletion
reconciliation, and a small batch result.

Folder identity is an opaque SHA256 of the canonical root. Absolute roots are
never returned or stored. Each imported SourceVersion records its POSIX
relative path. A `folder-path:<relative-directory>` tag supplies directory
classification context to the existing compiler.

## Expected Improvement

- recursive Markdown/TXT/saved-HTML import: pass;
- duplicate sync creates no SourceVersion: pass;
- modified file creates one new SourceVersion: pass;
- deleted or newly ignored file becomes non-current: pass;
- privacy preflight prevents partial writes: pass;
- Citation replay remains exact after folder import: pass;
- absolute local paths persisted or returned: zero;
- new runtime dependencies and paid services: zero.
