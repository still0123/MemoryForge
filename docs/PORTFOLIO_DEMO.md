# 公开演示与展示说明

<!-- memoryforge-release-claim: version=0.4.0; status=released; supported_platforms=macos; macos_passed=656; linux_status=not_rerun; windows_status=not_verified; release_verification=passed -->

这份文档按当前 v0.4 主线讲解 MemoryForge。演示只使用仓库内固定夹具或公开
`AgentSkill-Eval` 文档，不使用公司代码、真实飞书正文、Token 或 App Secret。

## 版本口径

| 范围 | 状态 |
| --- | --- |
| `v0.4.0` 正式发布 | macOS `656 passed`；Linux 未重跑；Windows 未验证 |
| 当前 `main` | 增加未发布的本地 Portal 控制面和冻结期加固；macOS `731 passed` |
| 功能边界 | 已冻结，只修 Bug、错误回答和文档 |

演示时先说明自己展示的是 `v0.4.0` Release 还是当前 `main`，不要把 `main` 的 Portal 门禁写成
v0.4.0 的发布结果。

## 30 秒介绍

> MemoryForge 是一个本地优先的技术知识 Wiki 编译器。它把 Git、文档、飞书和 AI 对话先编译成
> 可审核的 Markdown Wiki，再通过渐进式查询和受限 Agent 给出带来源的答案。项目重点不是再做
> 一个聊天机器人，而是解决知识如何持续更新、结论如何人工审核、回答如何回到原始证据。

## 3 分钟演示

### 1. 先讲主链

```text
代码 / 文档 / 飞书 / AI 对话
        ↓
不可变 SourceVersion
        ↓
WikiCompiler 生成 ChangeSet
        ↓ review → approve → apply
Markdown Wiki + Git Commit
        ↓
INDEX → 少量 Wiki 页面 → 必要时原文
        ↓
Portal / CLI / MiniClaude Agent / 飞书
```

讲解重点：

- 原始资料不会直接覆盖 Wiki；
- `review`、`approve`、`apply` 分开留痕；
- 查询读少量页面，证据不足就返回 `unknown`；
- Citation 可以回到固定版本和原文 locator。

### 2. 跑零 Key 端到端 Demo

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python demo/run_showcase_demo.py \
  --workdir /private/tmp/memoryforge-showcase-demo \
  --output /private/tmp/memoryforge-showcase
open /private/tmp/memoryforge-showcase/index.html
```

按页面顺序展示：

1. SourceVersion 和 Wiki 树；
2. ChangeSet Diff 与 `review → approve → apply`；
3. Query / Citation Trace；
4. 正向 Benchmark、正确拒答和 Click 外部失败；
5. Python、Go、TypeScript Code Wiki 的 Mermaid 架构图。

这条路径使用固定公开夹具，真实执行：

```text
import → Code Index → ChangeSet → review → approve → apply → query → showcase
```

### 3. 展示一个回答和一个拒答

若已准备公开 `AgentSkill-Eval` checkout：

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --workspace /private/tmp/memoryforge-public-demo \
  --output /private/tmp/memoryforge-public-result.json

.venv/bin/memoryforge ask \
  '统计器如何避免把模拟实验结果当成真实结论？' \
  --workspace /private/tmp/memoryforge-public-demo \
  --debug --verify
```

重点展示输出里的：

- `answer`：只组织已命中的事实；
- `citations`：Wiki 页面、来源版本和字符范围；
- `trace`：实际展开的索引、页面和原文证据。

再问知识库没有的问题：

```bash
.venv/bin/memoryforge ask \
  '这个项目的 Kubernetes 集群有几个节点？' \
  --workspace /private/tmp/memoryforge-public-demo
```

系统应拒答，而不是编造节点数。

### 4. 最后展示发布证据

