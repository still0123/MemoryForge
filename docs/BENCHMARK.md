# MemoryForge 公开 Benchmark

这组实验回答一个具体问题：把资料先编译成可维护 Wiki，再做渐进式查询，是否比直接在原始文档上做词法检索更容易找到正确来源？

## 数据与方法

- 公开资料：`AgentSkill-Eval@93f5dc0`，56 个来源文件；
- 题集：30 题，包含 15 道单来源题、5 道多来源题、5 道无答案题和 5 道同义改写题；
- MemoryForge：`INDEX.md → Wiki 页面 → Citation`，每题最多展开 3 个 Wiki 页面；
- Raw FTS：直接在原始 SourceVersion 上使用 SQLite FTS5，任意查询词命中后按 BM25 取 Top-3；
- 两条路径使用同一份资料和同一份题集，不调用模型，也不使用 LLM Judge。

单来源和改写题只要 Top-3 中出现预期来源就算召回；多来源题必须同时找到全部预期来源。Raw FTS 只是一条检索基线，不生成答案，因此没有回答准确率、引用准确率和拒答准确率。

## 结果

| 指标 | MemoryForge | Raw FTS |
| --- | ---: | ---: |
| Top-3 来源召回率 | **96.0%** | 56.0% |
| 多来源完整覆盖率 | **100.0%** | 20.0% |
| 回答准确率 | 96.7% | 不适用 |
| 引用落地准确率 | 96.0% | 不适用 |
| 无答案拒答准确率 | 100.0% | 不适用 |
| 平均展开 Wiki 页面 | 3.0 | 不适用 |
| 平均证据/候选文本字符数 | 140.17 | 728.0 |

在这套公开题集上，Wiki 路由的 Top-3 来源召回比直接 Raw FTS 高 40 个百分点，多来源完整覆盖高 80 个百分点。这个结果说明“先形成主题页面，再逐层展开”对跨文档问题有帮助；它不证明 MemoryForge 在所有知识库上都优于向量 RAG。

## 没有隐藏的失败案例

当前唯一失败是同义改写题：

```text
怎样核验实验统计不是模拟出来的？
```

系统展开了 3 个 Wiki 页面，但没有选出可回溯的 Citation，因此返回 `unknown`。修正评测口径后，这道题不会再被空 Citation 误算为“引用正确”，所以引用落地准确率从原来的 100% 修正为 96%。

这说明当前瓶颈位于候选页面之后的事实选择，不是页面级召回：仓库中的语义检索实验已经显示页面候选召回达到 100%，继续增加向量数据库并不能直接修复这个失败。

## 如何复现

准备一个已克隆的公开 `AgentSkill-Eval` 仓库，然后从空 Workspace 运行：

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --workspace /private/tmp/memoryforge-public-demo \
  --output /private/tmp/memoryforge-public-result.json
```

脚本会真实执行 `init → git-add → git-sync → ingest → review → apply → lint → ask → eval`。完整逐题结果保存在 [`demo/results/agent_skill_eval_public.json`](../demo/results/agent_skill_eval_public.json)。

## 结论与边界

- 当前规模继续使用 SQLite FTS5，不引入向量数据库或常驻检索服务；
- 下一项检索优化应针对“候选页面已有、但没有选出 Citation”的事实选择阶段；
- 题集来自一个公开技术项目，样本量仍小，后续应增加第二个公开仓库做外部验证；
- 不使用公司代码、飞书正文、模型 Key 或内部运行日志。

## 代码 Wiki C0 基线

固定三语言公开夹具包含 6 个源码文件、20 个 Symbol、10 条预期关系和 5 条模块归属。
评测不调用模型，且把当前不支持的关系保留为 `known_gap`。

| 指标 | 结果 |
| --- | ---: |
| Source 覆盖率 | 100% |
| Symbol 召回率 | 100% |
| Core Relation 召回率 | 100% |
| Known-gap Relation 召回率 | 0% |
| 总体 Relation 召回率 | 60% |
| 模块归属准确率 | 100% |
| Citation 落地准确率 | 100% |
| 确定性重放 | 100% |
| 单文件更新页面比例 | 20% |

保留的失败为 Python 跨文件 import/call、Go 参数接收者方法调用和 TypeScript 局部变量方法调用。
复现命令：

```bash
.venv/bin/python demo/run_code_wiki_benchmark.py \
  --workdir /private/tmp/memoryforge-code-wiki-c0 \
  --output /private/tmp/code_wiki_public.json
```

完整结果位于 [`demo/results/code_wiki_public.json`](../demo/results/code_wiki_public.json)。
