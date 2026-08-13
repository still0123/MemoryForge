# MemoryForge

MemoryForge is a local-first, auditable knowledge compiler for documents and
source repositories. It preserves source versions, reviewable changes, Wiki
pages, and replayable citations while supporting deterministic offline
evaluation.

```bash
python -m pip install memoryforge-wiki
memoryforge --help
```

Project documentation, benchmark Evidence, and release status are maintained
at <https://github.com/still0123/MemoryForge>.

## AI Host integration

MemoryForge exposes a read-only MCP stdio server (`memoryforge mcp`) plus one
write tool (`memoryforge_propose_update`) that stages PROPOSED Wiki changes for
human review; the stable Wiki and Git HEAD are never touched by the model.

Only Codex has an official, verifiable CLI, so it is the only Host that is
configured automatically (spec: never maintain unverified config writers):

```bash
memoryforge connect codex /absolute/path/to/project \
  --workspace /absolute/path/to/workspace
```

This registers the server through the official `codex mcp add` CLI (never
parsing or overwriting `~/.codex/config.toml`) and installs an on-demand
knowledge block in the project's `AGENTS.md`. The Codex configuration is shared
by the ChatGPT Desktop app and the Codex CLI/IDE extension; restart the Host
and check with `/mcp` afterwards.

Every other Host gets copyable configuration only — the command never edits
Host files:

```bash
memoryforge mcp-config --project-root /absolute/path/to/project \
  --workspace /absolute/path/to/workspace --format json
```

Paste the printed `mcpServers` object into Claude Code `.mcp.json`, Claude
Desktop `claude_desktop_config.json`, or VS Code `.vscode/mcp.json`. For a
`~/.codex/config.toml` `[mcp_servers.*]` block, use
`--format toml`. The generated server name and command are identical to the
ones `connect codex` registers, so both paths interoperate.

Local-only content stays out of the model context unless the fixed server
command carries `--allow-local-llm`; the model can never switch that on at tool
call time.
