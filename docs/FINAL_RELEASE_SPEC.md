# MemoryForge v0.2.0 最终发布 SPEC

## 1. 目标与状态

- 状态：实施中
- 集成分支：`release/v0.2.0`
- 基线：`main@05b7ad5`
- 依赖提交：
  - C0 relation resolution：`b2e33e4`
  - C4 trusted architecture：`341b1e2`
- 目标标签：`v0.2.0`

本轮只把已经实现的文档 Wiki、代码 Wiki、可信架构图和公开评测收敛成一个可发布版本。
完成标准不是“本机能跑”，而是远端 CI、全新虚拟环境、公开 Evidence 和面试演示使用同一组
可验证事实。

## 2. Ponytail 决策

- 复用现有 CLI、Demo、Benchmark、CI 和审批链；
- 只增加一个薄的 release check 编排入口，不增加发布框架；
- 使用 Python 标准库创建临时虚拟环境、计算 SHA256 和写 provenance；
- clean-room 只安装 Wheel，不执行 editable install；
- 固定代码夹具负责无网络核心验证；
- 完整 30 题公开评测继续使用公开的
  `AgentSkill-Eval@93f5dc05229da250b041850ad8deeeec886ef304`；
- 不新增数据库、服务、前端、模型调用或付费实验。

## 3. 发布范围

v0.2.0 在 v0.1.0 基础上增加：

1. Python、Go、TypeScript/TSX 的 Tree-sitter 代码索引；
2. 稳定 Symbol、Relation、Module 和 ArchitectureGraph 身份；
3. Python 跨文件 import/call、Go 参数接收者方法、TypeScript 局部变量方法解析；
4. 确定性 ModulePlan；
5. 带代码 Citation 的 `PROPOSED → review → approve → apply` Code Wiki；
6. Relation evidence 支撑的 Mermaid 架构图；
7. 固定三语言 Benchmark、增量门禁和 release provenance。

## 4. P0/P1 阻塞定义

以下任一项失败都禁止发布：

- GitHub Actions 任一 Python/OS 矩阵失败；
- Wheel 无法在新虚拟环境安装或 `pip check` 失败；
- clean-room 实际 import 指向源码 checkout；
- `memoryforge --help` 或核心 CLI 路径失败；
- 公开 Demo 或 Code Wiki Benchmark 无法从空 Workspace 运行；
- Citation、SourceVersion、Relation 或 Architecture edge 无法回读；
- 双重放 Evidence 不一致；
- 固定题集任一既有 100% 指标回退；
- 单文件增量页面比例高于 25%；
- README、Benchmark、Changelog、简历材料和 Evidence 数字冲突；
- 仓库包含凭据、公司代码、飞书私有正文或伪造运行结果。

P2 功能、体验优化和未被现有门禁证明必要的重构不进入 v0.2.0。

## 5. Clean-room 契约

release check 必须：

1. 接收已构建的 `.whl`；
2. 在指定的空目录创建全新 `venv`；
3. 清除 `PYTHONPATH`，仅从 Wheel 安装 `memoryforge`；
4. 执行 `pip check`；
5. 记录 `memoryforge.__file__`，并拒绝其位于仓库 `src/`；
6. 执行 `memoryforge --help`；
7. 使用安装后的解释器运行固定 Code Wiki Benchmark；
8. 校验以下指标均为 100%：
   - Source coverage；
   - Symbol、Core Relation、原 Known-gap Relation、Overall Relation recall；
   - Module assignment；
   - Citation grounding；
   - Architecture edge grounding；
   - Mermaid edge、Architecture Citation、Mermaid determinism；
   - deterministic replay；
9. 校验 `changed_page_ratio <= 0.25`；
10. 输出不含时间戳和临时绝对路径的 `release_provenance.json`。

provenance 至少记录：

- Schema version；
- MemoryForge Commit 和版本；
- Wheel 文件名与 SHA256；
- Python 实现与版本；
- 平台；
- 实际 import 路径是否位于 venv；
- 执行过的稳定命令名；
- Code Wiki Evidence SHA256、指标、门禁和增量结果。

完整 30 题公开评测允许单独运行，因为它依赖另一个公开 checkout。复现者必须自行 clone 并
checkout 固定 Commit；release check 不隐式访问网络，也不把本机路径写入 Evidence。

## 6. 质量门禁

本地与 GitHub Actions 使用同一组核心命令：

```text
ruff check .
ruff format --check .
mypy
pytest -W error::ResourceWarning --cov=memoryforge --cov-report=term-missing
python -m pip check
build wheel
run release check from a fresh venv
run fixed benchmarks twice and compare bytes
git diff --check
```

CLI help 测试必须固定终端宽度，不能依赖开发机 TTY。

## 7. 文档与面试验收

以下文档必须使用相同指标和边界：

- `README.md`
- `CHANGELOG.md`
- `docs/BENCHMARK.md`
- `docs/PORTFOLIO_DEMO.md`
- `docs/RESUME.md`
- `demo/README.md`

3 分钟演示必须能完成：

1. 说明 `Source → ChangeSet → Wiki → Query/Agent` 主链；
2. 展示 `review → approve → apply`；
3. 展示一个带 Citation 的正常回答和一个拒答；
4. 展示代码 Wiki 的 Symbol、Relation、Mermaid edge 与真实 source locator；
5. 展示公开 Benchmark、clean-room provenance 和已知失败；
6. 明确当前不是通用编码 Agent、在线 SaaS、向量数据库或多租户系统。

所有简历数字必须能在提交的 JSON Evidence 中直接找到，不能把局部实验写成普遍结论。

## 8. 发布流程

1. 在 `release/v0.2.0` 完成修复、clean-room 和文档；
2. 推送分支并创建到 `main` 的 PR；
3. 等待全部 GitHub Actions 通过；
4. 审查 PR diff，确认仅包含 C0、C4 和发布收敛；
5. 合并 PR；
6. 在合并后的 `main` 再运行发布门禁；
7. 创建 annotated tag `v0.2.0`；
8. 发布 GitHub Release，并附 Wheel 与 SHA256；
9. 核对远端 `main`、tag、Release asset 和本地验证 Commit 一致。

## 9. 工程诚实性

- `worktree_dirty`、跳过项和平台差异必须原样记录；
- 公开评测保留 1 道同义改写失败，不为发布刷题；
- C0/C4 指标来自固定夹具，不能外推到任意语言或任意仓库；
- clean-room 证明 Wheel 可安装并跑通固定流程，不等价于生产 SLA；
- 若发布受权限或外部服务阻塞，保留已通过证据并明确标记 blocker。

## 10. 完成定义

只有同时满足以下条件，长期 Goal 才能关闭：

- `main` 包含经审核的 C0、C4 和 release readiness 改动；
- GitHub Actions 全矩阵通过；
- committed public Evidence 与 release provenance 可回读；
- v0.2.0 Wheel 在 clean-room 通过；
- README、Benchmark、Changelog、演示与简历主张一致；
- 远端 annotated tag 和 GitHub Release 可访问；
- 工作树干净，远端 SHA 已核对。
