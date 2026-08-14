# Multi-Client MCP Setup Guide

MemoryForge MCP server supports three AI coding clients: **Codex**, **Claude Code**, and **Gemini**.
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

## 4. Session Capsule：在当前对话加载旧会话

默认方式不依赖 Hook。先创建任意新对话，然后直接说：

```text
列出我最近的 MemoryForge 会话
加载第 2 个
```

Host 会先调用 `memoryforge_sessions`，只返回最近会话的标题、日期、短摘要和临时引用。用户明确选择
后，再调用 `memoryforge_load_session`，把最多 3 条、默认不超过 6000 字符的 Session Capsule 加入
当前对话。加载的是已应用会话中编译出的结论，不是完整聊天记录，也不是语义检索结果。无需切换目录、
运行终端命令或重启客户端。

`local_only` 会话只有在 MCP 连接明确允许本地内容时才会出现在列表中；所有会话内容仍标记为
`unverified_history`。

后续问题采用渐进式链路：Host 保存用户选中的 `session_refs`，再次调用
`memoryforge_load_session(session_refs, question=...)`，仅取这些会话内与问题匹配的结论。命中时返回
`mode: focused` 和 `retrieval_scope: selected_sessions`；未命中时返回 `no_session_evidence`，Host 再用
同一问题调用 `memoryforge_context`。不会把无关会话摘要伪装成答案，也不会每轮重复加载完整会话。

### 4.1 可选：为下一次任务自动准备 Capsule

如果希望创建下一次任务时自动注入，再使用 SessionStart Hook。

#### 一次性配置 SessionStart Hook

`memoryforge connect codex --workspace ...` 已自动安全合并 Codex Hook，无需额外命令。
Claude Code 或需要修复配置时，生成可合并片段：

```bash
# Claude Code
memoryforge capture hook-config --host claude \
  --workspace /absolute/path/to/my-wiki
```

命令不会改写配置文件；它会输出 `merge_into` 和 `config`。把 `config.hooks.SessionStart` 合并到：

- Codex：`~/.codex/hooks.json`
- Claude Code：`~/.claude/settings.json`

不要覆盖文件中已有的 Hook。项目级配置也可以使用各客户端支持的项目配置路径，但仅应在可信仓库
中启用。配置完成后重启客户端。

#### 准备下一次任务

```bash
cd /absolute/path/to/next-task
memoryforge continue
```

命令显示最近会话标题和摘要；输入 `1` 或 `1,3` 即可。然后以目标目录创建新 Codex 任务。
Workspace 自动取自 SessionStart Hook；Claude Code 使用 `--to claude`。目标目录可以是空目录或
未登记仓库，但必须已经存在。需要覆盖默认值时仍可传 `--project` 或 `--workspace`。

`memoryforge capture sessions/queue/startup` 保留为调试和脚本接口，普通用户无需使用。

边界：

- 同一 Host + 目录只能有一个待消费 Capsule；重新排队会替换旧的。
- 在其他目录或另一个 Host 启动不会消费它。
- Capsule 是 `UNVERIFIED` 历史提示，不会自动写入正式 Wiki，也不能替代当前代码和测试证据。
- 第一版没有 Portal 选择器、自动挑选历史或常驻置顶记忆。

---

## 5. Dry-Run vs Write-Config

| Mode              | Mutates Files? | Runs Subprocess? | Typical Use                      |
|-------------------|----------------|------------------|----------------------------------|
| **Dry-run Plan**  | ❌ No          | ❌ No            | Default; used by tests and previews |
| **Write-config**  | ✅ Yes         | ❌ No            | Write `.mcp.json` (Claude) via CLI flag |
| **CLI install**   | ❌ No*         | ✅ Yes           | Delegated to integration layer; calls `codex mcp add` etc. |

\* Client CLIs may write to their own config locations.

---

## 6. Uninstall Commands

```bash
# Codex
codex mcp remove memoryforge-codex

# Claude
claude mcp remove memoryforge-claude

# Gemini
gemini mcp remove memoryforge-gemini

```

---

## 7. Host ID Format

Each client installation is identified by a stable **host ID**:

```
<agent>:sha256(workspace + "\0" + project)[:8]
```

Examples:
- `codex:a1b2c3d4`
- `claude:ff00ff00`
- `gemini:1234abcd`

The host ID binds a running MCP server to one (workspace, project) pair and
lets the MCP layer route capture events and handoffs correctly.
