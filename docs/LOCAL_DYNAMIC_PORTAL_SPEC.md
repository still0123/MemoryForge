# MemoryForge 本地动态知识门户实现 Spec

状态：PROPOSED

## 1. 背景

MemoryForge 已有两种阅读方式：

- `showcase build` 生成固定 Commit 的静态 Showcase；
- `showcase serve` 运行只绑定 `127.0.0.1` 的只读服务，按需读取页面。

静态 Showcase 适合发布证据和作品演示，不适合日常浏览大型私有 Workspace。当 Workspace
达到数千个 Wiki 页面时，若全部嵌入单个 HTML，首页体积、加载时间和目录密度都会随页面数
增长。现有动态服务已解决按需加载，但界面仍只有扁平页面列表，无法清楚回答：

1. 我收录了哪些数据源；
2. 哪些是代码仓库、飞书文档、AI 会话或普通笔记；
3. 每个项目包含哪些模块、文件、文档和会话；
4. 当前页面与其他页面有什么确定关系；
5. 哪些内容已应用，哪些仍待评审。

本 Spec 只改造本地动态服务。静态 Showcase 的输出、隐私边界和发布用途保持不变。

## 2. 目标

将 `memoryforge showcase serve` 变成供 Workspace 所有者日常使用的本地知识门户：

- 首页先展示真实数据源和项目，不先展示系统审计；
- 页面正文按知识类型展示，不直接暴露编译器内部术语；
- 代码、飞书文档和 AI 会话可通过确定性关系互相跳转；
- 搜索、筛选和页面内容全部按需读取；
- Workspace、Wiki 文件、SQLite 和 Git Commit 继续是唯一数据来源；
- 服务只读、仅本机可访问、零外部请求、零新增运行时依赖。

成功体验：用户打开首页后，三次点击内可以进入任意项目、数据源或最近页面；打开一页后能看懂
“这是什么、来自哪里、与什么有关”。

## 3. 非目标

- 不做公网部署、账户、权限系统、多用户协作或云同步。
- 不做桌面客户端；浏览器是本地服务的界面。
- 不增加 React、Vue、构建工具、Node.js 或模板引擎。
- 不增加向量库、Embedding、知识图谱数据库或 LLM 页面分类。
- 不在浏览器编辑、审核、批准、应用或删除知识。
- 不改变现有 Wiki Markdown、Raw Evidence、SourceVersion 或 Git 历史。
- 不自动推断业务因果。没有确定证据的关系不展示为事实。
- 不在本轮实现全局关系图；先做好页面级“相关知识”。

## 4. 用户入口

命令保持不变：

```bash
memoryforge showcase serve \
  --workspace <workspace> \
  --port 8765
```

启动后打印：

```text
MemoryForge local Wiki: http://127.0.0.1:8765 (Ctrl+C to stop)
```

不自动打开浏览器。用户明确提供哪个 Workspace，门户就读取哪个 Workspace；不得回退到示例
Workspace、当前项目或上次打开的目录。

## 5. 信息架构

### 5.1 一级导航

```text
首页
项目
AI 会话
飞书资料
其他知识
系统状态
```

“系统状态”包含来源版本、待评审数量、Workspace Commit 和诊断信息。日常阅读页不连续附带
ChangeSet、评测和审计长列表。

### 5.2 首页

首页按以下顺序展示：

1. 全局搜索；
2. 最近打开，最多 8 页，仅保存在浏览器 `localStorage`；
3. 项目卡片：仓库名、当前代码页数、最近同步 Commit；
4. 数据源卡片：代码仓库、飞书文档、AI 会话、其他知识；
5. 待处理提示：仅显示非零的待应用或错误数量。

禁止把历史 Source identity 总数标成“当前知识来源”。首页数字必须区分：

- 当前来源；
- 已应用页面；
- 历史版本，仅在系统状态中显示。

零值卡片默认隐藏。

### 5.3 项目页

项目页以 Git repository 为边界：

```text
项目概览
模块
代码文件
相关飞书资料
相关 AI 会话
```

