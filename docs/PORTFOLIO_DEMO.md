# 秋招演示与面试说明

<!-- memoryforge-release-claim: version=0.3.0; status=release_candidate; active_candidate=9; platform_gate_candidate=9; platform_gate_status=accepted; review_status=pending; macos_passed=607; linux_passed=604; linux_skipped=3; windows_confirmation=not_run; confirmation=not_run; holdout=not_run -->

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
   ↓ review / approve / apply
Markdown Wiki：INDEX.md + pages/
   ↓
渐进式查询：索引 → 页面 → 必要时原文
   ↓
CLI / MiniClaude Agent / 飞书机器人
```

面试时可以直接说：

> 我没有把所有资料直接塞给模型，而是先把资料编译成可读、可审核、可增量更新的 Wiki。查询时先读目录和少量页面，只有需要核验时才回到原文，并且回答必须带来源。

### 2. 跑公开端到端 Demo

最快路径不依赖外部仓库或模型 Key：

```bash
.venv/bin/python demo/run_showcase_demo.py \
  --workdir /private/tmp/memoryforge-showcase-demo \
  --output /private/tmp/memoryforge-showcase
open /private/tmp/memoryforge-showcase/index.html
```

按页面顺序展示来源版本、Wiki 树、ChangeSet Diff、`review → approve → apply`、Query/Citation
Trace、正向指标、Click 外部失败、正确拒答和 Code Wiki Mermaid。页面为只读静态文件，不依赖
正在运行的服务。

完整文档 Wiki 复现仍可使用已存在的公开 Git checkout：

准备一个已经存在的公开 Git checkout，然后执行：

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --workspace /private/tmp/memoryforge-public-demo \
  --output /private/tmp/memoryforge-public-result.json
```

它会依次执行：

```text
init → git-add → git-sync → ingest → review → approve → apply → lint → ask → eval
```

本次公开基线使用 `AgentSkill-Eval@93f5dc0`，会导入 56 个来源文件，生成 57 个 Wiki 文件，运行
30 道题。当前结果是：回答准确率 96.7%、Top-3 来源召回率 96.2%、引用落地准确率 100%、
拒答准确率 100%。同一题集上的 Raw FTS Top-3 来源召回率为 57.7%，完整方法见
[公开 Benchmark](BENCHMARK.md)。

随后展示 [Click 外部评测](CLICK_DOCS_BENCHMARK.md)：20 道冻结题中 development/holdout 的
Answer accuracy 只有 10%/0%，尽管 Citation grounding 都是 100%。这说明工程链路可信不等于
问答能力已经通用化；引用可回读也不等于答案正确。

再打开 [`code_wiki_public.json`](../demo/results/code_wiki_public.json) 和
[`release_provenance.json`](../demo/results/release_provenance.json)：前者证明三语言 Symbol、
Relation、Module、Mermaid edge 和 Citation 指标均为 100%，单文件更新只影响 20% 的源码模块页；
后者是 v0.2.0 的历史发布证据。v0.3.0 的 Wheel/sdist、SHA256、benchmark summary 和
provenance 由双隔离本地构建生成；原生 Windows confirmation、holdout 与最终 tag 完成前只称
release candidate，不称已发布。

当前 Candidate 7 本地门禁为 macOS 599 passed、Linux 596 passed / 3 skipped，coverage 均为
88%；Wheel/sdist 与 development 字节一致。原生 Windows confirmation 未运行。这组结果只证明
固定 Commit 的本地交付链路，不外推为 Windows 成功。

Candidate 5 最终静态审查为 0 P0 / 10 P1 / 2 P2，结果 rejected。审查失败优先于本地门禁成功，
因此不得授权 confirmation。

Candidate 6 development 已 6/6 通过，并从 detached Commit 快照构建；最终静态审查为
0 P0 / 9 P1 / 3 P2，结果 rejected。development 与本地门禁制品不一致，故不得授权 confirmation。

Candidate 7 已固定 package 输入和构建 epoch；development 6/6 通过。macOS 首次本地门禁因旧
`sdist.exclude` 测试合同而 598/599 rejected，失败保留。修复测试合同后，同一 package bytes
通过双平台门禁。最终静态审查发现 0 P0 / 10 P1 / 3 P2，结果 rejected；13 条原始发现保留，
因此不得授权 confirmation。

Candidate 8 development 已 6/6 通过：固定 checkout EOL；Summary schema 2 绑定 acceptance 与
negative Commit；冻结 manifest 从实际哈希题集计数；Workspace drill 验证正确 answer、Citation、
unknown 和恢复重放。本地门禁为 macOS 604 passed、Linux 601 passed / 3 skipped；两端 package
bytes 与 development 一致，retained SHA256SUMS 可原位重放。终审发现 0 P0 / 4 P1 / 2 P2，
结果 rejected；6 条发现保留，因此不得授权 confirmation。

Candidate 9 development 已 6/6 通过：版本探针不再继承 coverage 环境，历史 review sidecar
从固定 Commit 复算，Showcase 指标与最终 case 一致，Code Wiki 的 12 项 metrics、6 项 gates、
incremental 与原始 Evidence 完整绑定。本地门禁为 macOS 607 passed、Linux 604 passed / 3 skipped；
Wheel、sdist 与 raw Code Wiki Evidence 跨平台同字节。终审尚未运行，confirmation 继续关闭。

随后展示 support-score development：Answer 与 Selective Accuracy 为 100%，Coverage 为 90%，
Risk 为 0%；同时明确 confirmation 尚未运行。这样可以说明“有正向指标，但不越过冻结 split
提前外推”。

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

### 5. 可选展示飞书入口

飞书只作为可选交互层，Wiki 真值和静态 Showcase 都保存在本地。纯确定性模式：

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

新资料先生成 ChangeSet，不直接覆盖正式 Wiki。用户先 `review` 看变更，再用 `approve` 明确批准，最后由 `apply` 写入；更新失败可以查看历史并回滚。这样 Wiki 是可维护的项目资产，而不是一次性摘要。

### Mermaid 架构图为什么可信？

图节点来自确定性 `ModulePlan`，图边来自真实 `CodeRelation` 聚合；每条边都带完整 edge ID 和
Relation Citation，可回读到固定 Git Blob 的字符范围。系统不让模型猜边，证据不属于源模块时直接
拒绝编译。

## 项目边界

当前版本刻意不做公网部署、群聊、多用户权限、定时同步、向量数据库、知识图谱和通用编码 Agent。这些不是遗漏，而是为了把“个人技术 Wiki 的编译、增量更新、可信查询和证据回溯”做深。

完整主张、Evidence 和不能外推的边界见 [Evidence Claims](EVIDENCE_CLAIMS.md)。

## 演示前检查清单

- [ ] 只使用公开或脱敏资料；不把公司代码、飞书正文、Token、App Secret 放进仓库。
- [ ] 公开 Demo 能从空 Workspace 重新跑通。
- [ ] 发布 Wheel 能在全新 venv 通过 `run_release_check.py`，并核对 SHA256。
- [ ] `memoryforge lint --workspace <workspace>` 返回 `clean`。
- [ ] 至少演示一个正常回答和一个拒答。
- [ ] 至少展示一次 `review → approve → apply` 的 Wiki 变更流程。
- [ ] 如果展示模型，单独记录模型耗时，不把私有调用日志提交到 GitHub。
