# Changelog

<!-- memoryforge-release-claim: version=0.3.0; status=release_candidate; active_candidate=12; platform_gate_candidate=12; platform_gate_status=accepted; review_status=rejected; macos_passed=623; linux_passed=620; linux_skipped=3; windows_confirmation=not_run; confirmation=not_run; holdout=not_run -->

## v0.3.0 — Release Candidate

- 关闭 GitHub Actions 并移除 hosted CI/Release workflow；
- 新增本地零付费质量门禁，覆盖 Ruff、Mypy、pytest、Wheel/sdist 双 clean-room 和 SHA256。
- 建立 12 Suite / 8 Experiment 的严格 Benchmark Registry，分离 package version、
  suite revision 与 Evidence revision，并保留全部 rejected/superseded Evidence；
- 增加 SQLite FTS5 Wiki Fact 索引、精确 Code Index 路由、覆盖式多来源选择与可解释
  support score；development Answer/Selective Accuracy 为 100%，Coverage 为 90%，Risk 为 0%；
- 增加递归 Folder Import、单个公开 GitHub Issue/PR Thread Import 与离线 JSON 重放；
- 增加零 Key 只读静态 Showcase，展示 SourceVersion、Wiki、ChangeSet、Citation Trace、
  Benchmark、拒答案例与 Mermaid 架构；
- 增加 POSIX `fcntl` / Windows `msvcrt` 锁边界、PowerShell 本地门禁与 Linux clean-room
  Evidence；原生 Windows confirmation 仍冻结为 `not_run`；
- 增加双隔离 release build、Workspace backup/restore drill、benchmark summary 与本地
  provenance；Candidate 3 门禁在 macOS 为 586 passed、Linux 为 583 passed / 3 skipped，
  Wheel/sdist 跨平台 SHA256 一致；retained artifacts 不进入 sdist。
- 强化 release gate：核验实际 Wheel/sdist metadata 和 bytes，增加独立 sdist clean-room 与
  atomic publish；保留 macOS 路径 alias 导致的 preflight 失败。
- 保留 Candidate 4 文档门禁负结果；修复当前 RC claim 校验误扫历史平台 Evidence 的问题，
  不删除或改写旧的 macOS/Linux 计数。
- Candidate 5 development 6/6；macOS 586 passed、Linux 583 passed / 3 skipped，
  coverage 均为 88%，Wheel/sdist 跨平台 SHA256 一致。
- Candidate 5 最终静态审查发现 0 P0 / 10 P1 / 2 P2，结果 rejected；原始与 Top 5 报告均保留。
- Candidate 6 development 6/6；构建改用 detached Commit 快照，并严格绑定 package、
  provenance、summary、SHA256SUMS、retained artifacts、Workspace privacy 与 Registry Commit。
- Candidate 6 本地门禁在 macOS 为 594 passed、Linux 为 591 passed / 3 skipped，
  coverage 均为 88%，Wheel/sdist 跨平台 SHA256 一致。
- Candidate 6 最终静态审查发现 0 P0 / 9 P1 / 3 P2，结果 rejected；原始与 Top 5 报告均保留。
- Candidate 7 固定 package metadata/sdist 输入与构建 epoch，并要求 development 与 local-gate
  Wheel/sdist SHA256 完全一致；development 6/6 通过。
- Candidate 7 macOS 首次门禁为 598 passed / 1 failed，因旧测试仍要求已移除的
  `sdist.exclude` 合同而 rejected；Linux 未运行，失败 Evidence 已保留。
- Candidate 7 修复测试合同后本地门禁通过：macOS 599 passed，Linux 596 passed / 3 skipped，
  coverage 均为 88%；两端 Wheel/sdist 与 development 制品逐字节一致。
- Candidate 7 静态终审发现 0 P0 / 10 P1 / 3 P2，结果 rejected；原始 13 条、Top 5 与固定
  Commit 范围报告均保留。
- Candidate 8 development 6/6；固定 checkout EOL，闭合 Summary/manifest/case identity，
  拒绝 symlink，并真实验证 Workspace answer、Citation、unknown 与 backup/restore replay。
- Candidate 8 本地门禁通过：macOS 604 passed，Linux 601 passed / 3 skipped；coverage 88%，
  package bytes 与 development 一致，双平台 retained SHA256SUMS 可原位重放。