项目页显示仓库名、同步 Commit、语言、页面数量、顶层模块和最近相关知识。绝对 checkout path
不进入浏览器响应。

### 5.4 左侧目录

目录必须使用用户可识别层级：

```text
demo-repo
  项目概览
  模块
    db
    pkg
    sm
  代码文件
AI 会话
飞书资料
其他知识
```

不得使用 `代码 · source · go`、原始 Source ID、页面哈希作为主目录名称。完整标识只允许在
“来源详情”折叠区出现。

## 6. 知识类型与页面模板

分类只使用现有结构化数据，按以下优先级确定：

1. tags 包含 `conversation`：`conversation`；
2. 已登记在 `feishu_documents` 或 tags 包含 `feishu`：`feishu`；
3. tags 包含 `repository` 且 `generated=repository_overview`：`project`；
4. 页面关联 `git_source_revisions`、`git_code_modules`，或 Wiki Fact 含
   `repository_id`：`code`；
5. 其余：`note`。

分类结果只影响展示，不修改底层 Wiki。

### 6.1 项目总览模板

- 面包屑：`项目 / <repository>`；
- 标题和一句话说明；
- 当前同步 Commit；
- 顶层模块；
- 代表文件；
- 相关飞书资料和 AI 会话；
- 来源详情默认折叠。

### 6.2 代码模块模板

显示顺序：

1. 中文标题和模块路径；
2. 职责；
3. 入口与主要操作；
4. 上游、下游和依赖模块；
5. 代表文件；
6. 测试；
7. 已验证符号，默认折叠；
8. 代码依据，默认折叠。

已有英文标题在展示层映射：

- `Responsibilities`：`职责`；
- `Entry points and handlers`：`入口与处理器`；
- `Module dependencies`：`依赖模块`；
- `Representative files`：`代表文件`；
- `Verified symbols`：`主要类型与方法`；
- `Sources`：`代码依据`。

不得改写或翻译代码符号、文件路径和签名。

### 6.3 代码文件模板

- 面包屑：`项目 / 模块 / 文件`；
- 文件用途摘要；
- 语言和固定 Commit；
- 主要类型、函数和方法；
- 所属模块；
- 提到该文件的 AI 会话和飞书资料；
- 原始代码引用位置默认折叠。

### 6.4 AI 会话模板

- 标题、日期、平台和 `未验证会话记忆` 状态；
- 首要结论；
- 决策；
- 修复建议或操作结果；
- 未完成事项；
- 用户问题线索，默认折叠；
- 完整引用和原始会话标识，默认折叠；
- 提到的仓库、模块、文件和相关会话。

不得把 AI 历史结论显示为已验证代码事实。状态标签始终可见。

### 6.5 飞书文档模板

- 文档标题、章节路径、最近同步时间；
- 文档目录；
- 正文；
- 所属父文档和相邻章节；
- 相关项目、代码模块和 AI 会话；
- SourceVersion 详情默认折叠。

### 6.6 普通笔记模板

- 标题、摘要、标签、更新时间；
- 正文；
- 相关页面；
- 来源详情默认折叠。

## 7. 确定性关联

门户只展示可说明来源的关系，并标记关系类型。

### 7.1 直接关系

以下关系为“直接关联”：

- `page_sources`：Wiki 页面与 Source；
- `git_source_revisions`：代码 Source 与 repository、relative path、Commit；
- `git_code_modules`：代码模块与 repository；
- Wiki Fact 的 `repository_id`：页面与项目；
- Markdown 内部链接：页面与页面；
- 飞书父文档和拆分章节关系。

### 7.2 精确提及

AI 会话或飞书页面正文出现以下完整值时，建立“精确提及”：

- 已登记代码文件的完整 relative path；
- 已登记模块的完整 module path；
- 已验证 symbol 的完整 qualified name。

匹配必须基于完整标识，不使用模糊相似度。反向关系同步展示，例如代码文件页可显示“被 3 个
AI 会话提及”。

### 7.3 同项目关系

页面共享同一 `repository_id` 时，可展示为“同项目”，但不得冒充调用、依赖或因果关系。

