# 代码评审报告

- 仓库：MemoryForge
- 检测模式：通用检测
- 检测范围：569685c2f0bf790819820b821b4768d180c4ee0d...c23de5915e6237700c0aa8e03a14e44583ab1049
- 生成时间：2026-08-08 12:01
- 检查文件：114
- 变更行数：21288

## 缺陷统计

- P0：0
- P1：5
- P2：0
- 合计：5

## 缺陷详情

### 1. [P1][健壮性问题] 发布构建在创建隔离环境前依赖宿主运行时依赖

- 位置：`scripts/build_release.py:383-390`
- 置信度：10/10

**问题描述**

`main -> _build_release -> _require_version` 在两次 `_isolated_build` 之前调用这里的源码 CLI。`python -m memoryforge --version` 会导入 `memoryforge.cli` 及其运行时依赖；在仅含 CPython 3.11、尚未安装项目依赖的全新 venv 中可稳定因缺少 `typer` 退出，导致本应自行创建隔离构建环境的发布命令在构建开始前失败。

**修复建议**

不要通过完整 CLI 读取源码版本；使用 `ast` 解析 `src/memoryforge/__init__.py` 中的 `__version__` 并与 pyproject 比较，或在已安装固定依赖的隔离环境中执行版本探针。

---

### 2. [P1][业务语义问题] Workspace drill 丢弃原始语义结果，Candidate 9 只能复验自报的 passed 字符串

- 位置：`demo/run_release_workspace_drill.py:236-242`
- 置信度：10/10

**问题描述**

run_drill 已计算 query、unknown、restored_query、restored_unknown、restored_lint、cases、Showcase metrics 和输出摘要，但返回值只保留十个 passed/failed 字符串、泄漏计数和总 passed。build_release 随后删除临时 workdir，只保留这份 18 行 workspace-drill.json；_check_workspace_drill 和 _release_drill_contract 也仅断言这些字符串为 passed。因而 Candidate 9 的 retained Evidence 无法独立证明 exact signature、Citation locator/quote、SourceVersion=2、unknown abstention、backup/restore replay 或 Showcase case/metric 一致性，任何 producer 误判都会被下游原样认证为 6/6。

**修复建议**

在保留产物中写入并哈希规范化的原始/恢复后 answer、Citation、SourceVersion、unknown support、Workspace Commit、lint/no-pending 结果、最终 cases/metrics 及 Showcase 摘要；让 Registry validator 从这些字段重算各 check，而不是消费 passed 字符串。

---

### 3. [P1][业务语义问题] revision 10 raw Code Wiki Evidence 仍可用伪造语义内容通过闭包校验

- 位置：`demo/validate_benchmark_registry.py:3103-3112`
- 置信度：10/10

**问题描述**

新增 consumer 只要求 counts 非空、四个 case 组非空且每项 found=true，不验证 fixture/workflow、suite、count 值、case schema/ID/数量或指标与 cases 的对应关系。已在临时目录复制 Candidate 9 macOS 产物，将 fixture/workflow 置空、suite 改为 fabricated-suite、counts 改为 {fabricated:999}、每组 cases 缩成一个仅含 found=true 的对象，再同步重算 raw Evidence、provenance、SHA256SUMS 及 artifact_files 摘要；在 require_clean_sdist、require_replayable_sums、require_code_evidence 全开启时 _validate_bound_gate_artifacts() 仍返回 true。因此 producer 丢失绝大多数原始测试身份或保留内容被伪造时，revision 10 acceptance 仍会把它记为闭合 Evidence。

**修复建议**

对 fixture、workflow、evaluation.suite、counts 及四组 case 的精确键集、固定 ID 和预期数量建立严格合同，并从 cases 复算 counts/metrics，而不是只要求列表非空且 found=true。

---

### 4. [P1][业务语义问题] Candidate 8 到 9 的状态链丢失 review 维度

- 位置：`demo/evaluation/registry.json:1229-1259`
- 置信度：10/10

**问题描述**

Candidate 8 没有 regression_evidence，唯一失败是 passed=false 的 review_evidence，却被硬编码为 development_passed_regression_failed；紧接着 Candidate 9 仅凭 development 与 local-gate acceptance_evidence 就进入 accepted_development，机器记录没有 final review 的 pending 或 accepted Evidence。_benchmark_experiment_summary 会把 Candidate 9 发布进 accepted_evidence，而文档明确说明其 final review 尚未运行；同一状态机还把 Candidate 2/5/6 的 static_review_rejected sidecar 挂在 regression_evidence 下并在公开 summary 中输出为 regression_rejected。依赖机器状态的消费者因此无法区分“终审待运行”和“终审已通过”，并会漏掉或误分类历史 review rejection。

**修复建议**

将开发、local gate 和 final review 建模为正交状态；至少增加显式 review_pending/review_failed/review_accepted 状态并校验其 Evidence 身份，生成 accepted_evidence 时同时输出 review 状态。历史静态审查 sidecar 应统一挂到 review_evidence，而不是 regression_evidence。

---

### 5. [P1][安全漏洞] Registry 接受含私密路径的 retained provenance

- 位置：`demo/validate_benchmark_registry.py:2398-2402`
- 置信度：10/10

**问题描述**

development runner 会扫描 release-provenance.json 并以 private_detail_leaks == 0 作为通过条件，但 Registry 的 retained consumer 在这里仅要求 dependencies 为非空字符串映射，之后直接信任 Evidence 中已记录的零值。定向探针在 Candidate 9 retained provenance 的 dependencies 中加入 /Users/private/workspace，并同步更新 provenance_sha256、SHA256SUMS 及其摘要后，_validate_release_development_artifacts(..., evidence_revision=10) 仍返回 true，而现有 _payload_private_detail_leaks 返回 1。因此私密路径或凭证可在 producer 运行后被写入公开 retained artifact，仍穿过 Registry 并维持 accepted Evidence。

**修复建议**

在 _release_provenance_contract 中对完整 provenance 调用 _payload_private_detail_leaks 并要求结果为 0；不要仅检查 dependencies 的键和值是字符串。

---