打开 [`v0.4.0 Release`](https://github.com/still0123/MemoryForge/releases/tag/v0.4.0)，展示：

- Wheel 和 sdist；
- 两次隔离构建的同字节制品；
- `release-provenance.json`；
- `benchmark-summary.json`；
- `workspace-drill.json`；
- `SHA256SUMS`。

正式口径：

| Evidence | 结果 | 边界 |
| --- | ---: | --- |
| v0.4.0 发布门禁 | macOS **656 passed** | Linux 未重跑，Windows 未验证 |
| AgentSkill-Eval | Answer **96.7%**；Source recall@3 **96.2%** | 30 道固定公开题 |
| Citation / Abstention | **100% / 100%** | 同一固定题集 |
| 三语言 Code Wiki | Symbol / Relation / Mermaid / Citation **100%** | 固定公开夹具 |
| Click 外部迁移 | Answer **10% / 0%** | development / holdout 真实负结果 |

这些结果证明固定数据集上的工程链路，不代表任意仓库的通用准确率。完整方法见
[公开 Benchmark](BENCHMARK.md)，主张边界见 [Evidence Claims](EVIDENCE_CLAIMS.md)。

## 可选展示

### 本地 Portal

当前 `main` 可运行：

```bash
.venv/bin/memoryforge init /private/tmp/memoryforge-portal-demo
.venv/bin/memoryforge start \
  --workspace /private/tmp/memoryforge-portal-demo
```

展示“添加来源 → 后台任务 → 知识更新 → 批准并应用 → 阅读/提问”。明确说明这是当前
`main` 的未发布控制面，不改写 v0.4.0 Release Evidence。

### MiniClaude Agent

```bash
.venv/bin/memoryforge agent \
  '统计器如何避免把模拟实验结果当成真实结论？' \
  --workspace /private/tmp/memoryforge-public-demo
```

Agent 只有 `search_wiki`、`read_evidence`、`final` 三个工具，没有 Shell、任意写文件、
Subagent 或 MCP 权限。它的职责只是找证据、读证据、组织答案。

### 飞书入口

```bash
memoryforge feishu-serve --workspace /path/to/your-wiki
```

飞书只是交互层，不保存另一份真值；模型不可用时会回退到已验证 Wiki 事实。完整配置见
[FEISHU_MVP_SPEC.md](../FEISHU_MVP_SPEC.md)。

## 常见追问

### 为什么不是普通 RAG？

普通 RAG 通常直接切片、召回和拼接。MemoryForge 多了一层可维护的 Wiki：来源先被编译成稳定
主题页面，更新先审核，查询最后才按需回原文。

### 为什么不用向量数据库？

当前规模下 SQLite FTS5 已满足固定公开评测，部署和重放更简单。只有新的真实用户与外部评测
证明关键词召回不足时，才值得引入 Embedding。

### 如何防止模型胡编？

模型只能整理已命中的证据；Citation 由程序校验。没有足够证据时返回 `unknown`。
`local_only` 资料默认不发送给模型，必须显式传入 `--allow-local-llm`。

### Mermaid 架构图为什么可信？

节点来自确定性 `ModulePlan`，边来自真实 `CodeRelation` 聚合；每条边绑定固定 Git Blob 的
Citation。模型只允许补充带引用的模块叙事，不能生成架构边。

### 为什么保留失败结果？

Citation 可回读不等于答案正确。Click 外测的失败用于限制结论，避免只展示正向指标。

## 演示前检查

- [ ] 只使用公开或虚构资料。
- [ ] 明确区分 `v0.4.0` Release 与当前 `main`。
- [ ] Demo 能从空 Workspace 重新跑通。
- [ ] 至少展示一次审核链、一个正常回答和一个拒答。
- [ ] 所有公开数字都能回到提交的 JSON 或 Release 资产。
- [ ] 不把局部 Benchmark 外推为生产 SLA。

## 历史研究

v0.3.0 的逐 Candidate 过程不再放在当前演示主线。完整记录见
[v0.3 Release Candidate Specification](V030_RELEASE_CANDIDATE_SPEC.md) 和
[Release Delivery Research](research/V030_RELEASE_DELIVERY.md)。

<!-- memoryforge-release-claim: version=0.3.0; status=released; supported_platforms=macos+linux; active_candidate=19; platform_gate_candidate=19; platform_gate_status=accepted; review_status=accepted; macos_passed=642; linux_passed=639; linux_skipped=3; windows_status=not_verified; release_verification=passed -->

历史口径：v0.3.0 支持 macOS 与 Linux；Candidate 12 中间门禁为 `623 passed` 和
`620 passed / 3 skipped`，Click development/holdout 为 `10%/0%`，Windows 尚未验证。
这些数字只用于定位历史 Evidence，不代表 v0.4.0 或当前 `main`。
