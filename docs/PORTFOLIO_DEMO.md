# 秋招演示与面试说明

这份文档用于把 MemoryForge 讲成一个完整项目。演示只使用公开的 `AgentSkill-Eval` 文档，不使用公司代码、真实飞书正文、Token 或 App Secret。

## 一句话介绍

MemoryForge 是一个本地优先的技术知识 Wiki 编译器：它把 Git、Markdown、飞书文档或公开网页编译成可审核的 Markdown Wiki，再通过渐进式查询和最小 Agent 给出带来源的答案。

它的重点不是“再做一个聊天机器人”，而是解决三个问题：知识如何持续沉淀、答案如何控制读取范围、结论如何回到原始证据。

## 3 分钟演示顺序

### 1. 先展示项目结构

```text
原始资料
   ↓
Source Manifest + 原文快照
   ↓
WikiCompiler：生成 ChangeSet
   ↓ review / apply
Markdown Wiki：INDEX.md + pages/
   ↓
渐进式查询：索引 → 页面 → 必要时原文
   ↓
CLI / MiniClaude Agent / 飞书机器人
```

面试时可以直接说：

> 我没有把所有资料直接塞给模型，而是先把资料编译成可读、可审核、可增量更新的 Wiki。查询时先读目录和少量页面，只有需要核验时才回到原文，并且回答必须带来源。

### 2. 跑公开端到端 Demo

准备一个已经存在的公开 Git checkout，然后执行：

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --workspace /private/tmp/memoryforge-public-demo \
  --output /private/tmp/memoryforge-public-result.json
```

它会依次执行：

```text
init → git-add → git-sync → ingest → review → apply → lint → ask → eval
```

本次公开基线使用 `AgentSkill-Eval@93f5dc0`，会导入 56 个来源文件，生成 57 个 Wiki 文件，运行 30 道题。当前结果是：回答准确率 96.7%、Top-3 来源召回率 96.0%、引用落地准确率 96.0%、拒答准确率 100%。同一题集上的 Raw FTS Top-3 来源召回率为 56.0%，完整方法见 [公开 Benchmark](BENCHMARK.md)。

### 3. 展示带引用的回答

```bash
memoryforge ask '统计器如何避免把模拟实验结果当成真实结论？' \
  --workspace /private/tmp/memoryforge-public-demo \
  --debug --verify
```

重点展示输出里的三部分：

- `answer`：回答内容；
- `citations`：引用的 Wiki 页面、版本和字符范围；
- `trace`：本次查询展开了哪些索引、页面和原文证据。

然后再问一个知识库没有的问题：

```bash
memoryforge ask '这个项目的 Kubernetes 集群有几个节点？' \
  --workspace /private/tmp/memoryforge-public-demo
```

它应当拒答，而不是编造一个节点数。这是项目很适合面试展示的地方：系统不仅要答得对，还要知道什么时候没有证据。

### 4. 展示 MiniClaude Agent

```bash
memoryforge agent '统计器如何避免把模拟实验结果当成真实结论？' \
  --workspace /private/tmp/memoryforge-public-demo
```

Agent 只有三个工具：`search_wiki`、`read_evidence`、`final`。它不能执行 Shell、调用通用写文件工具、调用 Subagent 或自行扩展工具；session 由系统写入专用本地目录。这样可以把 Agent 的职责限制在“找证据、读证据、组织答案”。

连续追问时使用同一个本地 session：

```bash
memoryforge agent '管控面主要做什么？' \
  --session interview \
  --workspace /private/tmp/memoryforge-public-demo
memoryforge agent '那数据面呢？' \
  --session interview \
  --workspace /private/tmp/memoryforge-public-demo
memoryforge agent-clear interview \
  --workspace /private/tmp/memoryforge-public-demo
```

session 只保存最近 3 轮的有限文本和引用片段，保存在 Workspace 的 `.memoryforge/sessions/`，
不进入公开 Wiki 或 Git 提交。飞书服务按 `chat_id` 隔离会话；拿不到稳定会话标识时自动使用单轮模式。

### 5. 最后展示飞书入口

飞书只作为交互和展示层，Wiki 真值仍保存在本地 Workspace。纯确定性模式：

```bash
memoryforge feishu-serve \
  --workspace /path/to/your-wiki
```

如果已经配置了 OpenAI 兼容模型，并且明确允许模型读取本次命中的本地证据：

```bash
memoryforge feishu-serve \
  --llm --allow-local-llm \
  --workspace /path/to/your-wiki
```

如果模型服务临时超时或返回 503，回答会自动回退到已验证的 Wiki 事实，并在 CLI JSON 中标记 `model_status: fallback`；飞书仍然会收到带来源的回答。飞书服务不会自动抓取整个飞书空间，也不会把本地知识库上传到 GitHub。完整接入说明见 [FEISHU_MVP_SPEC.md](../FEISHU_MVP_SPEC.md)。

## 面试官可能追问什么

### 为什么不是普通 RAG？

普通 RAG 通常直接对原文做切片、召回和拼接。MemoryForge 多了一层可维护的 Wiki：资料先被编译成主题页面，页面有稳定路由和来源引用；查询先读 `INDEX.md`，再展开少量页面，最后才按需回到原文。

### 为什么不用向量数据库？

当前数据规模下，SQLite FTS5 已经满足公开评测，且更容易部署、解释和复现。仓库还保留了语义检索实验；只有当扩展评测证明关键词召回不足，才考虑加入 Embedding。这个取舍是用评测决定基础设施，而不是先堆技术。

### 如何防止模型胡编？

模型只能整理已经命中的证据，引用索引由程序校验；没有可接受证据时返回“不知道”。`local_only` 资料默认不发送给模型，必须显式传入 `--allow-local-llm`。

### Wiki 更新怎么做？

新资料先生成 ChangeSet，不直接覆盖正式 Wiki。用户先 `review` 看变更，再 `apply --approve` 写入；更新失败可以查看历史并回滚。这样 Wiki 是可维护的项目资产，而不是一次性摘要。

## 项目边界

当前版本刻意不做公网部署、群聊、多用户权限、定时同步、向量数据库、知识图谱和通用编码 Agent。这些不是遗漏，而是为了把“个人技术 Wiki 的编译、增量更新、可信查询和证据回溯”做深。

## 演示前检查清单

- [ ] 只使用公开或脱敏资料；不把公司代码、飞书正文、Token、App Secret 放进仓库。
- [ ] 公开 Demo 能从空 Workspace 重新跑通。
- [ ] `memoryforge lint --workspace <workspace>` 返回 `clean`。
- [ ] 至少演示一个正常回答和一个拒答。
- [ ] 至少展示一次 `review → apply` 的 Wiki 变更流程。
- [ ] 如果展示模型，单独记录模型耗时，不把私有调用日志提交到 GitHub。