### 7.4 实现方式

复用现有 SQLite 表和固定 Commit Wiki 页面。服务启动后为当前 Commit 构建一个小型内存目录：

- page path 到类型、标题、摘要、项目、来源；
- repository 和 relative path 到页面；
- 页面之间的直接链接和精确提及反向索引。

启动时读取所有页面的 front matter，但只扫描 AI 会话、飞书、普通笔记和生成的项目总览正文；
代码页正文继续按需读取。内存目录不保存 Raw blob，不写磁盘。检测到 Workspace Commit 改变后
丢弃并重建。第一版允许一次线性扫描；只有真实测量证明启动不可接受时，才新增持久化索引。

## 8. HTTP API

保留现有接口并扩展字段。所有响应带 `workspace_commit`。

### 8.1 `GET /api/summary`

```json
{
  "workspace_commit": "...",
  "current_source_count": 1200,
  "applied_page_count": 1180,
  "source_identity_count": 1400,
  "projects": 3,
  "code_pages": 1100,
  "conversation_pages": 40,
  "feishu_pages": 30,
  "note_pages": 10,
  "pending_sources": 1
}
```

### 8.2 `GET /api/projects`

返回项目卡片：`repository_id`、name、短 Commit、页面数、模块数、语言。不得返回 checkout
path 或 remote credential。

### 8.3 `GET /api/sources?kind=<kind>&offset=&limit=`

`kind` 为 `code|feishu|conversation|note`。返回用户可读来源名称、同步状态、当前版本和关联
页面数。默认 50，最大 100。

### 8.4 `GET /api/pages`

```text
/api/pages?q=&kind=&project=&parent=&offset=&limit=
```

返回 `path`、title、summary、kind、project、updated、status。搜索复用现有 FTS；分类和项目
筛选使用结构化目录，不扫描 Raw blob。

### 8.5 `GET /api/page?path=<page_path>`

返回：

- path、title、summary、kind、project、updated；
- 已渲染正文；
- 面包屑；
- 页面结构；
- 来源摘要；
- related：直接关联、精确提及、同项目三组。

为兼容现有只读 API，继续返回完整 Markdown `content`；Portal 前端只消费 `html`，不把
`content` 复制到 DOM、`localStorage` 或其他浏览器状态。证据详情通过渲染后的折叠区查看。

### 8.6 路由

浏览器使用 hash 路由：

```text
#home
#projects
#project=<repository_id>
#sources=conversation
#page=<url-encoded-page-path>
#system
```

直接打开 `#page=` 必须立即进入阅读页，不先滚过首页。

## 9. 前端实现

沿用原生 HTML、CSS 和 JavaScript：

- 首屏 HTML 小于 24KB；
- 页面列表和正文按需请求；
- DOM 节点使用 `textContent`；正文只使用服务端现有安全 Markdown renderer；
- 全局搜索支持 Enter 和 Cmd/Ctrl+K；
- 搜索输入延迟 200ms，取消过期结果；
- 最近页面只保存 page path，不保存正文；
- 目录支持键盘操作和清晰焦点；
- 小屏退化为目录、正文、关联信息依次排列；
- 所有空状态使用用户语言，例如“还没有已应用的 AI 会话”。

不实现无限滚动。分页更简单、可预测、易测试。

## 10. 只读与安全边界

- 服务只绑定 `127.0.0.1`。
- 只接受 `Host: 127.0.0.1[:port]` 或 `Host: localhost[:port]`；其他 Host 返回 403。
- 只实现 GET 和 HEAD；其他方法返回 405。
- 页面路径继续限制在 `wiki/pages/`，拒绝绝对路径、反斜杠、`..` 和 symlink。
- 所有 Wiki 页面从 Git Commit 读取，不从可变工作树读取。
- 所有 SQLite 查询使用只读连接。
- 响应包含 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、
  `Referrer-Policy: no-referrer`、`X-Frame-Options: DENY`。
- CSS 和 JavaScript 由同源 `/app.css`、`/app.js` 返回；CSP：
  `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'`。
