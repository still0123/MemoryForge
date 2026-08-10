# 代码评审报告

- 仓库：MemoryForge
- 检测模式：通用检测
- 检测范围：569685c2f0bf790819820b821b4768d180c4ee0d...f4dde0904e5bcaeb78be6d7a32e74a6beae5679a
- 生成时间：2026-08-08 11:00
- 检查文件：97
- 变更行数：17804

## 缺陷统计

- P0：0
- P1：4
- P2：1
- 合计：5

## 缺陷详情

### 1. [P1][业务语义问题] Candidate 8 的 Summary schema 2 可被降级为丢失关键身份的 schema 1

- 位置：`demo/validate_benchmark_registry.py:2519-2521`
- 置信度：10/10

**问题描述**

该合同直接信任 payload 自报版本，并用同一版本重建 expected。实测对 Candidate 8 的 2451f2d Registry 快照分别生成 schema 1 和 schema 2，_release_summary_contract() 对两者均返回 True。schema 1 会删除 accepted_evidence 的 acceptance path/SHA256/Commit，并删除 negative/review sidecar 的 memoryforge_commit，因此 revision 9 仍可用旧的有损摘要通过 retained support 语义校验。

**修复建议**

按生成 Commit 或 development evidence revision 固定期望 schema；Candidate 8/revision 9 必须为 schema 2，仅对已保留的旧 Commit 允许 schema 1。

---

### 2. [P1][健壮性问题] 严格 Registry 校验接受布尔 schema_version

- 位置：`demo/validate_benchmark_registry.py:1291-1293`
- 置信度：10/10

**问题描述**

Python 中 True == 1，这里的宽松比较会把 JSON schema_version=true 当成版本 1。实测仅把当前 Registry 根字段改为 true 后调用 validate_registry()，函数仍返回 status=valid，违反本次 validator 其余 release/Evidence 合同采用的严格 JSON 类型语义。

**修复建议**

先要求 type(registry.get("schema_version")) is int，再比较其值为 1；为根 Registry schema 增加布尔别名负向测试。

---

### 3. [P1][健壮性问题] 版本探针继承 coverage 插桩环境并污染 clean-worktree 门禁

- 位置：`demo/run_release_candidate_benchmark.py:743-765`
- 置信度：10/10

**问题描述**

_source_module_version 和 _cli_version 直接复制 os.environ。项目安装的 pytest-cov/coverage .pth 会在存在 COV_CORE_* 或 COVERAGE_* 时对子进程自动插桩；实测这两个探针会生成并行 coverage 数据文件。若 COV_CORE_DATAFILE 使用常见的相对值 .coverage，文件会写入固定 cwd=REPO_ROOT，且 .gitignore 只忽略 .coverage、不忽略 .coverage.*，于是同一次 main 在末尾把 clean_worktree_after_run 判为 false。_check_versions 又在两次 suite run 中各执行一次，因此在 coverage 驱动的终审/组合测试环境中可稳定把有效候选误判为失败。

**修复建议**

为两个 Python 版本探针复用隔离环境构造：移除 PYTHONHOME、PYTHONPATH、COV_CORE_* 和 COVERAGE_*，再显式设置 PYTHONPATH=SOURCE_ROOT 与 PYTHONNOUSERSITE=1。

---

### 4. [P1][业务语义问题] 本地门禁 provenance 可删除绝大多数 Code Wiki 结果仍被 Registry 接受

- 位置：`demo/validate_benchmark_registry.py:2938-2962`
- 置信度：10/10

**问题描述**

Group 1 producer 固定校验并写出 12 项 Code Wiki metrics、完整 gates 和真实 incremental 结果，但 Group 2 consumer 只要求 metrics/gates 非空且值全为 100/true，并允许 changed_symbols、changed_pages 及其 expected 集合同时为空。仓库现有 test_benchmark_registry_hashes_bound_gate_artifact_bytes 已构造仅含 deterministic_replay 一个 metric、deterministic 一个 gate 和空增量集合的 provenance，_validate_bound_gate_artifacts() 仍返回 True；定向运行该测试为 1 passed。因此重算 provenance/SHA256SUMS 摘要后，缺失核心 symbol、relation、Citation、architecture 与增量覆盖 Evidence 的本地门禁仍可被 acceptance consumer 接受。

**修复建议**

按 run_release_check.py 的 CODE_METRICS、实际 gate 集合和固定 incremental 身份做精确键集与值校验；同时保留并验证 code_wiki.evidence_sha256 指向的原始 Evidence。

---

### 5. [P2][业务语义问题] 历史 review sidecar 未按固定 Commit 范围复算且当前变更行数不一致

- 位置：`demo/validate_benchmark_registry.py:1437-1449`
- 置信度：10/10

**问题描述**

这里仅把 sidecar 与另一组硬编码数字比较并检查祖先关系，没有从两个固定 Commit 复算范围。实际对 candidate 5/6 执行三点 diff 分别得到 +7835/-30（7865 行）和 +10918/-35（10953 行），但已认证 sidecar/report 声称 7835 和 10918；candidate 7 又按 additions+deletions 记录 14125。当前 validator 仍返回 valid，因此这些 sidecar 不能准确证明历史评审覆盖量。

**修复建议**

根据 sidecar 的 base_commit/source_commit 执行 merge-base-to-source numstat，并按原评审过滤规则复算 reviewed_files 与 additions+deletions 后再接受。

---
