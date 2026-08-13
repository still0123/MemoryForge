# Multi-Client MCP Setup Guide

MemoryForge MCP server supports four AI coding clients: **Codex**, **Claude Code**, **Cursor**, and **Gemini**.
Each client has a different installation path and capture-hook capability.

> **Default:** Capture Inbox is **disabled** by default. Opt in explicitly via the `--capture` flag when planning.
> Capture content is always `local_only` sensitivity and marked `UNVERIFIED`. Review before trusting any handoff.

---

## 1. Installation Paths Overview

| Client   | Scope   | Install Mechanism         | Capture Hooks        | Notes                                    |
|----------|---------|---------------------------|----------------------|------------------------------------------|
| Codex    | project | `codex mcp add ...`       | Supported (opt-in)   | Via Codex CLI; requires hook trust review |
| Claude   | project | `claude mcp add ...` / `.mcp.json` | Supported (opt-in) | Project-level `.mcp.json` fallback      |
| Gemini   | user    | `gemini mcp add ...`      | Supported (opt-in)   | Defaults to user-level config           |
| Cursor   | user    | Manual `mcpServers` JSON  | **Not supported**    | Read-only MCP; manual JSON paste        |

---

## 2. Per-Client Install Commands

### 2.1 Codex (project scope, supports capture)

```bash
# Dry-run plan (no filesystem changes)
python -m memoryforge client plan codex \
    --workspace /path/to/workspace \
    --project   /path/to/project

# With capture hooks (opt-in)
python -m memoryforge client plan codex --capture \
    --workspace /path/to/workspace \
    --project   /path/to/project

# Actual CLI install (execute the command from the plan)
codex mcp add memoryforge-codex -- \
    /path/to/python -m memoryforge mcp \
    --workspace /path/to/workspace \
    --project   /path/to/project \
    --host-id   codex:<8-char-hash> \
    --profile   micro
```

### 2.2 Claude Code (project scope, supports capture)

```bash
# Dry-run plan
python -m memoryforge client plan claude [--capture] \
    --workspace /path/to/workspace \
    --project   /path/to/project

# Via CLI
claude mcp add memoryforge-claude -- \
    /path/to/python -m memoryforge mcp \
    --workspace /path/to/workspace \
    --project   /path/to/project \
    --host-id   claude:<8-char-hash> \
    --profile   micro

# Or write project .mcp.json
{
  "mcpServers": {
    "memoryforge-claude": {
      "command": "/path/to/python",
      "args": [
        "-m", "memoryforge", "mcp",
        "--workspace", "/path/to/workspace",
        "--project",   "/path/to/project",
        "--host-id",   "claude:<8-char-hash>",
        "--profile",   "micro"
      ]
    }
  }
}
```

### 2.3 Gemini (user scope, supports capture)

```bash
# Dry-run plan
python -m memoryforge client plan gemini [--capture] \
    --workspace /path/to/workspace \
    --project   /path/to/project

# Via CLI
gemini mcp add memoryforge-gemini -- \
    /path/to/python -m memoryforge mcp \
    --workspace /path/to/workspace \
    --project   /path/to/project \
    --host-id   gemini:<8-char-hash> \
    --profile   micro
```

### 2.4 Cursor (user scope, manual JSON, NO capture)

Cursor does **not** support MCP hooks today. Capture is explicitly not available.
Paste the JSON below into Cursor → Settings → MCP.

```json
{
  "mcpServers": {
    "memoryforge-cursor": {
      "command": "/path/to/python",
      "args": [
        "-m", "memoryforge", "mcp",
        "--workspace", "/path/to/workspace",
        "--project",   "/path/to/project",
        "--host-id",   "cursor:<8-char-hash>",
        "--profile",   "micro"
      ],
      "readOnly": true
    }
  }
}
```

---

## 3. Hook Event Reference (Capture Clients Only)

When `--capture` is enabled, MemoryForge subscribes to these hook events:

| Hook Name           | Emitted When                                  | Capture Event Type  |
|---------------------|-----------------------------------------------|---------------------|
| `SessionStart`      | New AI session begins                         | `session_start`     |
| `UserPromptSubmit`  | User sends a new prompt / question            | `user_prompt`       |
| `PostToolUse`       | Client finishes a file edit / write tool      | `file_changed`      |
| `PreCompact`        | Before context window compaction              | `pre_compact`       |
| `SessionEnd`        | AI session terminates                         | `session_end`       |
| *(Client-internal)* | Agent records a decision / rationale          | `decision`          |
| *(Client-internal)* | Test runner reports results                   | `test_result`       |

> **Trust Review Required:** Enabling hooks grants the MemoryForge MCP server
> read access to prompt text, file diffs, and intermediate decisions from the
> host agent. Review in accordance with your org's data-handling policy before
> enabling on sensitive repositories.

---

## 4. Dry-Run vs Write-Config

| Mode              | Mutates Files? | Runs Subprocess? | Typical Use                      |
|-------------------|----------------|------------------|----------------------------------|
| **Dry-run Plan**  | ❌ No          | ❌ No            | Default; used by tests and previews |
| **Write-config**  | ✅ Yes         | ❌ No            | Write `.mcp.json` (Claude/Cursor) via CLI flag |
| **CLI install**   | ❌ No*         | ✅ Yes           | Delegated to integration layer; calls `codex mcp add` etc. |

\* Client CLIs may write to their own config locations.

---

## 5. Uninstall Commands

```bash
# Codex
codex mcp remove memoryforge-codex

# Claude
claude mcp remove memoryforge-claude

# Gemini
gemini mcp remove memoryforge-gemini

# Cursor: manually remove the memoryforge-cursor entry from MCP settings JSON.
```

---

## 6. Host ID Format

Each client installation is identified by a stable **host ID**:

```
<agent>:sha256(workspace + "\0" + project)[:8]
```

Examples:
- `codex:a1b2c3d4`
- `claude:ff00ff00`
- `cursor:deadbeef`
- `gemini:1234abcd`

The host ID binds a running MCP server to one (workspace, project) pair and
lets the MCP layer route capture events and handoffs correctly.
