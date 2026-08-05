# MemoryForge 简历表述

## 一行版本

独立设计并实现本地优先的个人技术 Wiki 编译系统，将 Git、文档和飞书资料编译为可审核 Markdown Wiki，通过渐进式检索与受限 Agent 输出可回溯答案。

## 推荐写法

**MemoryForge｜个人技术知识 Wiki 与 MiniClaude Agent**
Python · SQLite FTS5 · Pydantic · Typer · OpenAI-compatible API · 飞书

- 设计 Raw Source、Markdown Wiki、Workspace Schema 三层知识模型；实现 `ingest → review → apply` 审核式编译与增量更新，使资料变更只影响相关页面，并保留来源版本和字符级 Citation。
- 实现 `INDEX → Wiki 页面 → 原文证据` 的渐进式查询和受限 MiniClaude Agent；Agent 仅能检索、读取证据、回答，支持 3 轮本地追问，避免 Shell、任意写文件等通用工具带来的失控边界。
- 使用 Tree-sitter 构建 Python、Go、TypeScript/TSX 的确定性 Code Wiki；架构图边绑定真实
  `CodeRelation` Citation，固定夹具上的 Symbol、Relation、Module 和 Mermaid 指标均为 100%，
  单文件更新页面比例为 20%。
- 构建公开可复现评测：在 `AgentSkill-Eval` 56 个公开资料上生成 57 个 Wiki 页面，30 题中回答
  准确率 96.7%、Top-3 来源召回率 96.2%、多来源完整覆盖率 100%；相对 Raw FTS 基线的 57.7%
  来源召回，验证 Wiki 路由对跨资料问题的提升。

## 面试时的 30 秒介绍

我做的不是一个把文档塞进模型的聊天机器人，而是一个技术知识编译器。资料先经过可审核的 Wiki 编译，查询时从目录路由到少量页面，必要时才回到原文，所以每个回答都能指出来源。我的重点是把“知识沉淀、增量维护、可信问答”做成一条闭环；MiniClaude 和飞书只是这条闭环的交互入口。

## 容易被追问的指标解释

- **96.2% 来源召回**：26 道可回答题中，预期来源是否进入最终 Citation；多来源题要求全部来源。
- **96.2% 引用落地准确率**：有且仅有带证据的回答才计入正确；一条同义改写题没有选出 Citation，因此如实记为失败。
- **Raw FTS 57.7%**：对原始资料直接做任意词匹配和 BM25 Top-3；它只衡量检索，不生成答案，因此不与回答准确率混比。

完整演示顺序见 [秋招演示与面试说明](PORTFOLIO_DEMO.md)，指标方法见 [公开 Benchmark](BENCHMARK.md)。
