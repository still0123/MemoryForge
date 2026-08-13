# Global Codex MCP Router

Register MemoryForge with Codex once for one Workspace:

```bash
memoryforge connect codex --workspace /absolute/path/to/wiki-workspace
```

The command registers one server named `memoryforge`. It does not edit a
project's `AGENTS.md` and does not create one MCP configuration per project.

Each project still needs the normal MemoryForge source lifecycle first: add
the Git checkout, sync it, compile its proposed Wiki pages, then review and
apply them. Registration controls which sources exist in the Wiki; it does not
limit a question to one project.

MemoryForge always searches the whole applied Workspace. When Codex provides
one registered MCP Root, pages from that checkout are ranked first. No Root,
an unregistered Root, or multiple Roots still works: MemoryForge searches the
whole Workspace without a project preference. This supports a brand-new chat
and questions spanning several repositories.

The current project is a ranking hint, never an access-control boundary.
Source sensitivity remains the boundary: by default the global connection
exposes only `public` sources.

Passing `--allow-local-llm` explicitly authorizes `local_only` content from
the whole registered Workspace:

```bash
memoryforge connect codex --workspace /absolute/path/to/wiki-workspace \
  --allow-local-llm
```

To remove the global registration later:

```bash
codex mcp remove memoryforge
```