- 页面不加载远程脚本、字体、图片、统计或模型。
- 用户可读响应默认不返回完整 Source ID、绝对路径、remote URL、环境变量、凭证或 Raw blob。
- 浏览门户不修改 Workspace、session、ChangeSet、receipt 或 Git 状态。

## 11. 最小代码范围

优先只修改：

- `src/memoryforge/local_portal.py`；
- `tests/test_local_portal.py`；
- `docs/LOCAL_DYNAMIC_PORTAL_SPEC.md`。

若 `local_portal.py` 超过约 700 行，再将内存目录拆到：

- `src/memoryforge/portal_catalog.py`；
- `tests/test_portal_catalog.py`。

第一版不提前拆分。复用 `showcase._markdown_html`、Workspace 只读连接、Git 读取和现有 FTS。

## 12. 实施顺序

### 阶段 1：真实数据源首页

- 修正 summary 计数命名；
- 增加项目和来源分类 API；
- 实现首页、项目卡、来源卡；
- 直接 hash 路由进入阅读页。

### 阶段 2：分类型阅读模板

- 增加页面分类和展示层章节映射；
- 实现项目、代码、会话、飞书和普通笔记模板；
- 审计详情移到系统状态。

### 阶段 3：确定性关联

- 建立直接链接、精确代码路径提及和反向索引；
- 页面右侧增加相关知识；
- 为项目聚合关联会话和飞书资料。

每阶段单独可用。阶段 1 未完成前，不做阶段 3。

## 13. 验收标准

1. 合成 Workspace 含 2 个仓库、1 份飞书文档、1 个已应用 AI 会话、1 个待应用 AI 会话时，
   首页分类、数量和状态精确；不出现当前 Workspace 之外的示例项目。
2. 入口命令不读取或回退到其他 Workspace。
3. 3000 页合成 Workspace 的首屏 HTML 不包含全部页面正文，大小小于 24KB。
4. 项目、AI 会话、飞书资料、代码模块、代码文件各有一个固定测试页面通过模板断言。
5. `#page=` 深链直接打开目标页面，刷新后状态保留。
6. 搜索可按标题、路径和正文命中；项目与类型筛选可组合。
7. 一个 AI 会话精确提到 `src/store.py` 时，会话页和代码页均显示反向关系。
8. 模糊同名符号不建立“精确提及”。
9. `page_sources`、repository、内部链接和飞书章节关系均可跳转。
10. 浏览前后 Workspace 文件树、SQLite、Git HEAD、index 和 worktree 字节不变。
11. 非 localhost Host 返回 403；不安全路径返回 400；非 GET/HEAD 返回 405。
12. HTML 无内联脚本、无远程资源，并包含要求的安全响应头。
13. `ruff check .`、`mypy src`、`tests/test_local_portal.py` 和全量测试通过。

## 14. 完成定义

功能完成后，用户不需要理解 Source ID、page hash、ChangeSet 或编译器章节名，也能完成：

```text
看到已有知识
找到所属项目
区分代码、飞书和 AI 会话
阅读一页
跳到其来源和相关知识
确认知识是否当前、已应用、未验证
```

达到以上体验后停止增加 Portal 功能。图谱、编辑器、公网托管和新检索技术继续冻结。

---

## 15. LLMWiki 用户操作层扩展

状态：PROPOSED

本扩展借鉴 DeepWiki、OpenDeepWiki 和 CodeAlmanac 的共同体验：安装可以使用命令行，日常导入、
更新、阅读和审核不要求用户记忆流水线命令。

本扩展覆盖前文以下限制：

- 第 3 节“不在浏览器审核、批准或应用”；
- 第 4 节只允许 `showcase serve` 入口；
- 第 10 节“只实现 GET 和 HEAD”及“浏览门户不修改 Workspace”；
- 第 11 至 14 节中仅面向只读 Portal 的代码范围、阶段和完成定义。

未被明确覆盖的隐私、安全、固定 Commit 阅读和零新增前端依赖要求继续有效。正式 Wiki 正文仍
不提供网页编辑器；网页只调用受类型约束的既有生命周期操作。

