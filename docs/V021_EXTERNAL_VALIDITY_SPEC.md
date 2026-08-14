# MemoryForge v0.2.1 External Validity & CI Hardening SPEC

## 1. 状态与目标

- 状态：v0.2.1 已发布；其 hosted workflow 后续已由本地免费工具链替代
- 基线：`v0.2.0@e7aef45e63018fdb795223b77384c84dbb06dff6`
- 集成分支：`release/v0.2.1`
- 目标版本：`v0.2.1`

v0.2.1 不扩张产品边界。它验证 Code Wiki 和文档 Wiki 能否在内置夹具之外稳定工作，并收敛
CI、依赖和发布链路。

## 2. Ponytail 决策

- 复用现有 CLI、Benchmark、release check 和 Evidence schema；
- 先测真实仓库，再决定是否增加缓存；
- 保持 SQLite、Markdown、Tree-sitter 和本地 CLI 架构；
- 不新增数据库、服务、前端、向量检索、语言 Adapter 或模型调用；
- 不为单道失败题增加专属同义词或路由规则；
- 负面外部结果同样提交，不删除难例。

## 3. Phase 0：基线冻结

必须保存：

- v0.2.0 tag、Commit、Release URL 和 CI run；
- Wheel、公开文档 Evidence、代码 Wiki Evidence、评测题集 SHA256；
- 文档 Wiki 与代码 Wiki 当前指标；
- 已知失败与边界。

产物：`demo/evaluation/v021_baseline.json`。

## 4. Phase 1：CI 与依赖

实施：

1. `push` 仅在 `main` 运行，PR 使用 `pull_request`；
2. 使用 workflow/ref concurrency，取消同一分支旧运行；
3. 使用 Node 24 版 `actions/checkout` 和 `actions/setup-python`；
4. 使用通用 `constraints/dev.txt` 固定开发与 CI 依赖；
5. CI 继续覆盖 Linux Python 3.11/3.12/3.13 与 macOS Python 3.12；
6. Python 3.11 继续执行 Wheel clean-room；
7. 同时验证 sdist 构建与安装。

门禁：

- 每个 PR 只产生一套四矩阵 CI；
- 无 Node 20 弃用警告；
- 所有 Python/OS 使用同一约束文件；
- Wheel 和 sdist 均可安装并通过 `pip check`。

## 5. Phase 2：真实 Code Wiki Benchmark

候选仓库：

- Python：`pallets/click`
- Go：`spf13/cobra`
- TypeScript：`colinhacks/zod`

仓库必须先通过许可证、固定 Commit、文件规模和生成目录审计。每个仓库只选择明确源码子目录，
排除 vendor、build、dist、generated 和 fixture 快照。

固定 Commit、许可证哈希、源码路径与文件数见
`demo/evaluation/external_code_wiki_sources_v021.json`。

每个仓库至少冻结：

- 20 个 Symbol expectation；
- 15 个 Import/Call/Method Relation expectation；
- 5 个 Module assignment expectation；
- 20 条实际 Relation precision 抽样。

硬门禁：

- Parser coverage 不低于 99%；
- Citation grounding 和 deterministic replay 为 100%；
- v0.2.0 C0/C4 固定指标保持 100%；
- 单文件增量页面比例不高于 25%；
- 两次完整 Evidence 字节一致。

Symbol/Relation 召回与 precision 必须完整报告。未达目标时允许发布负面结论，但禁止宣称广泛支持。

预期产物：

- `demo/evaluation/external_code_wiki_{click,cobra,zod}_v021.json`
- `demo/results/external_code_wiki_v021.json`
- `docs/EXTERNAL_CODE_WIKI_BENCHMARK.md`

当前状态：人工正样本标签、Runner 和基线 Evidence 已完成。60 个 Symbol、45 条 Relation、
15 条 Module assignment 均命中；该结果不代表全仓 precision/recall。单文件测试中 Click 和
Zod 的变化页面占比分别为 5.88% 和 2.94%，Cobra 因当前仅 2 个 Module 而达到 50%，未通过
25% 门禁。Relation precision 仍未完成。

## 6. Phase 3：性能决策

记录：

- Source 扫描、Tree-sitter parse、resolver、ModulePlan、Wiki compile 耗时；
- 冷启动总耗时；
- 单文件更新耗时；
- 峰值 RSS；
- Source、Symbol、Relation、Module 数量。

