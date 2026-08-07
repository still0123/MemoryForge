# GitHub Issue and Pull Request Thread Import Research

## Scope

This note records fixed-Commit references for the v0.3.0 public GitHub thread
connector. The connector imports exactly one public Issue or Pull Request and
its comments, with a saved-JSON path for offline replay.

## GitHub CLI

- Repository: `cli/cli`
- Commit: `83c6321b8faba2ec6202af70b1cc0e2ed936495e`
- License: MIT
- Relevant code:
  - `pkg/cmd/issue/view/view.go`
  - `pkg/cmd/issue/view/http.go`
  - `pkg/cmd/pr/shared/comments.go`
- Relevant tests:
  - `pkg/cmd/issue/view/view_test.go`
  - `pkg/cmd/issue/view/fixtures/issueView_previewFullComments.json`
  - `pkg/cmd/pr/view/fixtures/prViewPreviewFullComments.json`

GitHub CLI accepts one number or URL, resolves one repository resource, loads
all comment pages, retains title/body/state/time fields, and sorts Issue
comments and Pull Request reviews chronologically before rendering.

MemoryForge adopts:

- exact single-resource URL parsing;
- explicit Issue versus Pull Request identity;
- complete bounded pagination rather than a one-page preview;
- chronological comment ordering;
- empty-body handling;
- original GitHub URLs and timestamps beside each rendered contribution.

MemoryForge does not adopt:

- GraphQL, browser opening, terminal rendering, or repository context lookup;
- authenticated account state;
- project, milestone, reaction, check, or organization expansion;
- model-generated summaries;
- implicit repository-wide fetching.

## github-to-sqlite

- Repository: `dogsheep/github-to-sqlite`
- Commit: `d1f85b31d499e012ae9eeffdb4c9dbb6be834a39`
- License: Apache-2.0
- Relevant code:
  - `github_to_sqlite/cli.py`
  - `github_to_sqlite/utils.py`
- Relevant tests and fixtures:
  - `tests/test_issues.py`
  - `tests/test_issue_comments.py`
  - `tests/issues.json`
  - `tests/issue-comments.json`
  - `tests/pull_requests.json`

The project supports selecting explicit Issue or Pull Request IDs, stores
stable IDs, distinguishes Issues from Pull Requests, retains body and
created/updated timestamps, preserves comment `html_url` locators, and can
load saved JSON instead of fetching the API again.

MemoryForge adopts:

- a stable, strict saved-JSON contract for offline replay;
- explicit type, owner, repository, number, state, body, timestamps, and URL;
- stable comment IDs and original `html_url` locators;
- deterministic update identity based on the canonical public thread URL;
- fixture-driven tests for Issue, Pull Request, and comments.

MemoryForge does not adopt:

- SQLite table-per-GitHub-object storage;
- requests, sqlite-utils, Click, or any new dependency;
- auth files or token loading;
- organization, search, all-Issues, or all-Pull-Requests modes;
- labels, milestones, reactions, assets, users, or repository mirroring.

## Local Design

The connector uses the Python standard library and existing MemoryForge
SourceVersion pipeline.

- Input URLs must match exactly:
  `https://github.com/<owner>/<repo>/issues/<number>` or
  `https://github.com/<owner>/<repo>/pull/<number>`.
- Network requests target only exact `api.github.com/repos/...` endpoints.
- Issue and Pull Request comments are bounded, paginated, and sorted.
- Pull Requests additionally include review bodies and inline review comments.
- Saved JSON contains only the normalized public fields required for replay.
- Offline import applies the existing secure-open, size, UTF-8, ignore, and
  secret checks.
- Rendering preserves the resource URL and every contribution locator beside
  its text.
- Explicit delete deactivates the current SourceVersion while retaining
  immutable history.

## Expected Improvement

- single public Issue import: pass;
- single public Pull Request import: pass;
- saved-JSON offline replay: byte-deterministic;
- duplicate import creates no SourceVersion: pass;
- updated thread creates one new SourceVersion: pass;
- explicit delete makes the source non-current: pass;
- privacy boundary and secret rejection: pass;
- Citation replay retains original GitHub locator text: pass;
- repository-wide or organization-wide endpoints: zero;
- runtime dependencies, model calls, and paid services: zero.