## 16. 目标用户体验

首次安装后，用户只需启动一个入口：

```bash
memoryforge start --workspace <workspace>
```

`start` 是 `showcase serve` 的用户入口别名，负责启动本地服务并默认打开浏览器。`showcase
serve` 继续保留给脚本和高级用户。没有显式 Workspace 时不得扫描磁盘或静默选择其他知识库；
若本机只登记了一个 Workspace，可以显示确认页，由用户明确选择。

日常流程收敛为：

```text
打开本地 Wiki
  -> 添加或选择来源
  -> 后台导入并编译
  -> 查看可读差异
  -> 批准并应用
  -> 浏览、搜索或提问
```

用户界面不得要求用户理解 `ingest`、ChangeSet ID、receipt、SourceVersion 或 page hash。系统
状态页可以保留这些审计信息，但默认折叠。

## 17. 一级导航扩展

一级导航调整为：

```text
首页
我的知识
添加来源
知识更新
后台任务
系统状态
```

“我的知识”内部继续使用前文定义的项目、AI 会话、飞书资料和其他知识分类。搜索框始终可见，
提问入口可以放在首页和阅读页，不单独增加一级导航。

### 17.1 首页

首页只回答四个问题：

1. 我有哪些知识；
2. 最近更新了什么；
3. 是否有内容等待审核；
4. 我现在想添加来源还是提问。

首页主操作固定为“添加来源”和“提问”。后台任务、错误和待审核数量只有非零时才显示。

### 17.2 我的知识

使用用户可识别的卡片或列表展示：

- 代码仓库：仓库名、语言、固定 Commit、页面数、最近同步时间；
- AI 会话：平台、标题、日期、关联项目、是否已应用；
- 飞书资料：文档标题、章节数、最近同步时间；
- 文件和网页：标题、来源类型、更新时间。

内部标识符不得作为主标题。完整 ID 只在“来源详情”中展示，并提供复制按钮。

## 18. 添加来源向导

“添加来源”第一页只展示五类入口：

```text
代码仓库
AI 会话
飞书文档
文件或文件夹
网页 / GitHub 讨论
```

每类向导最多三步：选择来源、确认隐私、开始处理。高级参数默认折叠。

### 18.1 代码仓库

第一版支持本地仓库路径。用户输入或选择路径后，界面展示仓库名、当前分支、Commit 和检测到
的语言，再让用户确认。不得把绝对路径写入浏览器历史、页面 URL 或后续普通 API 响应。

远程 Git URL 只在已有安全导入能力可以复用时开放；不得在本轮增加凭证管理器。需要凭证的
仓库继续通过本地 checkout 导入。

### 18.2 AI 会话

第一版支持：

- 扫描本机 Codex 会话目录中的未收录会话；
- 上传或选择导出的 Markdown、TXT、JSONL；
- 展示会话标题、时间和关联项目候选，由用户勾选收录。

扫描结果不得把完整会话正文发送到浏览器列表；列表只返回标题、时间、平台、大小和本地匿名
标识。会话默认 `local_only`，不得提供“一键公开”。

### 18.3 飞书文档

用户粘贴文档或 Wiki 链接。界面只显示标题和授权状态；认证继续复用本机已有配置，不在 Portal
接收或显示 token。未授权时给出明确的本地授权命令，不尝试代替用户登录。

### 18.4 文件、文件夹和网页

- 小文件允许浏览器上传；
- 大目录使用本地路径，保留既有 no-follow、类型和大小限制；
- 网页使用明确 URL，不自动抓取同站其他页面；
- 默认 `local_only`，公开资料需要用户显式切换并二次确认。

### 18.5 提交后的行为

提交来源后立即返回任务页，不让 HTTP 请求等待完整编译。后台任务按顺序执行：

```text
校验来源
  -> 导入 SourceVersion
  -> 生成 ChangeSet
  -> 等待用户审核
```

禁止自动批准或自动应用。相同来源和版本重复提交应返回“已经是最新版本”，不得制造重复页面。

## 19. 后台任务

所有耗时操作使用可恢复的本地任务记录，至少包含：

