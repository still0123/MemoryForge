# GitHub Issue and Pull Request Thread Import Specification

## Status

`ACCEPTED_DEVELOPMENT_AND_LOCAL_REGRESSION`

## Base

- MemoryForge Commit:
  `cfa4957e80df78e23cd6df7f3cdf70e243fb7799`
- Package target: `0.3.0`
- Runtime: Python 3.11
- New runtime dependencies: none

## Frozen Inputs

- Development suite:
  `demo/evaluation/github_thread_import_development.json`
- Development SHA256:
  `b4a8f3269b6c99b9a7d5c3a122621f9186c966bad9170501c49749ce2de0f4ff`
- Development test:
  `tests/test_github_thread_import.py`
- Development test SHA256:
  `70be6edefcb388a05bb59a6f604c17722d6b335ff5b75d7e54aad912d4d0228e`
- Development cases: 5
- Confirmation suite:
  `demo/evaluation/github_thread_import_confirmation.json`
- Confirmation SHA256:
  `45de8d774b8d02b0112571ceb8c3b4d43db589cb0a68dbb96579aa2327ad24b0`
- Confirmation cases: 3
- Confirmation status: `not_run`

The development test and both suite files are frozen before production code
changes. Confirmation must not run during development.

## Baseline Result

- Evidence:
  `demo/results/github_thread_import_baseline_rejected.json`
- MemoryForge Commit:
  `271ec3490aa6cd14120913e7fa93f259bd9999fa`
- Evidence SHA256:
  `028d1d71db3e92b4b183a0fa3147d6eb581bb1274679a4d2c669d3e1f46b7255`
- Result: `REJECTED`
- Development pass rate: 0.0%
- Failed cases: 5
- Deterministic replay: passed
- Confirmation status: `not_run`

All cases fail because the preregistered connector, commands, JSON contract,
and explicit deletion path do not exist at the baseline Commit.

## Candidate 1 Result

- Evidence:
  `demo/results/github_thread_import_development_candidate_1.json`
- MemoryForge Commit:
  `8be9a05da49e4da1efe742dd8406f2f4706cb3f0`
- Evidence SHA256:
  `0b3e8304467d6ee38cd826d225e6c19a69a40cf1c1ed5fd6f89884920fc84ee5`
- Result: `DEVELOPMENT_PASS_SUPERSEDED`
- Development pass rate: 100.0%
- Failed cases: 0
- Deterministic evaluation SHA256:
  `486e1e81b633257de4f8f13f1c0d8b35079efbe97ad7d544d0c6250c03e13729`
- Confirmation status: `not_run`

Candidate 1 allowed a network snapshot and saved JSON to exceed the existing
5 MiB offline import boundary, so MemoryForge could create a replay artifact
that its own offline path rejected.

## Candidate 2 Result

- Evidence:
  `demo/results/github_thread_import_development_candidate_2.json`
- MemoryForge Commit:
  `fc1504488cc7735c2bfbf03f030ce6de0c946ddb`
- Evidence SHA256:
  `51963aab72b7462454544d4379ad46f44d9ae6c76924e68ffde9c155dfdce1fc`
- Result: `DEVELOPMENT_PASS_SUPERSEDED`
- Development pass rate: 100.0%
- Failed cases: 0
- Deterministic evaluation SHA256:
  `486e1e81b633257de4f8f13f1c0d8b35079efbe97ad7d544d0c6250c03e13729`
- Confirmation status: `not_run`

Candidate 2 applies the same 5 MiB ceiling to normalized saved JSON and
rendered Markdown before either artifact is written.

Candidate 2 still accepted a contribution whose locator belonged to the same
thread but to a different comment or review ID.

## Candidate 3 Result

- Evidence:
  `demo/results/github_thread_import_development_candidate_3.json`
- MemoryForge Commit:
  `c6f329152dac002ecead2f8d8bebcb002865aff6`
- Evidence SHA256:
  `3c32675802191dbeec6c8477e0b1abcb618b115120575abc0e6509f8dc565b2c`
- Result: `DEVELOPMENT_PASS_REGRESSION_PENDING`
- Development pass rate: 100.0%
- Failed cases: 0
- Deterministic evaluation SHA256:
  `486e1e81b633257de4f8f13f1c0d8b35079efbe97ad7d544d0c6250c03e13729`
- Confirmation status: `not_run`

Candidate 3 binds each contribution kind and ID to its exact GitHub fragment:
`issuecomment-<id>`, `pullrequestreview-<id>`, or `discussion_r<id>`.

## Candidate 3 Full Local Gate

- Gate Commit:
  `73242bc085e6a170d459b10324dabc57aed4bc50`
- Acceptance Evidence:
  `demo/results/github_thread_import_candidate_3_local_gate.json`
- Acceptance Evidence SHA256:
  `577d302250d9572b0fa295e3258d974b7f99f06b2a0f1fdbb34c1f6debb544fb`
- Ruff check without cache and format: passed
- Strict Mypy: passed
- Registry validation: passed
- Dependency check: passed
- Pytest: 506 passed
- Coverage: 88%
- Wheel clean-room: passed
- sdist clean-room: passed
- `pip check`: passed
- CLI version smoke: passed
- Confirmation status: `not_run`

Candidate 3 is accepted for development and local regression. Confirmation
remains closed until the release-candidate confirmation gate is explicitly
opened.

## Frozen Test Lint Exception

