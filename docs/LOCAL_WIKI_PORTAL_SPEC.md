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

## Dynamic read-only service

For large Workspaces where one static `index.html` plus `showcase.json` is too
slow, keep the static Showcase unchanged and run a loopback-only dynamic view:

```bash
memoryforge showcase serve --workspace <workspace> --port 8765
```

Open `http://127.0.0.1:8765/`. The service binds only to `127.0.0.1`, uses the
Python standard library HTTP server, reads the Workspace read-only, and never
caches private page bodies to disk.

HTTP contract:

- `GET /` returns a small Chinese HTML shell with no external assets.
- `GET /api/summary` returns current Commit, page count, and source counts.
- `GET /api/pages?q=&offset=&limit=` returns page paths/titles. `limit` defaults
  to 50 and has a hard cap of 100.
- `GET /api/page?path=<page_path>` returns one Wiki page's content and server
  rendered Markdown HTML.

Page paths must stay below `wiki/pages/`. Traversal, absolute paths, backslashes,
and symlink components are rejected before any Git read. Existing Showcase
Markdown rendering is reused; no page snapshot, frontend framework, template
engine, or new dependency is introduced.

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
8. The dynamic service exposes summary, paginated/search page list, and lazy
   page content without mutating the Workspace or writing private page bodies
   to disk.