- Candidate 8 静态终审发现 0 P0 / 4 P1 / 2 P2，结果 rejected；全部 6 条发现与固定 Commit
  范围报告保留。
- Candidate 9 development 6/6；拒绝 Summary schema 降级与布尔 schema 别名，复算历史评审
  范围，隔离版本探针，并保留、哈希和验证完整 Code Wiki Evidence。
- Candidate 9 本地门禁通过：macOS 607 passed，Linux 604 passed / 3 skipped；coverage 88%，
  Wheel、sdist 与 raw Code Wiki Evidence 跨平台 SHA256 一致。
- Candidate 9 静态终审发现 0 P0 / 10 P1 / 4 P2，结果 rejected；全部 14 条与固定 Commit
  范围报告保留，confirmation/holdout 未运行。
- Candidate 10 development 生成 Summary schema 3 与 Workspace drill schema 2，但旧 consumer
  仍要求 drill schema 1，结果 5/6 rejected；完整 artifacts 与失败 Evidence 保留。
- Candidate 11 development 6/6；consumer 复用 schema-aware Registry contract，保留可重算
  answer/Citation/unknown/replay、完整 experiment identity 与显式 review 状态。
- Candidate 11 本地门禁通过：macOS 609 passed，Linux 606 passed / 3 skipped；coverage 88%，
  Wheel、sdist 与 raw Code Wiki Evidence 跨平台同字节。
- Candidate 11 静态终审发现 0 P0 / 8 P1 / 4 P2，结果 rejected；12 条独立根因与固定
  Commit 范围报告保留。
- Candidate 12 development 6/6；关闭 package metadata、Registry-to-Commit、Workspace
  verify Evidence、跨平台隐私路径、clean-room import、Git fixture identity、artifact root 与
  passed-review 状态合同。
- Candidate 12 本地门禁通过：macOS 623 passed，Linux 620 passed / 3 skipped；coverage 88%，
  Wheel、sdist 与 raw Code Wiki Evidence 跨平台同字节。
- Candidate 12 静态终审发现 0 P0 / 9 P1 / 7 P2，结果 rejected；20 条 raw findings、16 条
  去重后独立根因、Top 5 和固定 Commit 范围报告均保留。
- 强化回答准确率契约，要求关键事实和冻结来源同时命中；AgentSkill-Eval 30 题严格 Answer 为
  96.7%，Citation grounding 为 100%，Source recall@3 为 96.2%；
- 追加独立 Click 20 题复评；回答准确率 5.0%、来源召回 16.7%，再次确认外部有效性缺口；
- 修复离线 semantic retrieval 实验调用 `_candidate_pages` 时的陈旧签名；
- 拒绝未通过 MkDocs confirmation 的英文页面 rank-fusion 候选，生产查询保持不变；
- 增加纯标准库 document-frequency 页面路由；Uvicorn development 100%，Typer confirmation
  83.3%，旧 30 题检索与 Citation 指标无回退。
- 增加候选页内英文词形匹配和 page-aware fact 排序；Watchfiles grounded Answer 从 20% 提升至
  80%，Structlog confirmation 为 66.7%，Citation grounding 均为 100%。
- 关闭只读 Workspace 与 Source Manifest 校验连接，并让本地门禁拒绝未关闭资源警告。

Release Candidate 尚未完成原生 Windows confirmation、单次 holdout、最终 tag 与 Release 上传。

## v0.2.1 — 2026-08-06

- 合并多仓库 Code Wiki、会话上下文和大仓库刷新改进；
- 受限修复中文否定同义改写的 Citation 选择，不放宽无答案问题；
- 固定 Click 20 题外部评测：Citation grounding 100%，但 Answer accuracy 仅 10%/0%，
  Source recall@3 为 0%，保留为外部有效性负结果；
- 原 AgentSkill-Eval 30 题 Answer/Citation 为 100%，Source recall@3 保持 96.2%。
- 新增 annotated tag 发布工作流，冻结 Hatchling 构建环境并校验 Wheel/sdist、SHA256、
  公开仓库 artifact attestation、Release 上传和远端资产回下载；个人私有仓库在 provenance
  明确标记 attestation 不适用。

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
