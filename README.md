# MemoryForge

> 把散落在代码仓库、技术文档和飞书里的信息，整理成一套你自己能长期维护的技术 Wiki。

写项目久了，真正难找的往往不是代码，而是这些问题的答案：

- 这个服务当初为什么这样设计？
- 缓存时间、限流规则写在哪份文档里？
- 两个仓库之间是什么关系？
- 半年前的方案为什么没有采用？

资料通常散落在 README、`docs/`、ADR、复盘和飞书文档里。MemoryForge 的目标不是再做一个“聊天窗口”，而是先把这些资料沉淀成可读的 Markdown Wiki；需要时，再从 Wiki 里找到答案和原始出处。

它是一个单用户、本地优先的个人知识库项目。核心想解决的问题是：**资料不断变化时，如何持续维护一份可信、可检索、能追溯来源的 Wiki。**

## 最终用起来是什么感觉？

你把已经克隆到本地的项目仓库、几份设计文档和一篇飞书文档放进来。MemoryForge 会生成类似这样的知识库：

```text
my-wiki/
├── wiki/
│   ├── INDEX.md                 # 总目录：有哪些主题、每页讲什么
│   └── pages/
│       ├── order-service.md     # 服务/仓库介绍
│       ├── cache-policy.md      # 机制、约束、踩坑
│       └── payment-retry.md     # 方案选择和复盘
├── raw/                         # 导入时保存的原始资料快照
├── .memoryforge/                # 本地 SQLite 索引和版本信息
└── .git/                        # Wiki 的修改历史
```

之后可以直接问：

```bash
memoryforge ask '订单服务的缓存多久过期？' --workspace ./my-wiki
```

程序不会把所有文档都读一遍。它先看 `INDEX.md`，再打开最相关的少量 Wiki 页面；只有加上 `--verify` 时，才回到原始资料核对具体内容。这就是项目最核心的“渐进式 Wiki 记忆”。

## 它现在能做什么？

- 导入 Markdown/TXT 文件，记录资料的历史版本；
- 从**已经在本地克隆好**的 Git 仓库中读取 README、CHANGELOG、`docs/`、`adr/` 下的文档；
- 手动导入一篇你有权限访问的飞书 Docx 或 Wiki 文档；
- 手动导入一篇无需登录、可直接读取的公开网页文章；
- 把资料编译成三类 Wiki 页面：项目/模块介绍、机制说明、方案与复盘；
- 为每个导入的 Git 仓库生成一个“项目总览”页，作为进入该仓库文档的导航入口；
- 新资料或旧资料更新后，只修改受影响的页面，不重复造一堆相似页面；
- 先生成改动预览，确认后再真正写入 Wiki；
- 在 Wiki 中全文搜索和提问，并能看到答案来自哪份资料；
- 检查 Wiki 中的目录链接、引用和来源映射是否损坏。

## 5 分钟跑通

环境要求：Python 3.11+，macOS 或 Linux。

```bash
git clone <your-repo-url>
cd MemoryForge

python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

准备一份资料，例如 `notes/cache.md`：

```md
# Cache policy

Cache entries expire after sixty seconds.
```

初始化一个自己的 Wiki，然后导入这份资料：

```bash
memoryforge init ./my-wiki

memoryforge import ./notes/cache.md \
  --category design \
  --workspace ./my-wiki
```

接下来分三步：生成候选页面、看改动、确认写入。

```bash
# 生成候选 Wiki，命令会输出 changeset_id
memoryforge ingest --pending --workspace ./my-wiki

# 先看本次会新建/修改哪些页面
memoryforge review <changeset-id> --workspace ./my-wiki

# 确认后才写入 wiki/ 并记录 Git 历史
memoryforge apply <changeset-id> --approve --workspace ./my-wiki
```

现在就可以检索：

```bash
memoryforge search 'cache policy' --workspace ./my-wiki
memoryforge ask '缓存多久过期？' --workspace ./my-wiki

# 查看它依次读取了什么
memoryforge ask '缓存多久过期？' --debug --workspace ./my-wiki

# 需要严谨确认时，再读取对应原文
memoryforge ask '缓存多久过期？' --verify --workspace ./my-wiki

