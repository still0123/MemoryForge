# Static Showcase Research

## Scope

This note records fixed-Commit references for the v0.3.0 read-only Showcase
builder. The builder exports one MemoryForge Workspace as static files without
a server, model key, Node.js, or a new runtime dependency.

GitHub search used:

```text
static documentation generator language:Python
```

## MkDocs

- Repository: `mkdocs/mkdocs`
- Commit: `2862536793b3c67d9d83c33e0dd6d50a791928f8`
- License: BSD-2-Clause
- Relevant code:
  - `mkdocs/commands/build.py`
- Relevant tests:
  - `mkdocs/tests/build_tests.py`

MkDocs gathers files, navigation, and page context before writing output. A
normal build cleans stale output, generates relative links for nested pages,
copies only selected assets, and treats warnings as failures in strict mode.
Its tests cover relative paths, empty output, stale files, navigation order,
excluded content, and nested output.

MemoryForge adopts:

- gather and validate the complete snapshot before writing files;
- clean replacement of a previously owned output directory;
- relative, server-free navigation;
- deterministic ordering;
- explicit tests for stale and foreign output directories.

MemoryForge does not adopt:

- Markdown plugins, themes, live reload, draft modes, or a web server;
- Jinja2, Markdown, or MkDocs as a dependency;
- incremental dirty builds, because stale evidence is unacceptable in a
  public audit artifact;
- arbitrary template execution.

## pdoc

- Repository: `mitmproxy/pdoc`
- Commit: `00e18eb12ae2f06f3c245f796bfe7f6224db033a`
- License: MIT-0
- Relevant code:
  - `pdoc/__main__.py`
  - `pdoc/__init__.py`
  - `pdoc/render.py`
  - `pdoc/search.py`
- Relevant tests:
  - `test/test_main.py`
  - `test/test_search.py`

pdoc exposes one CLI output-directory switch, writes an index plus nested
static pages, autoescapes template data, and can emit a search index. Its tests
verify CLI output and graceful fallback when optional search precompilation is
unavailable.

MemoryForge adopts:

- one command that writes a complete static directory;
- an index page plus a machine-readable snapshot;
- HTML escaping at the rendering boundary;
- useful output without optional external tools.

MemoryForge does not adopt:

- runtime Python import and introspection;
- Jinja2 templates or Elasticlunr;
- Node.js search precompilation;
- CDN resources, live reload, or server fallback;
- project-wide customization hooks.

## Local Design

Command:

```text
memoryforge showcase build --workspace <workspace> --output <directory>
```

The implementation uses the Python standard library and existing MemoryForge
models.

- Open the Workspace through `Workspace.open_readonly`.
- Read SourceVersion metadata from the validated SQLite database in read-only
  mode.
- Export only public sources, public-backed Wiki pages, and public-backed
  ChangeSets by default.
- Report redacted local-only counts without exporting local titles, paths,
  quotes, diffs, or page names.
- Read archived ChangeSet receipts and candidate files only after validating
  their recorded SHA256 identities.
- Build ChangeSet diffs against their fixed base Commit.
- Accept an optional public evidence JSON file for one query trace, benchmark
  metrics, failures, and abstention cases.
- Extract the deterministic Mermaid block from a public Code Wiki page and
  render a dependency summary without a browser library.
- Write `index.html`, `showcase.json`, and an ownership marker into a temporary
  sibling directory, then replace only absent, empty, or marker-owned output.
- Never write inside the Workspace.

## Expected Improvement

- zero-key public Showcase build: pass;
- required sections present: 8/8;
- public source and SourceVersion coverage: 100%;
- local-only detail leakage: zero;
- Workspace file mutation: zero;
- foreign output deletion: zero;
- repeated owned build: pass;
- query route and Citation trace visibility: pass;
- benchmark positive, failure, and abstention visibility: pass;
- Code Wiki Mermaid visibility: pass;
- runtime dependencies, model calls, hosted services, and paid services: zero.
