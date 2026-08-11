# Local Wiki Portal Specification

## Goal

Turn the existing static Showcase into a useful local Wiki browser. A user
builds one directory, opens `index.html`, and can inspect projects, Wiki pages,
citations, query evidence, and evaluation results without running a server.
The primary audience is the owner of the local Wiki, not a public Showcase
viewer: retrieval, recent work, and readable page context come before audit
details.

## User flow

```bash
memoryforge showcase build \
  --workspace <workspace> \
  --output <directory> \
  --include-local

open <directory>/index.html
```

When a host browser blocks large `file://` pages (for example, the Codex
in-app browser), the same output may be served locally without changing the
portal or uploading its contents:

```bash
cd <directory>
<MemoryForge-venv>/bin/python -m http.server 8765 --bind 127.0.0.1
```

Open `http://127.0.0.1:8765/index.html`; this is a local-only troubleshooting
fallback, not a deployment mode.

The existing `showcase build` command remains the only build command. The
portal is an improved rendering of the same fixed-Commit snapshot, not a new
application or deployment mode.

## Required experience

The generated page must provide:

1. an overview with source, version, Wiki page, and evaluation counts;
2. a grouped, searchable Wiki page tree (code roots and AI sessions are
   separate groups);
3. an in-page Markdown reader for each included Wiki page, including front
   matter summary and fenced code blocks;
4. progressive disclosure: page title, summary, hash, and evidence first, full
   audit detail on demand;
5. readable source titles, categories, IDs, and versions when page Evidence
   contains them;
6. the existing ChangeSet lifecycle, query trace, benchmark failures, and code
   architecture sections;
7. a personal-mode global search box with Enter and Cmd/Ctrl+K shortcuts;
8. a browser-local recent-pages list (up to eight stable page paths; no content
   leaves the browser);
9. responsive navigation usable on a laptop-sized screen.

Search is case-insensitive and matches page title, path, and page content. It
runs entirely in the browser and requires no model.

## Local-only boundary

- The portal performs no network request and loads no remote script, font,
  image, stylesheet, or analytics endpoint.
- The output remains a self-contained `index.html`, `showcase.json`, and the
  existing ownership marker.
- `local_only` pages appear only when the user explicitly passes
  `--include-local`.
- Building the portal does not mutate the Workspace.
- Page content is read from the fixed Workspace Commit used by the snapshot.
- Legacy local-only ChangeSets without complete review and approval Evidence are
  omitted from the lifecycle panel; their Wiki pages remain browsable. Public
  Showcase builds continue to reject incomplete lifecycle Evidence.

## Implementation constraints

- Reuse `memoryforge.showcase`; do not add a frontend framework, package
  manager, web server, database, template engine, or runtime dependency.
- Use semantic HTML, existing Python escaping, small inline CSS, and small
  inline JavaScript for filtering and page selection.
- Keep audit sections secondary to the personal retrieval flow: home, search,
  recent pages, and Wiki reading are the primary path.
- Preserve deterministic output for identical Workspace Commit and Evidence.
- Keep the current atomic publication and output ownership rules.

## Non-goals

- Remote hosting, authentication, multi-user access, and cloud sync.
- Editing Wiki pages in the browser.
- Live LLM chat or live re-indexing.
- Rendering every Markdown extension. Headings, paragraphs, lists, fenced code,
  inline code, and links are sufficient for the first version.
- Replacing the Feishu bot.

## Acceptance criteria

1. `showcase build --include-local` produces a portal that opens through a
   `file://` URL with no server.
2. A Wiki page can be found by title, path, or a phrase in its content.
3. Selecting a result reveals readable page content without navigation away
   from `index.html`.
4. Local-only content is absent when `--include-local` is not passed.
5. The generated HTML contains no `http://` or `https://` reference.
6. Rebuilding the same input produces byte-identical output.
7. Existing Showcase tests and one focused portal interaction contract pass.