# 让模型只根据命中的公开证据归纳答案，并保留来源引用
memoryforge ask '缓存多久过期？' --llm --workspace ./my-wiki
```

## 日常怎么把资料放进来？

### 已有的 Git 仓库

MemoryForge 不会替你 clone、fetch，也不会碰远端凭证。你只要把仓库准备在本地：

```bash
memoryforge git-add ~/code/my-service --workspace ./my-wiki
memoryforge git-list --workspace ./my-wiki
memoryforge git-sync <repository-id> --workspace ./my-wiki
# 刷新所有已登记的 Git 仓库和飞书文档；不会自动写入 Wiki
memoryforge refresh --workspace ./my-wiki
```

`git-sync` 只读取当前提交（`HEAD`）中的文档。仓库资料更新后，再次执行同步，然后重复 `ingest → review → apply`；系统会定位并更新原来的 Wiki 页面。

Git 仓库默认是 `local_only`，不会发送给模型。只有仓库内容可以公开时，才在登记时显式加上 `--public`；已经登记过的仓库，重新执行一次这条命令即可更新授权，然后重新同步：

```bash
memoryforge git-add ~/code/AgentSkill-Eval --public --workspace ./my-wiki
memoryforge git-sync <repository-id> --workspace ./my-wiki
```

如果某个目录是你想长期理解的核心实现，可以明确加入它的 Go 或 Python 源码；不会扫描整仓库：

```bash
memoryforge code-add <repository-id> internal/meter --workspace ./my-wiki
memoryforge refresh --workspace ./my-wiki
```

它会为选中目录中的每个 Go/Python 文件生成一张代码页，列出包/模块和公开类型、函数，并保留
对应 Git commit 和代码位置引用。内部仓库仍保持 `local_only`。

如果这些页面已经生成并应用，还可以让模型只根据“页面标题、文件路径和已有摘要”生成一份主题目录。它不会改写原有页面，也不会参与问答：

```bash
memoryforge topics <repository-id> --workspace ./my-wiki
memoryforge review <changeset-id> --workspace ./my-wiki
memoryforge apply <changeset-id> --approve --workspace ./my-wiki
```

第一次编译后，`INDEX.md` 还会多出一个“`<仓库名> 项目总览`”入口。它只负责把同一仓库的资料页串起来；具体问答仍只使用带原文引用的页面。

### 飞书文档

先使用现有的 `lark-cli` 登录，并确保你自己有文档的阅读权限：

```bash
memoryforge feishu-import 'https://<tenant>.larkoffice.com/docx/<token>' \
  --workspace ./my-wiki
```

第一版先通过 `feishu-import` 指定一篇文档；之后它会被登记到当前 Workspace，日常执行
`memoryforge refresh` 就能重新读取。长文档会按一级标题拆成“总览 + 章节”来源，后续各自生成
可检索的 Wiki 页面；命令输出的 `sources` 列表可看到每一部分。它不做后台同步、机器人和权限平台。
飞书资料默认只留在本地，不会被发送给 LLM。

### 公开网页文章

对于掘金、博客或技术论坛中的文章，可以导入用户明确给出的单个公开链接：

```bash
memoryforge web-import 'https://example.com/engineering/cache-design' \
  --workspace ./my-wiki
```

它只保存当次可读取正文的本地快照、原始链接和标题；不会登录、绕过验证码、批量爬取或后台刷新。
需要 JavaScript 渲染、登录或验证码的页面（例如当前无法静态读取的掘金文章），请先在浏览器另存为网页后使用下面的 `html-import`；也可以自行保存成 Markdown 再用 `memoryforge import` 导入。

也可以在浏览器打开文章后使用“另存为网页”，再让 MemoryForge 本地转成 Markdown：

```bash
memoryforge html-import ./article.html \
  --url 'https://juejin.cn/post/7637856870833635343' \
  --workspace ./my-wiki
```

这个命令只读取你指定的 HTML 文件，不读取浏览器 Cookie、登录态或历史记录。

## 这个项目的关键设计

### 1. 先写成 Wiki，再做检索

普通 RAG 往往把文档切块后丢进向量库。MemoryForge 先生成可以直接打开阅读的 Wiki 页面：一个页面讲清一个项目、机制或决策。即使不用模型，Wiki 本身也应该有价值。

### 2. 资料更新时，更新原页面

每份资料都有自己的版本和归属页面。某篇设计文档更新后，系统知道该改哪一页，而不是重新生成一批重复摘要。多个来源也可以共同支撑同一个页面。

### 3. 答案要能回到原文

每个页面都保存来源、版本和原文位置。需要确认时，`--verify` 才打开原始片段；日常提问只读取目录和少量页面，速度更快，也不会把无关资料塞进上下文。

```text
提问
  ↓