The preregistered test was formatted before
`memoryforge.github_thread_adapter` existed. Its byte identity remains frozen,
so `pyproject.toml` exempts only `I001` for that file. Ruff now declares
`memoryforge` as first-party globally, preventing future preregistered tests
from changing classification when their production module is later added.

## Goal

Import exactly one public GitHub Issue or Pull Request thread as an immutable,
auditable source. Preserve title, body, state, comments, timestamps, and
original GitHub locators. Support saved normalized JSON for offline replay.

## Commands

```text
memoryforge github-thread-import <url> --workspace <workspace>
memoryforge github-thread-import-json <path> --workspace <workspace>
memoryforge github-thread-delete <url> --workspace <workspace>
```

Network and offline imports default to public sensitivity because the accepted
resource identity is a public `github.com` URL. `--local-only` remains
available.

## URL Contract

1. only `https://github.com/<owner>/<repo>/issues/<number>` and
   `https://github.com/<owner>/<repo>/pull/<number>` are accepted;
2. credentials, fragments, query strings, extra path components, non-positive
   numbers, non-GitHub hosts, and non-HTTPS schemes are rejected;
3. owner and repository names are normalized only for validation, not silently
   reassigned;
4. one command addresses exactly one repository resource;
5. no search, repository listing, organization listing, or inferred repository
   context is allowed.

## Network Contract

1. requests target only fixed `https://api.github.com/repos/...` endpoints
   derived from the validated resource identity;
2. no API token, browser state, cookie, Git credential, or model key is read;
3. the resource response and comment pages have byte, page, and item limits;
4. pagination links must remain HTTPS on `api.github.com` and on the original
   resource endpoint;
5. redirects outside `api.github.com` are rejected;
6. HTTP, timeout, malformed JSON, pagination, and schema failures are
   fail-closed;
7. Issue import fetches Issue comments only;
8. Pull Request import fetches conversation comments, review bodies, and inline
   review comments;
9. all contributions are deduplicated by `(kind, id)` and sorted by creation
   time, kind, and ID.

## Saved JSON Contract

1. schema version is explicit and unknown fields are rejected;
2. the file contains normalized resource and contribution fields only;
3. required resource fields are kind, owner, repository, number, title, body,
   state, created time, updated time, and canonical HTML URL;
4. required contribution fields are kind, stable ID, author login, body,
   created time, updated time, and original HTML URL;
5. every contribution locator must belong to the canonical resource URL;
6. JSON output is sorted and UTF-8 encoded with a trailing newline;
7. offline input uses the existing secure local-file and secret boundary;
8. offline import performs no network request;
9. absolute JSON file paths are never persisted or returned.

## SourceVersion Contract

1. source identity is `SHA256("github-thread:" + canonical_url)`;
2. source path is
   `github/<owner>/<repo>/<issue-or-pull>-<number>.md`;
3. rendered Markdown contains canonical resource metadata, body, and all
   contributions;
4. each resource and contribution includes its original GitHub locator and
   timestamps beside the source text;
5. first import returns `created`;
6. identical normalized JSON returns `unchanged`;
7. changed normalized JSON returns `updated` with one new SourceVersion;
8. explicit delete marks the current SourceVersion non-current;
9. delete retains all historical SourceVersions, blobs, and manifests;
10. reimport after delete retains source identity and creates a new current
    SourceVersion;
11. Citation replay uses the rendered immutable snapshot exactly.

## Privacy Contract

1. no token or credential source is inspected;
2. malformed URLs cannot redirect requests to another host;
3. offline files pass filename, symlink, UTF-8, size, and secret validation;
4. secret-like bodies fail before SourceVersion mutation;
5. result payloads contain only canonical public URL, source ID, status, and
   immutable snapshot metadata;
6. no absolute local path enters SQLite, manifests, Wiki pages, or CLI JSON.

## Frozen Development Cases

The development suite will freeze these cases before production code changes:

1. exact Issue and Pull Request endpoint selection with chronological
   contribution rendering;
2. saved-JSON offline replay and duplicate-import idempotency;
3. update, explicit delete, and reimport lifecycle;
4. URL/redirect/secret privacy boundaries with no partial writes;
5. exact Citation replay including canonical GitHub locator text.

## Frozen Confirmation Cases

Confirmation remains `not_run` during development:

1. more than one comment page is fetched without crossing endpoint scope;
2. Pull Request review bodies and inline comments are deduplicated and ordered;
3. a saved JSON update with the same URL retains source identity.

## Development Gates

- all frozen development cases pass;
- both clean development runs are deterministic;
- existing importer, web, compiler, lifecycle, privacy, Citation, and registry
  tests pass;
- full local Ruff, format, strict Mypy, dependency, pytest, Wheel, sdist,
  `pip check`, and CLI smoke gates pass;
- confirmation remains `not_run`;
- GitHub Actions remains disabled.

## Research

Fixed references and decisions are recorded in:

`docs/research/GITHUB_THREAD_IMPORT.md`.

## Forbidden

- repository-wide, organization-wide, user-wide, or search endpoints;
- authenticated private GitHub content;
- Git credentials, cookies, browser profiles, or token files;
- GraphQL, SDKs, requests, or new runtime dependencies;
- dropping comments because only the first page was read;
- trusting arbitrary pagination or redirect URLs;
- absolute local path persistence;
- model calls or LLM Judge;
- confirmation execution during development;
- deleting failed or superseded Evidence.