- 用户可读名称；
- 类型：导入、刷新、编译、应用；
- 状态：等待、运行中、等待审核、完成、失败、已取消；
- 创建、开始和结束时间；
- 当前阶段和简短进度；
- 结构化 `error_code`、用户提示和可重试标记；
- 关联 Source、ChangeSet 和 Workspace Commit 的内部引用。

任务列表默认只显示名称、状态、阶段、耗时和下一步。日志、内部 ID 和技术错误放进详情折叠区。
服务重启后，不把残留 `running` 任务继续显示为运行中；应恢复为可重试失败或从安全检查点继续。

第一版使用单 Workspace、单工作进程、串行写任务。没有真实性能证据前不实现分布式队列、并行
Agent 或多租户调度。

## 20. 知识更新与审核

“知识更新”代替用户直接操作 ChangeSet。列表按来源展示：

```text
仓库 A 更新：新增 3 页，修改 5 页，删除 1 页
AI 会话：新增 1 页
飞书文档 B：修改 2 页
```

详情页必须提供：

- 来源名称和当前版本；
- 新增、修改和删除页面数量；
- 每页标题和可读 Markdown 差异；
- 引用依据和隐私状态；
- lint 或编译警告；
- “拒绝”和“批准并应用”两个主操作。

“批准并应用”是一次明确用户手势，但后端仍按真实状态机执行：记录 review、生成 approval
receipt、校验固定输入、apply、lint。不得调用或复制 `apply --approve` 的 legacy 绕过逻辑。
任一步失败时停止并显示可恢复状态，不得把部分成功显示为已应用。

拒绝必须保留审计记录，但不删除 Raw SourceVersion。第一版不提供批量全部批准；可以按同一来源
批准一个 ChangeSet，降低误操作范围。

## 21. 自动更新

自动更新是可选能力，默认遵循安全边界：

- 代码仓库：检测已登记 checkout 的新 Commit；
- AI 会话：检测 Codex 会话目录中的新增或变化文件；
- 飞书和网页：只有用户显式开启后才定时刷新；
- 自动更新只生成待审核内容，永不自动批准。

macOS 可复用现有 `launchd` 路线。Portal 提供“开启自动更新”和“关闭自动更新”，并展示计划、
上次运行和下次运行时间。生成配置时必须保留用户已有的 Codex、shell 和 launchd 配置，不覆盖
整个文件。

知识维护只做确定性和可审核的工作：

- 新版本替代当前版本，但保留历史 SourceVersion；
- 来源删除或章节消失时生成停用提议，不静默删除正式 Wiki；
- 重复页面、弱关联和过期知识只生成维护 ChangeSet；
- 没有持久新知识时允许 no-op。

## 22. 写操作 API

保留前文所有只读 API，新增最小控制面：

```text
POST /api/sources/preview
POST /api/sources
POST /api/sources/<id>/refresh
GET  /api/jobs
GET  /api/jobs/<id>
POST /api/jobs/<id>/cancel
GET  /api/updates
GET  /api/updates/<id>
POST /api/updates/<id>/reject
POST /api/updates/<id>/approve-and-apply
GET  /api/automation
POST /api/automation
```

接口名称表达用户概念；内部可以映射到 Source、ChangeSet 和 receipt。所有写入请求：

- 只接受 `application/json`，上传文件除外；
- 校验同源 `Origin` 和 `Host`；
- 要求启动时生成、仅保存在当前页面内存中的 CSRF token；
- 不接受 shell 命令、任意参数数组或可执行文件路径；
- 使用既有 Python 应用服务，不通过拼接字符串启动 CLI 子进程；
- 返回结构化错误，不返回 traceback、环境变量或凭证。

页面内容和目录继续从固定 Wiki Commit 读取。写操作成功并生成新 Commit 后，目录缓存整体失效并
按新 Commit 重建。

## 23. 安全边界增补

本地服务可写不等于信任所有本机网页。除前文安全要求外，还必须：