只有最大仓库的单文件更新同时超过冷启动耗时 50% 且超过 10 秒，才允许实现文件级解析缓存。
缓存只能按 Blob SHA、语言和 parser version 复用不可变代码事实。未触发阈值时输出 `NO_CHANGE`。

当前状态：已在 clean Commit `d90a129` 上完成三仓各 3 次 profile。最大仓库 Zod 的冷启动和
单文件更新中位数分别为 5.40 秒和 6.40 秒；更新比例为 118.5%，但未超过 10 秒，因此缓存门禁
未触发，决策为 `NO_CHANGE`。完整结果见
`demo/results/external_code_wiki_profile_v021.json`。

## 7. Phase 4：第二套文档评测

要求：

- 选择与 AgentSkill-Eval 无关的公开技术仓库；
- 至少 20 道题；
- 覆盖 single source、multi source、unanswerable、paraphrase；
- 修改查询逻辑前冻结题目与 Source expectation；
- 分离 development 与 holdout；
- 只有至少三个独立失败共享同一根因时才修改检索。

v0.2.0 的 30 题基线不得回退。新题集允许负面结果，但 Citation integrity 必须为 100%。

当前状态：Click 公开文档 development/holdout 各 10 题已冻结并在 clean Commit `293bd8f`
复跑。Answer accuracy 分别为 10.0% 和 0.0%，Source recall@3 均为 0.0%，Citation grounding
均为 100.0%。原 AgentSkill-Eval 30 题 Answer/Citation 为 100%，Source recall@3 保持
v0.2.0 的 96.2%，其余核心指标无回退。holdout 已揭盲，本阶段不据此调参；完整结果见
`docs/CLICK_DOCS_BENCHMARK.md`。

## 8. Phase 5：发布自动化

Tag workflow 必须：

1. checkout exact tag；
2. 从约束文件安装构建环境；
3. 构建 Wheel 和 sdist；
4. 在全新环境分别安装；
5. 运行 release check；
6. 生成 SHA256 和 tag-aligned provenance，公开仓库再生成 GitHub artifact attestation；
7. 上传 Release assets；
8. 下载远端资产并校验 SHA256。

provenance 的 Commit 必须等于 annotated tag 解引用 Commit。

历史状态：v0.2.1 使用 tag workflow 完成了 annotated tag/version 校验、冻结 Hatchling 的无隔离
构建、Wheel/sdist 双 clean-room、SHA256、Release 上传和远端资产回下载校验。发布完成后仓库关闭
GitHub Actions 并移除 workflow；后续版本使用 `scripts/check_local.sh` 生成本地 Evidence，再手动
上传 Release 资产。

## 9. Phase 6：公开展示材料

更新：

- `docs/EVIDENCE_CLAIMS.md`
- `docs/PORTFOLIO_DEMO.md`
- `docs/RESUME.md`
- `docs/BENCHMARK.md`
- `CHANGELOG.md`

每条项目主张必须标明 Evidence、指标口径和不能外推的边界。演示继续使用终端与 committed
Evidence，不新增 Web UI。

当前状态：`docs/EVIDENCE_CLAIMS.md` 已建立；`PORTFOLIO_DEMO.md`、`RESUME.md`、`BENCHMARK.md`
和 `CHANGELOG.md` 已同时记录固定领域强结果、Click 外部负结果与发布边界。版本号已对齐为
`0.2.1`，等待合并后的 `main` 创建 annotated tag。

首次 tag 运行证明 GitHub 不向个人私有仓库提供 artifact attestations。发布流程不会擅自改变仓库
可见性：公开仓库强制执行 attestation；私有仓库在 provenance 标记
`not_applicable_private_repository`，Wheel/sdist、SHA256 与远端回下载校验仍为强制门禁。

## 10. 条件功能

影响分析不属于 v0.2.1 必做项。只有 Phase 1-5 全绿且不会延迟发布时，才允许复用
`CodeRelation + ModulePlan` 增加确定性 downstream 查询；否则顺延 v0.3.0。

## 11. 完成定义

- CI 仅运行一套四矩阵且无弃用警告；
- 开发依赖可冻结重建；
- 三个真实代码仓库结果已提交；
- 第二套文档评测结果已提交；
- 性能决策有证据，未进行推测性缓存；
- v0.2.0 指标无回退；
- Wheel、sdist、SHA256、provenance 与 tag 对齐；
- `main`、annotated tag、GitHub Release 和下载资产均已核对；
- 工作树干净。
