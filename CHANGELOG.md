# Changelog

## Unreleased

- 合并多仓库 Code Wiki、会话上下文和大仓库刷新改进；
- 受限修复中文否定同义改写的 Citation 选择，不放宽无答案问题；
- 固定 Click 20 题外部评测：Citation grounding 100%，但 Answer accuracy 仅 10%/0%，
  Source recall@3 为 0%，保留为外部有效性负结果；
- 原 AgentSkill-Eval 30 题 Answer/Citation 为 100%，Source recall@3 保持 96.2%。
- 新增 annotated tag 发布工作流，冻结 Hatchling 构建环境并校验 Wheel/sdist、SHA256、
  artifact attestation、Release 上传和远端资产回下载。
- 追加独立 Click 20 题复评；回答准确率 5.0%、来源召回 16.7%，再次确认外部有效性缺口；
- 修复离线 semantic retrieval 实验调用 `_candidate_pages` 时的陈旧签名；
- 拒绝未通过 MkDocs confirmation 的英文页面 rank-fusion 候选，生产查询保持不变；
- 增加纯标准库 document-frequency 页面路由；Uvicorn development 100%，Typer confirmation
  83.3%，旧 30 题无回退；

## v0.2.0 — 2026-08-06

以可信 Code Wiki、稳定发布和独立复现为重点的第二个公开版本。

### 新增

- Python、Go、TypeScript/TSX Tree-sitter CodeMap，以及稳定 Symbol、Relation 和 Module 身份；
- 确定性 ModulePlan 与带代码 Citation 的审核式 Code Wiki；
- 基于真实 `CodeRelation` evidence 的 Mermaid 架构图，不允许模型补边；
- Python 跨文件 import/call、Go 参数接收者方法和 TypeScript 局部变量方法解析；
- Wheel clean-room 检查：全新 venv 安装、import 路径、依赖、CLI、双 Benchmark 和 SHA256
  provenance。

### 正确性修复

- 修复 Rich help 在 GitHub 窄终端中换行导致的跨平台测试误报；
- 强 INDEX 路由优先于宽松 FTS，弱 INDEX 仍让位于精确 FTS；
- 事实排序优先长标识符、明确 yes/no 条件和页面摘要，多来源选择优先覆盖尚未命中的英文标识符；
- 重新审计固定公开题集，将源文档中已有明确 Vue 证据的题目从“无答案”纠正为单来源题。

### 公开验证

- 30 题文档 Wiki：回答准确率 96.7%，Top-3 来源召回率 96.2%，Citation 96.2%，多来源覆盖和
  拒答准确率 100%；
- Raw FTS Top-3 来源召回率 57.7%，多来源完整覆盖 20.0%；
- 固定代码 Wiki：C0/C4 全指标 100%，单文件更新页面比例 20%；
- 375 个 pytest 用例、Ruff、Mypy、依赖检查和 Wheel clean-room 通过，总代码覆盖率 88%。

### 版本边界

仍不包含公网 SaaS、多租户、定时同步、向量数据库或通用编码 Agent。固定夹具和单个公开仓库的
结果不能外推为任意代码库或知识库上的普遍性能。

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