- 只绑定 loopback，拒绝非 localhost Host；
- 所有写操作拒绝缺失或跨站 Origin；
- CSRF token 不写 URL、日志、Cookie 或 `localStorage`；
- 来源路径先解析再校验，不跟随符号链接逃逸；
- 写操作按 Workspace 加锁，避免 CLI、watch 和 Portal 并发修改；
- apply 前再次绑定并校验 SourceVersion、ChangeSet、review、approval 和 Workspace HEAD；
- 上传内容写入受控临时目录，校验成功后再进入 Raw store；
- Portal 不保存飞书、Git 或模型凭证。

停止来源、取消任务和拒绝更新可以直接执行；删除 Raw、删除历史版本、重写 Git 历史仍不进入
网页能力。

## 24. 最小实现范围

优先复用已有 CLI 下方的 Python 逻辑。若现有命令只有 CLI 编排，先提取小型应用服务，不在
HTTP handler 中复制业务逻辑。

预计修改范围：

- `src/memoryforge/cli.py`：增加 `start` 薄入口；
- `src/memoryforge/local_portal.py`：控制面路由和安全校验；
- `src/memoryforge/portal_catalog.py`：只读目录，达到前文拆分条件后再新增；
- `src/memoryforge/portal_jobs.py`：只有任务状态无法复用现有存储时才新增；
- 对应测试和用户文档。

禁止引入 React、Vue、Node 构建链、Celery、Redis、向量库或第二套数据库。原生 HTML、CSS、
JavaScript 和现有 SQLite 足够完成第一版。

## 25. 扩展实施顺序

### 阶段 A：一个入口和来源总览

- 增加 `memoryforge start`；
- 完成前文只读首页、项目和来源分类；
- 增加“添加来源”入口和预览，但先不提交写操作。

### 阶段 B：添加来源和任务进度

- 接通本地仓库、Codex 会话和文件导入；
- 生成后台任务并展示进度；
- 编译完成后进入“等待审核”。

### 阶段 C：网页审核和应用

- 展示可读差异；
- 接通拒绝、批准、apply 和 lint；
- 覆盖并发、重启恢复、CSRF 和失败原子性测试。

### 阶段 D：可选自动更新

- 接通代码和 Codex 会话检测；
- 提供 launchd 状态与开关；
- 所有更新继续停在待审核状态。

每阶段必须独立可用。阶段 C 未通过安全和原子性测试前，不实施阶段 D。

## 26. 扩展验收标准

1. 新用户除安装外，只用 `memoryforge start --workspace <workspace>` 即可完成日常操作。
2. 用户能从网页添加一个本地仓库、一个 Codex 会话和一个普通文件。
3. 添加后 HTTP 请求立即返回任务，页面显示真实阶段，刷新页面不丢失状态。
4. 编译结果不会自动进入正式 Wiki；只有“批准并应用”后才产生新 Wiki Commit。
5. “批准并应用”产生真实 review 和 approval 记录，不使用 inline legacy approval。
6. 同一版本重复添加返回 no-op；新 Commit 只生成对应增量更新。
7. AI 会话始终为 `local_only`，普通页面不暴露正文、绝对路径或完整内部 ID。
8. 跨站 POST、非法 Host、缺失 CSRF、路径逃逸和并发写入均被拒绝。
9. Portal、CLI 和自动更新共享同一状态机；不存在网页专用的简化数据通路。
10. 服务重启后任务状态可解释、可重试，不出现永久“运行中”。
11. 3000 页 Workspace 仍按需加载；控制面不得把全部页面或 Raw 内容送进浏览器。
12. 关闭 Portal 后，CLI 的导入、审核、应用、查询和静态 Showcase 行为保持兼容。

## 27. 扩展完成定义

普通用户无需记忆多条命令，即可完成：

```text
看到有哪些 Wiki
添加代码、AI 会话、飞书、文件和网页
知道后台正在做什么
看懂知识发生了哪些变化
明确批准后再写入正式 Wiki
等待下一次自动更新
```

达到以上体验后停止。富文本编辑、公共托管、多人权限、移动端、任意插件市场、知识图谱和新增
检索基础设施继续冻结。
