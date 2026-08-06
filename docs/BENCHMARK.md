# MemoryForge 公开 Benchmark

这组实验回答一个具体问题：把资料先编译成可维护 Wiki，再做渐进式查询，是否比直接在原始文档上做词法检索更容易找到正确来源？

## 数据与方法

- 公开资料：`AgentSkill-Eval@93f5dc0`，56 个来源文件；
- 题集：30 题，包含 16 道单来源题、5 道多来源题、4 道无答案题和 5 道同义改写题；
- MemoryForge：`INDEX.md → Wiki 页面 → Citation`，每题最多展开 3 个 Wiki 页面；
- Raw FTS：直接在原始 SourceVersion 上使用 SQLite FTS5，任意查询词命中后按 BM25 取 Top-3；
- 两条路径使用同一份资料和同一份题集，不调用模型，也不使用 LLM Judge。

单来源和改写题只要 Top-3 中出现预期来源就算召回；多来源题必须同时找到全部预期来源。回答准确
要求状态、关键事实和冻结来源同时命中。Raw FTS 只是一条检索基线，不生成答案，因此没有回答
准确率、引用准确率和拒答准确率。

## 结果

| 指标 | MemoryForge | Raw FTS |
| --- | ---: | ---: |
| Top-3 来源召回率 | **96.2%** | 57.7% |
| 多来源完整覆盖率 | **100.0%** | 20.0% |
| 回答准确率 | 96.7% | 不适用 |
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

## 没有隐藏的来源偏差

30 道题中有 29 道严格回答命中；Citation grounding、多来源覆盖和拒答均为 100%。严格回答与
来源召回保留同 1 道失败：

```text
前端页面使用 React 还是 Vue？
```

系统正确回答 Vue，Citation 也可回读，但命中 `docs/dashboard-mvp.md`，不是题集冻结的
`docs/evolution-timeline-dashboard.md`。两篇文档都包含有效 Vue 证据，因此 Citation grounding
计为正确；严格回答准确率和 Source recall 均按冻结 Source expectation 计为失败。

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
- Click 第二套公开题集显示外部迁移性能很差，详见 [Click 外部评测](CLICK_DOCS_BENCHMARK.md)；
- 不根据已揭盲 holdout 调参，后续修复必须使用新冻结的独立确认集；
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