INDEX.md 找主题
  ↓
展开少量 Wiki 页面
  ↓
需要时才打开原始资料核验
```

### 4. 自动生成，但不直接覆盖

无论页面由本地规则还是模型草稿生成，都会先变成可看的 Diff。只有你执行 `apply --approve`，稳定 Wiki 才会更新并提交到它自己的 Git 历史中。

## 可选：让模型参与整理

默认的 `ingest` 使用本地确定性规则，适合基础沉淀和内部资料。

如果你明确允许某些来源发送到模型，也可以接一个 OpenAI-compatible 接口。推荐在项目根目录创建一次 `.env`；它已被 Git 忽略，之后不用每次 `export`：

```bash
cp .env.example .env
# 只需把 .env 里的 replace-with-your-key 改成自己的 Key

memoryforge ingest --pending --llm --workspace ./my-wiki
memoryforge ask '缓存多久过期？' --llm --workspace ./my-wiki
```

临时设置的同名环境变量优先于 `.env`，适合偶尔切换模型。

模型只负责写页面草稿。本地代码仍负责来源引用、页面格式、改动预览和最终写入。Git 和飞书导入的资料默认是 `local_only`，不会被送到这个接口。Git 资料只有通过 `git-add --public` 明确授权后才会随 `--llm` 发送；飞书资料目前始终只保留在本地。

## 现在不做什么？

为了把核心问题做深，当前刻意不做：

- 完整的 Claude Code / 通用编码 Agent；
- 飞书机器人、定时同步和权限管理；
- 向量数据库、知识图谱、多 Agent 编排；
- Web 管理后台和多用户协作；
- 自动重试、消息队列等服务端基础设施。

这些能力并非不重要，但不属于这个项目的第一目标：做好一个可维护的个人 Wiki 记忆内核。

## 完整命令

```bash
memoryforge init <workspace>
memoryforge import <path> --workspace <workspace> [--category <category>]
memoryforge git-add <local-checkout> [--public] --workspace <workspace>
memoryforge git-list --workspace <workspace>
memoryforge git-sync <repository-id> --workspace <workspace>
memoryforge code-add <repository-id> <relative-code-path> --workspace <workspace>
memoryforge refresh --workspace <workspace>
memoryforge topics <repository-id> --workspace <workspace>
memoryforge feishu-import <docx-or-wiki-url-or-token> --workspace <workspace>
memoryforge web-import <public-http-url> [--local-only] --workspace <workspace>
memoryforge html-import <saved-page.html> --url <public-http-url> --workspace <workspace>
memoryforge search <query> --workspace <workspace>
memoryforge ingest --pending [--llm] --workspace <workspace>
memoryforge review <changeset-id> --workspace <workspace>
memoryforge apply <changeset-id> --approve --workspace <workspace>
memoryforge ask <question> [--llm] [--debug] [--verify] [--max-pages 1..10] --workspace <workspace>
memoryforge lint --workspace <workspace>
memoryforge eval <public-suite.json> --workspace <workspace>
```

## 用公开资料做一次小评测

`eval` 用一份 JSON 题集检查：回答是否包含预期关键事实、引用是否能回到预期原始资料，以及每题实际展开了几页 Wiki。评测查询本身遵循日常路径，不读取原文；为了检查引用，评测器会在查询结束后单独读取被引用的原文片段，并把这部分成本单列为 `average_citation_audit_characters`。它还会记录同一问题在原始资料 FTS 搜索中的命中情况，作为简单基线；不调用模型。

仓库内提供了一份仅面向公开 `AgentSkill-Eval` 文档的示例：

```bash
memoryforge eval demo/evaluation/agent_skill_eval.json --workspace ./my-wiki
```

题集中的 `expected_source_path` 和 `required_terms` 是人工写下的验收标准，因此结果可复现、也便于发现检索退化。不要把内部资料题集或评测输出提交到公开仓库。

实现细节、数据模型和阶段计划在 [SPEC.md](./SPEC.md)。公开演示时请只使用虚构或公开资料，不要提交公司内部代码和文档。
