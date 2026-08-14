# MemoryForge

<!-- memoryforge-release-claim: version=0.4.0; status=released; supported_platforms=macos; macos_passed=656; linux_status=not_rerun; windows_status=not_verified; release_verification=passed -->

> 把代码、文档和 AI 对话编译成可审核、可追溯、可查询的本地技术 Wiki。

MemoryForge 是一个本地优先的知识编译器。它把分散资料整理成可读、可版本化的 Markdown
Wiki，再按需提供给人或 AI。每次更新都经过审核，有证据的答案可回到固定来源版本和原文位置。

**正式版本：[`v0.4.0`](https://github.com/still0123/MemoryForge/releases/tag/v0.4.0)**

[快速开始](#快速开始) · [在 Codex 中使用](#在-codex-中使用) ·
[中文指南](docs/USER_GUIDE_CN.md) · [60 秒演示](docs/PORTFOLIO_DEMO.md) ·
[设计文档](SPEC.md)

![MemoryForge：把多来源资料编译成本地技术 Wiki](assets/01-memoryforge-hero.png)

## 它解决什么

MemoryForge 支持本地 Git 仓库、文件夹、Markdown/TXT/HTML、飞书文档、公开网页、
GitHub Issue/PR 和 AI 对话。最终产物仍是普通 Markdown、Git 历史与本地 SQLite 索引。

- **更新可审核**：生成、审阅、授权和写入分离，AI 不能直接修改正式 Wiki。
- **结论可追溯**：Citation 固定到 SourceVersion、原文 locator、Commit 和 SHA256。
- **上下文按需加载**：先定位少量 Wiki 页面，必要时才读取对应 Evidence。
- **证据不足不猜测**：部分证据只支持部分结论；没有本地证据时不编造项目事实。
- **旧会话可选择性加载**：按主题聚合历史对话，按需把指定会话上下文带入当前任务。
- **资料默认留在本机**：Git、飞书、代码和会话来源默认标记为 `local_only`。

### 和普通 RAG 的区别

| 普通 RAG | MemoryForge |
| --- | --- |
| 原文切片后直接召回 | 先编译为可读、可审核、可版本化的 Wiki |
| 更新直接替换索引 | 先生成 ChangeSet，再 `review → approve → apply` |
| 主题相关就尝试回答 | 证据状态不足时保留边界，不补全项目事实 |
| 很难重放一次结论 | Citation 固定到来源版本、locator、Commit 和 SHA256 |

## 快速开始

需要 Python 3.11+。v0.4.0 的正式验证范围为 macOS。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install memoryforge-wiki

memoryforge init ./my-wiki
memoryforge start --workspace ./my-wiki
```

Portal 只绑定 `127.0.0.1`。在浏览器中按"添加来源 → 后台任务 → 知识更新 → 批准并应用"
完成首份资料入库，再从首页搜索或提问。

最小 CLI 流程：

```bash
memoryforge import /absolute/path/to/design.md --workspace ./my-wiki
memoryforge ingest --pending --workspace ./my-wiki
memoryforge review <changeset-id> --workspace ./my-wiki
memoryforge approve <changeset-id> --workspace ./my-wiki
memoryforge apply <changeset-id> --workspace ./my-wiki
memoryforge ask '这个项目为什么这样设计？' --workspace ./my-wiki
```

源码安装、代码仓库、飞书、网页、GitHub Thread、AI 会话和桌面端的完整步骤见
[中文使用指南](docs/USER_GUIDE_CN.md)。

## 工作原理

### 发布知识

SourceAdapter 保存不可变来源版本，WikiCompiler 只生成待审核 ChangeSet。`review` 查看 Diff、
引用和影响范围；`approve` 记录独立授权；`apply` 才写入 Wiki、更新索引并创建可回滚的 Git
Commit。自动更新也不能跳过这条链路。

![MemoryForge 知识发布链：来源快照、提案、审核、授权与发布](assets/04-memoryforge-publish-pipeline.png)

### 查询知识

查询先通过 `INDEX.md` 和 SQLite FTS5 找到少量相关页面，再读取 Citation；只有需要核验时，
才展开对应的原文 Evidence，无需把整个 Wiki 或全部会话塞进每个新上下文。

![渐进式查询：先找页面，再读引用，最后按需核验原文](assets/usage/03-progressive-recall.png)

MCP 调用成功统一返回 `status: ok`，项目证据另由 `evidence_status` 表示：

| `evidence_status` | 含义 |
| --- | --- |
| `grounded` | 本地证据足以支持项目结论，并返回 Citation |
| `partial` | 只陈述已支持部分，同时标明未证实边界 |
| `no_local_evidence` | Wiki 未建立该项目事实，可给通用分析但不能伪装成项目结论 |

## 使用方式

| 入口 | 适合场景 |
| --- | --- |
| **macOS 桌面端** | 日常浏览、添加来源和审核更新 |
| **本地 Portal** | 在浏览器中完成完整流程 |
| **CLI** | 批处理、自动更新和诊断 |
| **Codex MCP** | 在 AI 对话中按需读取已审核知识和历史会话 |

![MemoryForge 桌面端：首页与知识更新审核](assets/07-memoryforge-desktop-workflow.jpg)

<sub>真实运行截图：左侧为本地知识库首页，右侧为待审核更新，不含用户本地数据。</sub>

桌面端构建与运行见 [macOS 桌面端指南](docs/DESKTOP_APP_CN.md)。

## 在 Codex 中使用

### 1. 连接一次

对一个 Workspace 执行一次全局连接，重启 Codex 后用 `/mcp` 确认 `memoryforge` 已出现：

```bash
memoryforge connect codex --workspace /absolute/path/to/my-wiki
```

该命令注册一个本地 MCP Server，不会为每个仓库重复注册，也不会改写项目的 `AGENTS.md`。
Codex 只在需要项目历史、既有决策、Wiki 证据或历史会话时按需查询，不会预载整个知识库。

默认只向 AI Host 提供 `public` 来源；读取 `local_only` 内容必须显式授权
`--allow-local-llm`。详见 [Codex MCP Router](docs/GLOBAL_CODEX_MCP_ROUTER.md)。

### 2. 直接提问

连接后不需要先打开 MemoryForge，也不需要把 Wiki 粘贴进对话。可以在新任务中直接问：

```text
这个项目为什么把 approve 和 apply 分开？
缓存策略最近为什么修改过？
先查 MemoryForge，再说明这个模块的设计约束。
这个结论对应哪条原文和哪个版本？
```

Codex 会在问题涉及项目历史、设计决策或既有文档时调用 `memoryforge_context`，先读取少量
相关 Wiki 页面；需要核验时，再通过 `memoryforge_read_evidence` 展开对应原文。

### 3. 加载指定旧会话

需要延续之前的某次讨论时，可以让 MemoryForge 先列出最近主题：

```text
列出我最近的 MemoryForge 主题
加载第 2 个主题
```

系统会用 `memoryforge_episodes` 按主题聚合近期会话，只返回主题、短摘要和原始会话引用；
选中后再用 `memoryforge_load_session` 把有字符上限的 Episode Capsule 加入当前对话，
不会在每次新任务自动注入旧历史。重要结论仍需通过当前代码、测试或正式 Wiki 证据确认。
完整说明见 [多客户端接入指南](docs/MULTI_CLIENT_SETUP.md)。

### 4. 按证据回答

```text
你：先查 MemoryForge，再告诉我为什么 approve 和 apply 要分开。

Codex：approve 只记录与提案哈希绑定的授权，apply 才写入 Wiki 并创建 Git Commit。
       因此审阅、授权和落盘可以独立审计与重放。

来源：Wiki 页面 · SourceVersion · 原文 locator · Commit
```

当证据完整时，Codex 基于引用回答；只有部分证据时，它会保留已支持结论并标出待核验部分；
没有本地证据时，则明确区分项目事实与通用分析。

## 文档

| 文档 | 内容 |
| --- | --- |
| [中文使用指南](docs/USER_GUIDE_CN.md) | 安装、导入、问答与 AI Host MCP 接入 |
| [macOS 桌面端指南](docs/DESKTOP_APP_CN.md) | 原生窗口、Workspace 选择与打包方式 |
| [SPEC.md](SPEC.md) | 架构、数据模型和安全边界 |
| [公开演示](docs/PORTFOLIO_DEMO.md) | Demo 命令、展示顺序和常见追问 |
| [公开 Benchmark](docs/BENCHMARK.md) | 方法、结果、负案例和复现 |
| [研究归档](docs/research/README.md) | Candidate、负结果和历史 Evidence |
| [CHANGELOG](CHANGELOG.md) | 版本范围和验证状态 |

MemoryForge 面向个人开发者、技术负责人和长期维护多个仓库的人，不做公网 SaaS、群聊、
多用户权限、向量数据库、知识图谱、通用编码 Agent 或多 Agent 编排。完整 CLI 运行
`memoryforge --help`。

<!-- memoryforge-release-claim: version=0.3.0; status=released; supported_platforms=macos+linux; active_candidate=19; platform_gate_candidate=19; platform_gate_status=accepted; review_status=accepted; macos_passed=642; linux_passed=639; linux_skipped=3; windows_status=not_verified; release_verification=passed -->
