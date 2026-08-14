# MemoryForge 项目 Spec

> 把分散的项目资料编译成一套适合人和 Agent 阅读的个人技术 Wiki。

| 字段 | 内容 |
|---|---|
| 文档状态 | Implemented v1.0 |
| 当前基线 | 分支 `main` |
| 产品形态 | 单用户、本地优先、CLI |
| 当前阶段 | v0.2.0 Code Wiki 内核已完成，正在整合飞书项目上下文并验证真实仓库 |

## 1. 项目定义

MemoryForge 面向拥有多个代码仓库、设计文档和个人笔记的开发者。它将原始资料整理为长期维护的 Markdown Wiki，并让回答能够返回相关页面和原始出处。

```text
多个知识来源
  -> 统一读取
  -> 编译 Entity / Concept / Synthesis Wiki
  -> 人工查看页面 Diff
  -> 写入 Git
  -> 按需逐层检索
```

项目不做普通“文档聊天”，也不复刻完整 Claude Code。技术深度集中在一个模块：**渐进式披露的 Wiki 记忆**。

主要借鉴：

- [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 的 Raw、Wiki、Schema 三层结构；
- [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki) 的多来源摄入和可阅读 Wiki；
- 内部 `llm-wiki` 的 Concept、Entity、Synthesis 页面；
- “让 Context 流动起来”的多仓库、文档和工作记录统一沉淀思路。

## 2. 用户、来源与范围

第一版只服务一个可信的本地用户，主要处理：

- 多个 Git 仓库的 README、docs、ADR、Changelog 和用户选定的 Python、Go、TypeScript/TSX 核心模块；
- 技术方案、PRD、项目总结和事故复盘；
- Markdown/TXT 个人笔记；
- 飞书文档和用户明确指定的公开网页文章。

典型问题包括：

- 每个仓库负责什么，它们如何协作？
- 某个模块的核心流程是什么？
- 为什么选择当前设计？
- 某个结论来自哪份文档或哪段代码？

### 2.1 MVP

- LocalFile 和 GitRepository 两种来源；
- Entity、Concept、Synthesis 三类页面；
- 自动生成 `INDEX.md` 和页面摘要；
- 为每个 Git 仓库生成一个项目总览页，链接该仓库内的资料页；
- 多份来源更新同一页面；
- 更新前显示 Diff，确认后写入 Git；
- Index → 摘要 → 正文 → 原文的渐进式查询；
- FTS5 检索；
- 一个 OpenAI-compatible 模型适配器；
- 一个只使用 Wiki 公开证据的最小 Agent Loop；
- 一组可复现的公开资料评测。
- 手动刷新已登记 Git 仓库和飞书文档的 `refresh` 命令。
- 选择性导入 Git 仓库中已提交的 Python、Go、TypeScript/TSX 代码模块，并生成带代码位置引用的模块页。
- 通过 `web-import <url>` 导入单篇可静态读取的公开网页文章。
- 通过 `html-import <file> --url <url>` 将用户保存的 HTML 网页转为 Markdown 来源。

### 2.2 暂不实现

- 飞书、Notion、Obsidian 完整接入，以及网页批量采集；
- PDF/OCR、音视频和多模态；
- 完整通用编码 Agent、多 Agent 和复杂工具循环；当前 Agent 只支持 Wiki 检索和原文核验；
- Web/桌面 UI；
- Neo4j、向量数据库调优和完整知识图谱；
- RBAC、多租户、后台任务和审批流；
- 完整的冲突解决和时序数据库。

飞书放在 MVP 后，作为一个薄 Source Adapter，不进入核心编译逻辑。

## 3. 简化架构

```text
LocalFileAdapter ─┐
GitRepoAdapter ───┼─> LocalDocument ─> WikiCompiler ─> PageChange
Future Adapters ──┘                                  │
                                                    v
                                             review / apply
                                                    │
                                      Markdown Wiki + Git + FTS5
                                                    │
                                                    v
                                             Progressive Query
```

系统的稳定业务结构仍保持精简：

1. `LocalDocument`：统一表示输入；
2. `PageChange`：表示模型提出的页面更新；
3. `ChangeSet`：组织一次可审核更新。

LLM 编译额外保留一个很小的 `CompilationPlan`，只用于说明“准备创建/更新哪些页面”，
并作为 `ChangeOperation.details` 随审核记录保存；它不是新的持久化知识模型。除此之外，
不引入 `PageProjection`、Claim Mutation、独立审计事件等多层中间对象。

## 4. 知识来源

### 4.1 SourceAdapter

所有来源转换成同一种结构：

```python
class LocalDocument(BaseModel):
    id: str
    kind: Literal["local_file", "git"]
    title: str
    text: str
    uri: str
    revision: str | None = None
    metadata: dict[str, str] = {}
```

Adapter 只负责读取和标准化，不直接生成 Wiki。

### 4.2 LocalFileAdapter

- 读取用户指定目录中的 Markdown/TXT；
- 文件变化时产生新版本；
- 先用 `mtime + size` 快速判断；
- 只有可能变化时才计算一次内容哈希。

### 4.3 GitRepositoryAdapter

一个知识库可以绑定多个本地 Git 仓库。版本身份使用：

```text
repository_id + relative_path + commit_sha
```

默认读取：

- `README*`
- `docs/**`
- `adr/**`
- `CHANGELOG*`
- 用户显式配置的代码或配置文件

不把整个仓库所有代码复制进 Wiki。用户可通过 `code-add <repository-id> <relative-path>` 选择
一个已提交的 Python、Go、TypeScript/TSX 文件或目录；编译器生成确定性符号图和层级模块计划，
再通过 `ingest --code-wiki <repository-id>` 提出带引用代码页。精确代码搜索
仍交给 `rg`、LSP 或代码托管平台；Wiki 主要保存模块职责、核心流程、设计原因和跨仓关系。

#### 当前 Phase 3A 实现范围

Phase 3A1 已完成本地 Git 文档扫描：只从已有 checkout 的 `HEAD` 读取根目录
`README*`、`CHANGELOG*`、`docs/` 和 `adr/` 下的 Markdown/TXT 文件。Phase 3A2
已完成 workspace 登记和同步：

```text
git-add <checkout> -> git-list -> git-sync <repository-id>
```

`repository_id` 在有远端时来自去除用户名、凭证、query 和 fragment 后的远端身份；
没有远端时来自本地仓库根目录。同步只读取本地已有 checkout，不 clone、fetch 或访问远端，
所有导入文档默认 `local_only`。对可公开仓库，可通过
`git-add <checkout> --public` 显式授予模型访问权限；重新 `git-sync` 后，该仓库新版本
才会成为 `public`。Phase 3B 已实现来源到页面的唯一归属和多来源页面
的增量路由；Phase 4 已实现有上限的渐进式查询和来源删除后的 Wiki 生命周期维护。只读 Wiki Lint 已检查页面
Frontmatter、来源映射、可展开引用、Index 链接、孤儿页和需要清理的失效来源。每个 Git 仓库还会生成一个只做导航、
不参与问答的项目总览页。飞书第一版只提供
`feishu-import <docx-or-wiki-url-or-token>`：通过已登录的 `lark-cli` 读取一篇 Markdown
文档并按 `local_only` 来源进入现有编译链。长文档按一级标题拆成一个总览来源和多个章节来源，
继续复用现有的页面、审核和查询流程；不做后台同步、监听或权限管理。

可公开的 Git 仓库在其来源页已经 `apply` 后，可运行
`memoryforge topics <repository-id>`。模型只接收这些页面的标题、相对路径和已有摘要，
并将它们分成若干主题。结果写入同一个项目总览页，仍然由 review/apply 决定是否生效；
主题页不承担问答证据，具体问答继续使用带原文引用的来源页。

### 4.4 后续扩展

- 飞书会议纪要；
- PDF；
- 网页批量采集、登录态页面和动态渲染页面；
- 经过人工选择的聊天结论。

这些来源仍然转换为 `LocalDocument`，核心编译器不感知飞书或网页 API。

## 5. Wiki 结构

```text
workspace/
├── raw/
├── wiki/
│   ├── INDEX.md
│   └── pages/
└── .memoryforge/
```

| 页面类型 | 回答的问题 | 示例 |
|---|---|---|
| Entity | 它是什么？ | 仓库、服务、模块、协议 |
| Concept | 它如何工作？ | 流程、机制、约束、踩坑 |
| Synthesis | 为什么这样做？ | 技术选型、方案对比、复盘、跨仓总结 |

Source Summary、Index 和 Log 是系统视图，不再扩展更多知识类型。
Entity、Concept、Synthesis 是页面 Frontmatter 和 `INDEX.md` 中的分类，
不是三个物理目录；每个来源的稳定页面路径为
`wiki/pages/<source_id>.md`。

页面使用统一 Frontmatter：

```yaml
title: Cache Key Design
type: synthesis
summary: 缓存键由业务命名空间、对象 ID 和版本组成
tags: [cache, backend]
sources: [note-cache-design, repo-order-service]
source_version: 12
updated: 2026-07-29
```

正文末尾保存简单引用，定位使用字符区间：

```text
[note-cache-design:chars:12-240]
[repo-order-service@abc123:docs/cache.md:chars:8-200]
```

MVP 不单独维护一份与页面正文重复的 Claim Ledger。

## 6. Wiki 编译与审核

一次编译读取：

- 新增或变化的 `LocalDocument`；编译器以 `source_version` 判断页面是否
  已经同步；
- `INDEX.md` 中的页面摘要；
- 页面类型和写作规则。

未变化来源不会读取 Raw Blob；只有待编译的变化来源才会读取其正文。
`INDEX.md` 以已有索引条目和本次候选页面增量合并，不扫描整个 `wiki/pages/`。

模型最终生成：

```python
class Citation(BaseModel):
    source_id: str
    locator: str | None = None


class PageChange(BaseModel):
    operation: Literal["create", "update"]
    path: str
    title: str
    page_type: Literal["entity", "concept", "synthesis"]
    summary: str
    body: str
    citations: list[Citation]
```

启用 LLM 编译时，Provider 先返回一个受校验的 `CompilationPlan`，再根据该计划生成
`PageChange`。计划只允许引用当前 pending 来源和 `wiki/pages/*.md` 页面；本地代码校验
来源归属、路径和 Citation，模型不能绕过 staging。

页面路由规则：

1. 用标题、标签、别名和摘要召回候选页；
2. 同一主题优先更新已有页面；
3. 主题明显不同时才创建页面；
4. 不同页面类型通过 WikiLink 关联；
5. 多个来源可以共同更新一页。

进入审核前只做必要校验：

- 页面路径位于 `wiki/`；
- 必填字段存在且页面可解析；
- Citation 的 `source_id` 存在；
- 明确的本地行号没有越界；
- 模型不能修改 `raw/`。

语义正确性通过页面 Diff、引用和公开评测保证，不增加第二个 LLM Judge 作为强制门禁。

### 6.1 ChangeSet

```python
class ChangeSet(BaseModel):
    id: str
    source_ids: list[str]
    changes: list[PageChange]
    created_at: datetime
    status: Literal["pending", "applied", "rejected"]
```

```text
ingest -> staging -> review -> approve -> applied / rejected
```

`approve` 独立记录用户授权，`apply` 只负责写入 Wiki、重建 Index，并创建一个 Git commit。
审核与审批收据绑定不可变提案并随应用结果归档。Git 继续负责 Diff、历史和回滚。

LLM 编译通过 `memoryforge ingest --pending --llm` 显式开启。CLI 从
`MEMORYFORGE_API_BASE`、`MEMORYFORGE_API_KEY`、`MEMORYFORGE_MODEL` 读取
OpenAI-compatible Provider 配置；默认 ingest 不读取这些环境变量，也不发网络请求。
模型先提议一个简短的 `CompilationPlan`，再生成 `PageChange`：它可以把多个 pending 来源合并到一个
`wiki/pages/*.md` 页面，但不能写 Frontmatter、`INDEX.md` 或 raw。来源校验、引用、
Frontmatter、INDEX 和 ChangeSet 由本地代码生成，仍必须经过 review/approve/apply。
根目录 `AGENTS.md` 和 `.memoryforge/schema.yaml` 会作为有上限的 Workspace contract
进入编译 prompt；它们只能补充写作约束，不能替代本地校验。
LLM prompt 包含待编译来源的正文；对于已存在且相近的公开页面，只提供路径、标题、摘要和来源
ID 的小卡片。模型可以把新来源追加到其中一个候选页，但不能移动已有来源；本地代码仍验证唯一归属。
默认不上传 `local_only` 来源或其卡片。只有用户显式传 `--allow-local-llm`，才允许当前配置的可信
模型处理本地资料。

## 7. 渐进式查询

```text
L0 INDEX.md               -> 确定范围和候选页面
L1 页面摘要/Frontmatter  -> 筛选相关页面
L2 Wiki 正文             -> 回答大多数问题
L3 原始来源              -> 核实细节或提供出处
```

规则：

1. 导航问题可以停在 L0/L1；
2. 解释问题优先使用 L2；
3. 用户追问依据或 Wiki 信息不足时读取 L3；
4. 不要求每个问题都回读原文；
5. 找不到支持信息时说明知识库暂无答案。

默认输出答案、使用的 Wiki 页面和必要来源。查询先读 `INDEX.md`，并用已应用 Source 到页面的
SQLite FTS5 映射补充候选页。候选页按分数和路径
稳定排序，默认最多展开 3 页，可用 `--max-pages 1..10` 调整；原始来源仍只在
`--verify` 时读取。调试模式可以展示读取路径，但不记录冗长的 Token 级 Trace。

先使用 FTS5 和简单评分；只有评测证明召回不足，才增加 Embedding。

## 8. 适度工程原则

这是可信单用户环境下的个人/开源项目：

- 只防止路径写错、来源不存在、解析失败等直接问题；
- 哈希只用于本地文件版本识别，不进入每条 Citation；
- Git 来源直接使用 Commit SHA，不重复生成版本指纹；
- 查询和 Apply 不反复重新计算内容哈希；
- 一个概念只保存在一个主要位置；
- 不提前抽象 Repository 层、事件总线、Provider Factory 和插件框架；
- 不为极端异常增加复杂重试、锁和恢复流程；
- 无效模型输出直接报错并保留 staging，不设计自动修复链；
- 新增依赖或抽象前，先说明它解决的当前问题。

现有代码中的 Blob Hash 和 Quote Hash 可以为兼容旧数据保留，但不继续传播到新模型、页面和查询流程。

## 9. CLI

```bash
memoryforge init <workspace>
memoryforge import <path> [--category <category>]
memoryforge git-add <local-checkout> [--public]
memoryforge git-list
memoryforge code-add <repository-id> <relative-path>
memoryforge git-sync <repository-id>
memoryforge ingest --pending [--llm] [--allow-local-llm]
memoryforge ingest --code-wiki <repository-id>
memoryforge review <changeset-id>
memoryforge approve <changeset-id>
memoryforge apply <changeset-id>
memoryforge search "<query>"
memoryforge ask "<question>" [--llm] [--allow-local-llm] [--max-pages <1-10>] [--verify]
memoryforge agent "<question>" [--allow-local-llm]
memoryforge lint
memoryforge feishu-import <docx-or-wiki-url-or-token>
memoryforge feishu-reply "<question>"
memoryforge web-import <public-http-url>
memoryforge html-import <saved-page.html> --url <public-http-url>
```

不为每个内部操作设计独立命令。

## 10. 当前进展

当前已实现：

- Workspace 初始化；
- Markdown/TXT 导入和 Source 版本保存；
- SQLite FTS5 中英文搜索；
- 确定性的单来源 Entity、Concept、Synthesis 页面生成；
- `wiki/pages/` 稳定页面路径、统一 Frontmatter 和增量 `INDEX.md`；
- staging、review、approve、apply 和 Git commit；
- Git 多仓库 Adapter 和已应用来源到 Wiki 页的编译路由；
- 基于标题和摘要候选卡片的增量主题扩展、页面相关链接和关系页；
- Lint 对相关页失链、来源删除和来源更新未重编译的提示；
- Git 来源删除后的 `cleanup_required` 检查，以及单来源归档、共享页面重建的审核式 ChangeSet；
- `ARCHIVE_PAGE` 在已审批的 `apply` 中删除稳定页面并清理来源映射，`reject` 不改变稳定 Wiki；
- 一个 OpenAI-compatible LLM Provider，以及多来源页面更新；
- LLM 编译的两步“CompilationPlan → PageChange”流程；计划会随 ChangeSet operation details
  进入 review，但不成为新的知识层；
- Workspace 根目录 `AGENTS.md` 和 `.memoryforge/schema.yaml` 会进入 WikiCompiler 与 MiniClaude
  的 prompt，且有固定字符上限；
- Index → SQLite FTS5 回退 → 有上限 Wiki 正文 → 按需原文核实的渐进式问答，
  以及来源版本和定位信息；
- `refresh` 一次性同步已登记的本地 Git checkout 和飞书文档；它不 fetch 远端、
  不做后台任务，变更仍须经过 `ingest → review → approve → apply`。
- MiniClaude Agent 已要求每个最终引用先经过 `read_evidence`；默认只发送公开资料，
  可信内部模型可用 `--allow-local-llm` 明确放行本地资料。
- MiniClaude Agent 支持 `--propose-update`：只能基于本次已读取的原始证据生成一个
  `PROPOSED` ChangeSet，不直接修改稳定 Wiki，仍须经过 `review → approve → apply`。
- MiniClaude Agent 已支持明确的工具错误观察、Provider 错误终态、稳定 `call_id`、多 Citation
  读取，以及 3 页 / 6 Citation / 2,000 字符证据 / 8,000 字符工具结果预算；Payload 会记录查询成本。
- `search`、`ask` 和 `agent` 已支持 `--repository <repository-id>`；范围通过 Git 来源映射过滤，
  不指定时仍保持全局查询，本地文件来源也不会被误删。
- 提供纯函数式 Feishu 文本事件到 Wiki 回复 payload 的桥接层，以及复用 `lark-cli` 的
  本地 `feishu-serve` 常驻命令；它只监听私聊文本事件、调用既有 Wiki 问答并回复原消息，
  不引入 HTTP 服务或在项目内保存 App Secret。

已实现一个离线 `eval <suite.json>`：题集为每道题声明预期来源路径和关键事实；
它检查 Wiki 回答正确率、引用正确率、每题展开的 Wiki 页数和查询期间的原文读取数；
引用核验读取的原文片段单独统计。它还使用任意词匹配和 BM25 Top-3，记录原始资料 FTS 是否命中预期来源；
多来源题要求完整命中全部来源，避免和 MemoryForge 使用不同口径。
第一版不伪造完整 Chunk RAG，先把现有可复现的
Raw FTS 基线跑通；只有它证明需要更强语义检索时，才加入 Chunk/Embedding 基线。

## 11. 实现阶段

### Phase 1：渐进式 Wiki

- 统一页面 Frontmatter；
- 生成三类页面和 `INDEX.md`；
- 按 Index → 摘要 → 正文 → Source 查询；
- 先使用现有确定性编译器打通闭环。

验收：三份相关文档形成多类页面，页面可被发现，回答可显示来源，重复导入不产生更新。

### Phase 2：最小 LLM 编译器（已完成）

- 接入一个 OpenAI-compatible Provider；
- 模型先输出简短的 `CompilationPlan`，再生成 `PageChange`；
- 支持多来源更新同一页面；
- Review 显示清晰的 Markdown Diff。
- `AGENTS.md` 和 `.memoryforge/schema.yaml` 进入编译与 Agent prompt；

验收：模型不能绕过 staging，页面没有明显重复，引用能定位已有 Source，无效输出直接失败。

### Phase 3：多仓库来源与编译路由（已完成）

- 实现 `GitRepositoryAdapter`；
- 绑定多个本地仓库；
- 读取高价值文档和用户指定代码；
- 生成仓库 Entity 和跨仓 Synthesis。

验收：两个关联仓库形成各自页面和跨仓总结；单仓变化只影响相关页面。

### Phase 4：有上限渐进式查询（已完成）

- `INDEX.md` 优先路由；
- Index 未命中时从已应用 Source FTS5 回退到页面；
- 最多读取指定数量的 Wiki 页面，原文只在 `--verify` 时展开。

### Phase 5：评测与包装（已完成）

- 构造公开的小型多来源数据集；
- 对比 Raw FTS 基线与 MemoryForge；
- 评测回答正确率、引用正确率、页面展开数、原文读取数和查询成本；
- 完成 README、架构图和五分钟 Demo。

评测用于证明渐进式 Wiki 的价值，不追求大规模 Benchmark 和复杂消融实验。

### Phase 6：Wiki-backed MiniClaude Agent（已完成最小闭环）

- 复用现有 OpenAI-compatible Provider；
- 通过有限轮 JSON 决策实现 Agent Loop；
- 只提供 `search_wiki`、`read_evidence` 和 `final` 三个动作；
- 默认只把 `public` 来源证据发送给模型，可信内部模型可显式放行 `local_only`；
- 不执行 Shell、不修改文件、不引入 MCP、Subagent 或新依赖。
- 支持从一次已完成的证据问答提议更新一个已有 Wiki 页面；提议进入现有 staging，
  不绕过审核流程。

验收：模型能完成“搜索 Wiki → 核验原文 → 带引用回答”的闭环；超过步数或缺少引用时不生成无依据的最终答案。

### Phase 7：薄飞书机器人接入（已完成）

- 解析飞书 URL 验证和文本消息事件；
- 用既有渐进式 Wiki 查询生成标准文本回复 payload；
- `feishu-serve` 通过 `lark-cli event consume im.message.receive_v1 --as bot` 常驻监听，
  再用 `lark-cli im +messages-reply --as bot` 回复同一条消息；
- 支持 `/project` 固定当前聊天的仓库范围、`/resume` 恢复本机保存的有限会话上下文；
- `watch` 可定期刷新已登记来源并生成待审核 ChangeSet，但不会自动覆盖正式 Wiki；
- 不在项目内保存 App Secret、起 HTTP 服务或代替飞书权限管理。

验收：在飞书私聊机器人发送一条文本问题，机器人从已应用的本地 Wiki 返回答案和 Wiki 页面引用；
机器人自身消息和非文本消息不会触发回复。

## 12. 项目完成标准

五分钟 Demo：

1. 绑定两个公开 Git 仓库和一组本地文档；
2. 生成 Entity、Concept、Synthesis 页面；
3. Review 并 Apply；
4. 回答单仓与跨仓问题；
5. 从 Wiki 进一步定位原始出处；
6. 修改一份资料，只生成相关页面的小范围 Diff；
7. 展示公开题集上的可复现评测结果。

达到公开展示标准需要：

- LocalFile 和 GitRepository 两类来源可用；
- 三类页面能够持续更新；
- 多来源可以汇总到同一页面；
- 变更经过简单人工审核并进入 Git；
- 回答能够返回 Wiki 页面和原始出处；
- 有公开、可复现的评测和 Raw FTS 基线结果；
- README 明确区分已实现和计划；
- 代码没有为了“看起来完整”增加明显冗余抽象；
- 公开仓库不包含公司内部资料。

项目介绍表述：

> 设计并实现面向个人开发者的多来源知识编译系统，将本地文档和多个 Git 仓库持续整理为 Entity、Concept、Synthesis 三类 Markdown Wiki；通过渐进式披露降低 Agent 查询上下文，通过页面 Diff、Git 版本和来源定位保证更新可审阅、答案可回源，并在公开资料上以可复现题集和 Raw FTS 基线验证检索效果。

## 13. MVP 之后

- 飞书文档与会议纪要 Adapter；
- 网页批量采集、动态网页和 PDF Adapter；
- Embedding 语义召回；
- 轻量冲突提示和历史问答；
- MCP 接口；
- 查询结果转为待审核知识。

这些方向不得影响 MVP 的 Wiki 编译主线。
