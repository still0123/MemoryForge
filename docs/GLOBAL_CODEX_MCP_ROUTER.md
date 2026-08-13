# Global Codex MCP Router

Register MemoryForge with Codex once for one Workspace:

```bash
memoryforge connect codex --workspace /absolute/path/to/wiki-workspace
```

The command registers one server named `memoryforge`. It does not edit a
project's `AGENTS.md` and does not create one MCP configuration per project.

Each project still needs the normal MemoryForge source lifecycle first: add
the Git checkout, sync it, compile its proposed Wiki pages, then review and
apply them. Only registered checkouts can be queried.

When Codex provides MCP Roots, MemoryForge uses the single current registered
project automatically. For Hosts that do not provide Roots, the agent passes
its current project directory to the same global server; MemoryForge validates
that directory against the registered checkout before it reads any Wiki data.

If the Host exposes more than one registered project Root, MemoryForge returns
`active_project_ambiguous` rather than mixing their knowledge. If the current
directory was not registered, it returns `active_project_unavailable`.

By default the global connection exposes only `public` sources. Passing
`--allow-local-llm` is explicit authorization for `local_only` content from
the selected project:

```bash
memoryforge connect codex --workspace /absolute/path/to/wiki-workspace \
  --allow-local-llm
```

To remove the global registration later:

```bash
codex mcp remove memoryforge
```
