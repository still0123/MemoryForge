<!-- memoryforge-release-claim: version=0.4.0; status=released; supported_platforms=macos; macos_passed=656; linux_status=not_rerun; windows_status=not_verified; release_verification=passed -->

<div align="center">

<img src="assets/01-memoryforge-hero.png" alt="MemoryForge：把多来源资料编译成本地技术 Wiki" width="860"/>

# MemoryForge

**把代码、文档和 AI 对话，编译成可审核、可追溯的本地技术 Wiki。**

Local-first knowledge compiler — AI 只能提案，人审核后才发布；每条结论都能回放到原文。

<a href="https://github.com/still0123/MemoryForge/releases/tag/v0.4.0"><img src="https://img.shields.io/badge/release-v0.4.0-1664FF" alt="release v0.4.0"/></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"/></a>
<img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+"/>
<img src="https://img.shields.io/badge/platform-macOS%20%C2%B7%20Windows-lightgrey" alt="macOS · Windows"/>
<img src="https://img.shields.io/badge/release%20gate-656%20tests%20passed-brightgreen" alt="release gate: 656 tests passed"/>

[快速开始](#快速开始) · [与 RAG 的区别](#与普通-rag-的区别) · [设计决策](#核心设计决策) · [在 AI 应用中使用](#在-ai-应用中使用)

**简体中文** | [English](README_EN.md)

</div>

---

## 它解决什么问题

给 AI 编程助手补上项目知识，常见做法有两种，各有代价：

- **把文档整包塞进上下文**：昂贵、混乱，而且马上过期；
- **搭一套 RAG**：切片召回的答案无法溯源，索引更新没有任何人审核。

MemoryForge 的思路是**编译**：把 Git 仓库、Markdown/TXT/HTML、飞书文档、公开网页、
GitHub Issue/PR 和 AI 对话，统一整理成一份人类可读、Git 可版本化的 Markdown Wiki。
AI 只能生成提案（ChangeSet），人审核后才真正发布；每条结论都能回放到固定版本的原文
位置。最终产物仍是普通 Markdown、Git 历史与本地 SQLite 索引。

| 能力 | 说明 |
| --- | --- |
| **更新可审核** | 生成 → review → approve → apply 四步分离，AI 不能直接修改正式 Wiki |
| **结论可追溯** | Citation 锁定 SourceVersion、原文 locator、Git Commit 与 SHA-256 |
| **上下文按需加载** | `INDEX.md` + SQLite FTS5 先定位页面，需要核验才展开原文 Evidence |
| **证据不足不猜测** | `grounded / partial / no_local_evidence` 三级证据状态 |
| **旧会话可选择性加载** | 按主题聚合历史对话，按需把指定会话上下文带入当前任务 |
| **多客户端统一接入** | Codex、Claude Code、Gemini 共用同一个本地 MCP Server |
| **本地优先** | Git、飞书、代码和会话来源默认 `local_only`，资料留在本机 |

## 与普通 RAG 的区别

![MemoryForge 与普通 RAG 的管线对比](assets/diagrams/vs-rag-pipeline.zh.svg)

| 普通 RAG | MemoryForge |
| --- | --- |
| 原文切片后直接召回 | 先编译为可读、可审核、可版本化的 Wiki |
| 更新直接替换索引 | 先生成 ChangeSet，再 `review → approve → apply` |
| 主题相关就尝试回答 | 证据状态不足时保留边界，不补全项目事实 |
| 很难重放一次结论 | Citation 固定到来源版本、locator、Commit 和 SHA-256 |

## 核心设计决策

这一节记录几个关键取舍，也是理解本项目的最短路径。

**为什么 `approve` 和 `apply` 是两个步骤？**
`approve` 只记录一次与提案哈希绑定的授权，`apply` 才真正写入 Wiki 并创建 Git
Commit。审阅、授权、落盘因此可以分别审计与重放；出问题时 revert 对应 Commit
即可，授权记录不会被一起抹掉。自动更新也不能跳过这条链路。

**为什么 Citation 要锁 SHA-256？**
路径会移动、链接会失效、文档会改版，内容哈希不会变。Citation 同时固定
SourceVersion、原文 locator、Commit 与 SHA-256，所以任何历史结论都能在本地重放
"当时读的是哪个版本的哪段原文"。

**为什么用 SQLite FTS5，而不是向量数据库？**
Wiki 页面是编译产物，不是原文切片：数量少、标题和结构语义强，全文检索足以定位
页面，结果完全可解释，且零外部服务依赖。向量数据库在非目标清单里（见文末）。

**为什么要有 `evidence_status` 三级？**
强制模型把"有本地证据的结论"和"通用猜测"分开说：`grounded` 必须附带 Citation；
`partial` 只陈述已证实部分并标出边界；`no_local_evidence` 明确表示 Wiki 没有该项目
事实，不允许伪装成项目结论。

## 快速开始

需要 Python 3.11+。v0.4.0 的正式验证范围为 macOS。

```bash
git clone https://github.com/still0123/MemoryForge.git
cd MemoryForge

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install .

memoryforge init ./my-wiki
memoryforge start --workspace ./my-wiki
```

> 如果 PyPI 安装 `python -m pip install memoryforge-wiki` 失败（镜像源同步问题或包未发布），请使用源码安装方式。

Portal 只绑定 `127.0.0.1`。在浏览器中按"添加来源 → 后台任务 → 知识更新 → 批准并应用"
完成首份资料入库，再从首页搜索或提问。最小 CLI 流程：

```bash
memoryforge import /absolute/path/to/design.md --workspace ./my-wiki
memoryforge ingest --pending --workspace ./my-wiki
memoryforge review <changeset-id> --workspace ./my-wiki
memoryforge approve <changeset-id> --workspace ./my-wiki
memoryforge apply <changeset-id> --workspace ./my-wiki
memoryforge ask '这个项目为什么这样设计？' --workspace ./my-wiki
```

重新编译所有当前 AI 会话（包括已应用版本）仍只会生成待审核 ChangeSet：

```bash
memoryforge recompile conversations --workspace ./my-wiki
memoryforge recompile conversations --workspace ./my-wiki --trae --allow-local-llm
```

`--trae` 固定使用 `gpt-5.6-sol__max` 与 `xhigh` 推理强度；它会把本地会话发送给
该模型，因此必须显式授权。两种模式都只生成 `PROPOSED` ChangeSet，不会自动发布。

## 工作原理

### 发布知识

SourceAdapter 保存不可变来源版本，WikiCompiler 只生成待审核 ChangeSet。`review`
查看 Diff、引用和影响范围；`approve` 记录独立授权；`apply` 才写入 Wiki、更新索引并
创建可回滚的 Git Commit。自动更新也不能跳过这条链路。

![MemoryForge 知识发布链：来源快照、提案、审核、授权与发布](assets/04-memoryforge-publish-pipeline.png)

真实审核界面——一次 ChangeSet 汇总五个来源（网页、两次 AI 会话、两份笔记），`approve` 前不会写入正式 Wiki：

![知识更新审核页：五个来源的提案，批准并应用](assets/usage/08-portal-review.png)

### 查询知识

查询先通过 `INDEX.md` 和 SQLite FTS5 找到少量相关页面，再读取 Citation；只有需要
核验时，才展开对应的原文 Evidence——无需把整个 Wiki 或全部会话塞进每个新上下文。

![渐进式查询：先找页面，再读引用，最后按需核验原文](assets/usage/03-progressive-recall.png)

AI 会话也会被编译成带结构的知识页（概览 / 结论要点 / 适用边界），并标注"未验证会话记忆"，重要结论仍以当前代码和正式证据为准：

![AI 会话编译页：设计讨论被整理为可检索的 wiki 页面](assets/usage/10-portal-conversation.png)

MCP 调用成功统一返回 `status: ok`，项目证据另由 `evidence_status` 表示：

| `evidence_status` | 含义 |
| --- | --- |
| `grounded` | 本地证据足以支持项目结论，并返回 Citation |
| `partial` | 只陈述已支持部分，同时标明未证实边界 |
| `no_local_evidence` | Wiki 未建立该项目事实，可给通用分析但不能伪装成项目结论 |

当你在 AI 应用中提问时，MemoryForge 不会把整个 Wiki 灌入上下文，而是执行一条渐进式、
有边界的查询链：先找页面 → 再读摘要 → 必要时才展开原文 → 最后由 AI 基于返回的引用
合成回答。

![AI Host 查询回答流程：从提问到带引用答案的完整链路](assets/usage/04-mcp-query-answer-flow.png)

## 使用方式

| 入口 | 适合场景 |
| --- | --- |
| **macOS / Windows 桌面端** | 日常浏览、添加来源和审核更新 |
| **本地 Portal** | 在浏览器中完成完整流程 |
| **CLI** | 批处理、自动更新和诊断 |
| **Codex / Claude Code / Gemini MCP** | 在对话中按需读取已审核知识和历史会话 |

![MemoryForge 桌面端：多来源本地知识库首页](assets/07-memoryforge-desktop-workflow.png)

<sub>macOS 桌面端真实运行截图（dogfooding：知识库内容来自 MemoryForge 仓库本身、两次 AI 设计/排查会话、网页与笔记）。</sub>

本地 Portal 提供同一套界面的浏览器版本：

![本地 Portal 首页：hero 数据徽章、指标卡与项目卡片](assets/usage/07-portal-home.png)

<sub>Portal 只绑定 `127.0.0.1`，资料默认留在本机。</sub>

macOS 桌面端是日常使用的推荐方式：

```bash
python -m pip install ".[desktop]"
memoryforge-desktop
```

Windows 需要在 Windows 10/11 主机上构建，产物是单文件 `dist\MemoryForge.exe`：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
.\.venv\Scripts\memoryforge.exe init .\my-wiki
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_app.ps1
.\dist\MemoryForge.exe
```

> 正式 Windows 发布支持仍需原生 Windows 门禁验证；详情见[桌面端指南](docs/DESKTOP_APP_CN.md)。

## 在 AI 应用中使用

只需为一个 Workspace 注册一次全局 MCP Router，Codex、Claude Code、Gemini 都可以通过
同一本地服务器访问已审核 Wiki。当前打开的项目目录仅作为页面排序提示，不是访问边界。

**1. 连接一次：**

```bash
memoryforge connect codex --workspace /absolute/path/to/my-wiki   # Codex（推荐）
memoryforge connect claude --workspace /absolute/path/to/my-wiki  # Claude Code
claude mcp list                                                    # 验证

python -m memoryforge client plan gemini \                         # Gemini
    --workspace /absolute/path/to/my-wiki \
    --project /absolute/path/to/your-project
# 按输出指引执行 gemini mcp add
```

连接命令只注册一个本地 MCP Server 和一个小型调用规则 Skill，不会为每个仓库重复注册，
也不会改写项目的 `AGENTS.md` 或 `.mcp.json`。默认只向 AI 暴露 `public` 来源；读取
`local_only` 内容（私有代码、飞书私有文档、会话记录）必须在连接时显式授权
`--allow-local-llm`。重启客户端后用 `/mcp`（Codex/Claude）确认 `memoryforge` 已出现。

**2. 直接提问，不需要粘贴 Wiki：**

```text
这个项目为什么把 approve 和 apply 分开？
缓存策略最近为什么修改过？
先查 MemoryForge，再说明这个模块的设计约束。
```

AI 会在问题涉及项目历史、设计决策或既有文档时调用 `memoryforge_context` 读取少量相关
Wiki 页面；需要核验原文细节时，再通过 `memoryforge_read_evidence` 展开对应 locator 的
原文片段，不会扫描整个原始资料库。

**3. 加载指定旧会话：** `memoryforge_episodes` 按主题聚合近期会话，只返回主题、短摘要
和原始会话引用；选中后用 `memoryforge_load_session` 把有字符上限的 Episode Capsule
加入当前对话——不会在每次新任务自动注入旧历史。重要结论仍需通过当前代码、测试或正式
Wiki 证据确认。

**4. 可选，为下一次任务自动准备上下文：** 启用 SessionStart Hook 后，在目标目录运行
`memoryforge continue` 选择要延续的会话主题，再创建新任务。Capsule 是未验证的历史
提示，不会自动写入正式 Wiki。

```bash
memoryforge connect codex --startup-hook --workspace /absolute/path/to/my-wiki
# Claude Code 同理：memoryforge connect claude --startup-hook ...
```

**5. 按证据回答的实际效果：**

```text
你：先查 MemoryForge，再告诉我为什么 approve 和 apply 要分开。

AI：approve 只记录与提案哈希绑定的授权，apply 才写入 Wiki 并创建 Git Commit。
    因此审阅、授权和落盘可以独立审计与重放。

来源：Wiki 页面 · SourceVersion · 原文 locator · Commit SHA
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [中文使用指南](docs/USER_GUIDE_CN.md) | 安装、导入、问答与 AI Host MCP 接入 |
| [多客户端接入指南](docs/MULTI_CLIENT_SETUP.md) | Codex / Claude Code / Gemini 连接与 Hook 配置 |
| [桌面端指南](docs/DESKTOP_APP_CN.md) | macOS / Windows 原生窗口、Workspace 选择与打包方式 |
| [SPEC.md](SPEC.md) | 架构、数据模型和安全边界 |
| [公开演示](docs/PORTFOLIO_DEMO.md) | Demo 命令、展示顺序和常见追问 |
| [公开 Benchmark](docs/BENCHMARK.md) | 方法、结果、负案例和复现 |
| [研究归档](docs/research/README.md) | Candidate、负结果和历史 Evidence |
| [CHANGELOG](CHANGELOG.md) | 版本范围和验证状态 |

## 非目标

MemoryForge 面向个人开发者、技术负责人和长期维护多个仓库的人，不做公网 SaaS、群聊、
多用户权限、向量数据库、知识图谱、通用编码 Agent 或多 Agent 编排。完整 CLI 运行
`memoryforge --help`。

## 许可证

[MIT](LICENSE)

<!-- memoryforge-release-claim: version=0.3.0; status=released; supported_platforms=macos+linux; active_candidate=19; platform_gate_candidate=19; platform_gate_status=accepted; review_status=accepted; macos_passed=642; linux_passed=639; linux_skipped=3; windows_status=not_verified; release_verification=passed -->
