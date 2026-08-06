# MemoryForge 公开 Benchmark

这组实验回答一个具体问题：把资料先编译成可维护 Wiki，再做渐进式查询，是否比直接在原始文档上做词法检索更容易找到正确来源？

## 数据与方法

- 公开资料：`AgentSkill-Eval@93f5dc0`，56 个来源文件；
- 题集：30 题，包含 16 道单来源题、5 道多来源题、4 道无答案题和 5 道同义改写题；
- MemoryForge：`INDEX.md → Wiki 页面 → Citation`，每题最多展开 3 个 Wiki 页面；
- Raw FTS：直接在原始 SourceVersion 上使用 SQLite FTS5，任意查询词命中后按 BM25 取 Top-3；
- 两条路径使用同一份资料和同一份题集，不调用模型，也不使用 LLM Judge。

单来源和改写题只要 Top-3 中出现预期来源就算召回；多来源题必须同时找到全部预期来源。Raw FTS 只是一条检索基线，不生成答案，因此没有回答准确率、引用准确率和拒答准确率。

## 结果

| 指标 | MemoryForge | Raw FTS |
| --- | ---: | ---: |
| Top-3 来源召回率 | **96.2%** | 57.7% |
| 多来源完整覆盖率 | **100.0%** | 20.0% |
| 回答准确率 | 100.0% | 不适用 |
| 引用落地准确率 | 100.0% | 不适用 |
| 无答案拒答准确率 | 100.0% | 不适用 |
| 平均展开 Wiki 页面 | 3.0 | 不适用 |
| 平均证据/候选文本字符数 | 159.2 | 728.0 |

在这套公开题集上，Wiki 路由的 Top-3 来源召回比直接 Raw FTS 高 38.5 个百分点，多来源完整覆盖高
80 个百分点。这个结果说明“先形成主题页面，再逐层展开”对跨文档问题有帮助；它不证明
MemoryForge 在所有知识库上都优于向量 RAG。

v0.2.0 重新审计题集时发现，“前端页面使用 React 还是 Vue？”在固定源仓
`docs/evolution-timeline-dashboard.md` 中有明确 Vue 证据，因此它不再伪装成无答案题。题目文本和源
Commit 均未改变，只修正 expected status、source path 和 required term；这也使 Raw FTS 的分母与
结果从 56.0% 变为 57.7%。

## 已修复的失败案例

v0.2.0 的唯一失败是同义改写题：

```text
怎样核验实验统计不是模拟出来的？
```

系统当时已经把 `docs/statistics-and-reports.md` 对应页面排在首位，但首条事实只与问题命中
“实验”和“统计”两个中文短语。纯中文事实的固定三词门槛拒绝了该 Citation，最终返回
`unknown`。

当前选择器只对“首选页面的首条摘要事实”放宽到两个短语，并要求问题和事实都包含否定语义。
这使目标题选中 `Manifest` / `RunMeasurement` 事实，同时保留 GPU 型号、作者生日、数据库分片键
等无答案题的拒答。题目、预期来源和固定源 Commit 均未修改；该题从空 Workspace 重跑后通过，
其余 29 题的回答、Citation 和拒答结果没有回退。这个修复发生在 Citation 选择阶段，没有引入
向量数据库。

当前仍有 1 个来源口径缺口：前端框架题从 `docs/dashboard-mvp.md` 引用了正确的 Vue 事实，但固定
预期来源是 `docs/evolution-timeline-dashboard.md`。因此回答和 Citation grounding 均正确，来源
召回仍如实记为 96.2%，没有把语义正确的替代来源改写成预期来源。

## 如何复现

准备一个已克隆的公开 `AgentSkill-Eval` 仓库，然后从空 Workspace 运行：

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --workspace /private/tmp/memoryforge-public-demo \
  --output /private/tmp/memoryforge-public-result.json
```

脚本会真实执行 `init → git-add → git-sync → ingest → review → approve → apply → lint → ask → eval`。完整逐题结果保存在 [`demo/results/agent_skill_eval_public.json`](../demo/results/agent_skill_eval_public.json)。

## 结论与边界

- 当前规模继续使用 SQLite FTS5，不引入向量数据库或常驻检索服务；
- 下一步冻结第二个公开仓库做盲测，验证该选择规则的外部有效性；
- 当前题集仍只来自一个公开技术项目，不能外推为任意知识库上的普遍性能；
- 不使用公司代码、飞书正文、模型 Key 或内部运行日志。

## 代码 Wiki C0-C4 基线

固定三语言公开夹具包含 6 个源码文件、20 个 Symbol、10 条预期关系和 5 条模块归属。
评测不调用模型，且把当前不支持的关系保留为 `known_gap`。

| 指标 | 结果 |
| --- | ---: |
| Source 覆盖率 | 100% |
| Symbol 召回率 | 100% |
| Core Relation 召回率 | 100% |
| 原 Known-gap Relation 召回率 | 100% |
| 总体 Relation 召回率 | 100% |
| 模块归属准确率 | 100% |
| Citation 落地准确率 | 100% |
| Architecture edge grounding | 100% |
| Mermaid edge coverage | 100% |
| Architecture Citation coverage | 100% |
| Mermaid 顺序确定性 | 100% |
| 确定性重放 | 100% |
| 单文件更新页面比例 | 20% |

题集未修改；原先失败的 Python 跨文件 import/call、Go 参数接收者方法调用和 TypeScript
局部变量方法调用现已全部命中。C4 Mermaid 边只来自确定性 `ArchitectureGraph`，每条边都绑定
真实 `CodeRelation` Citation。
复现命令：

```bash
.venv/bin/python demo/run_code_wiki_benchmark.py \
  --workdir /private/tmp/memoryforge-code-wiki-c0 \
  --output /private/tmp/code_wiki_public.json
```

完整结果位于 [`demo/results/code_wiki_public.json`](../demo/results/code_wiki_public.json)。
