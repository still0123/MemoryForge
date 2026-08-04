# Changelog

## v0.1.0 — 2026-08-04

首个可公开演示的版本，聚焦“个人技术 Wiki 如何持续更新、可靠查询并回到证据”。

### 已包含

- 多源资料导入：本地文件、已克隆 Git 仓库、飞书 Docx/Wiki 与公开网页；
- `WikiCompiler`、`review → apply` 审核流、增量路由与来源删除清理；
- `INDEX → Wiki 页面 → 原文证据` 的渐进式查询与可回溯 Citation；
- 有边界的 MiniClaude Agent：仅能搜索、读取证据、回答，支持最近 3 轮追问；
- 飞书私聊展示入口，以及模型繁忙时回退到确定性 Wiki 回答；
- 30 题公开评测和同题集 Raw FTS 对照实验。

### 公开验证

- `AgentSkill-Eval@93f5dc0`：56 个来源文件、57 个 Wiki 文件、30 道题；
- MemoryForge Top-3 来源召回率 96.0%，多来源完整覆盖 100.0%，回答准确率 96.7%；
- Raw FTS Top-3 来源召回率 56.0%，多来源完整覆盖 20.0%；
- GitHub Actions 同款命令通过：Ruff、Mypy、336 个 pytest 用例，覆盖率 87%。

完整实验方法与失败案例见 [公开 Benchmark](docs/BENCHMARK.md)。

### 版本边界

不包含公网部署、群聊/多租户、定时同步、向量数据库、知识图谱、MCP 或通用编码 Agent。它们不属于 v0.1.0 的核心问题。
