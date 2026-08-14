# Multi-Client MCP Setup Guide

MemoryForge MCP server supports three AI coding clients: **Codex**, **Claude Code**, and **Gemini**.
Each client has a different installation path and capture-hook capability.

> **Default:** Capture Inbox is **disabled** by default. Opt in explicitly via the `--capture` flag when planning.
> Capture content is always `local_only` sensitivity and marked `UNVERIFIED`. Review before trusting any handoff.

---

## 1. Installation Paths Overview

| Client | 推荐 Scope | Install Mechanism | 当前对话加载历史 | SessionStart 自动注入 |
|--------|------------|-------------------|------------------|-----------------------|
| Codex | global Router | `memoryforge connect codex ...` | 支持 | 显式 `--startup-hook` |
| Claude Code CLI | user/global Router | `memoryforge connect claude ...` | 支持 | 显式 `--startup-hook` |
| Gemini | user | `gemini mcp add ...` | MCP 工具可用 | 显式 Hook |

---

## 2. Per-Client Install Commands

### 2.1 Codex（推荐全局 Router）

```bash
# 默认只安装全局 MCP Router 和按需调用 Skill
memoryforge connect codex \
    --workspace /absolute/path/to/workspace

# 允许读取 local_only 来源
memoryforge connect codex --allow-local-llm \
    --workspace /absolute/path/to/workspace

# 仅在明确需要下一任务自动注入时开启
memoryforge connect codex --startup-hook \
    --workspace /absolute/path/to/workspace
```

不带 `--startup-hook` 的推荐命令会移除该 Workspace 的旧 MemoryForge SessionStart Hook，并清理尚未
消费的 Codex Capsule；不会删除其他产品的 Hook。

### 2.2 Claude Code CLI（推荐 user scope，全局 Router）

```bash
# 一次注册全局 Router，并安装个人按需 Skill
memoryforge connect claude \
    --workspace /absolute/path/to/workspace

# 允许读取 local_only 来源（包括多数 AI 会话）
memoryforge connect claude --allow-local-llm \
    --workspace /absolute/path/to/workspace

# 仅在明确需要下一任务自动注入时开启
memoryforge connect claude --startup-hook \
    --workspace /absolute/path/to/workspace

# 验证
claude mcp list
# 进入 Claude Code 后也可运行 /mcp
```

默认连接会保持 Claude SessionStart 自动注入关闭，并清理尚未消费的 Claude Capsule；不会覆盖
`~/.claude/settings.json` 中的其他 Hook。个人 Skill 安装到
`~/.claude/skills/memoryforge-knowledge/SKILL.md`。

若只想绑定一个项目，可继续使用 dry-run adapter 生成 project-scoped 命令：

```bash
python -m memoryforge client plan claude \
    --workspace /path/to/workspace \
    --project /path/to/project
```

也可以手工写项目根目录的 `.mcp.json`：

```json
{
  "mcpServers": {
    "memoryforge-claude": {
      "command": "/path/to/python",
      "args": [
        "-m", "memoryforge", "mcp",
        "--workspace", "/path/to/workspace",
        "--project-root", "/path/to/project",
        "--profile",   "micro"
      ]
    }
  }
}
```

Claude Code 会对项目级 `.mcp.json` 请求信任确认。个人跨项目使用优先选择 `--scope user`，避免每个仓库
重复配置。不要使用相对 Python 路径；从不同目录启动 Claude Code 时会找不到服务器。

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
列出我最近的 MemoryForge 主题
加载第 2 个主题
```

Host 会先调用 `memoryforge_episodes`，在读取时用标题确定性聚合主题，并用已编译结论匹配主题搜索。
Episode 不复制、改写或持久化原始会话，只返回主题、短摘要和 `session_refs`。用户明确选择后，再调用
`memoryforge_load_session`，把默认不超过 6000 字符的 Capsule 加入当前对话。需要精确时间线时才调用
`memoryforge_sessions`。无需切换目录、运行终端命令或重启客户端。

加载完成后的确认必须包含 Capsule 中的有用摘要（核心结论、实现路径、调用链、相关文件等），不能只
回复“已加载”。这份摘要只属于当前对话；它与 SessionStart 自动注入是两套独立机制。

`local_only` 会话只有在 MCP 连接明确允许本地内容时才会出现在列表中；所有会话内容仍标记为
`unverified_history`。

后续问题采用渐进式链路：Host 保存 Episode 的 `session_refs`，再次调用
`memoryforge_load_session(session_refs, question=...)`，仅取这些会话内与问题匹配的结论。命中时返回
`mode: focused` 和 `retrieval_scope: selected_sessions`；未命中时返回 `no_session_evidence`，Host 再用
同一问题调用 `memoryforge_context`。不会把无关会话摘要伪装成答案，也不会每轮重复加载完整会话。

### 4.1 可选：为下一次任务自动准备 Capsule

如果希望创建下一次任务时自动注入，再使用 SessionStart Hook。

#### 一次性配置 SessionStart Hook

默认连接不会安装 SessionStart Hook，也不会让新对话自动继承旧会话。Codex 用户需要显式选择：

```bash
memoryforge connect codex --startup-hook \
  --workspace /absolute/path/to/my-wiki
```

不带 `--startup-hook` 再次运行连接命令，会移除该 Workspace 的旧版 MemoryForge SessionStart Hook，
并作废尚未消费的 Codex Capsule；其他产品的 Hook 和 Claude Capsule 不受影响。

Claude Code 推荐直接使用：

```bash
memoryforge connect claude --startup-hook \
  --workspace /absolute/path/to/my-wiki
```

如果只想查看待合并配置，可生成片段：

```bash
# Claude Code
memoryforge capture hook-config --host claude \
  --workspace /absolute/path/to/my-wiki
```

命令不会改写配置文件；它会输出 `merge_into` 和 `config`。把 `config.hooks.SessionStart` 合并到：

- Codex：`~/.codex/hooks.json`
- Claude Code：`~/.claude/settings.json`

不要覆盖文件中已有的 Hook。项目级配置也可以使用各客户端支持的项目配置路径，但仅应在可信仓库
中启用。Claude Code 的 Hook 配置属于 `settings.json`，不是 MCP 所在的 `~/.claude.json`。配置完成后
新建会话；如当前 CLI 会话没有刷新配置，可退出后重新进入。Claude Code 将 Hook 返回的
`additionalContext` 放入模型上下文，通常不会把它显示成一条普通聊天消息。

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

# Claude 全局 Router
claude mcp remove --scope user memoryforge

# Claude 旧版项目连接
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
