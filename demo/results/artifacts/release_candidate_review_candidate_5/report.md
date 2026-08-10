# 代码评审报告

- 仓库：MemoryForge
- 检测模式：通用检测
- 检测范围：origin/main...26767333bc20a6367bc87f239cdc956cd40e7f4e
- 生成时间：2026-08-08 07:36
- 检查文件：48
- 变更行数：7835

## 缺陷统计

- P0：0
- P1：5
- P2：0
- 合计：5

## 缺陷详情

### 1. [P1][业务语义问题] Benchmark summary 只校验列表类型，可静默删除全部 suite 与负结果

- 位置：`demo/run_release_candidate_benchmark.py:269-280`
- 置信度：10/10

**问题描述**

_check_benchmark_summary 只绑定 header、Commit、聚合 registry，并要求 suites/negative_results 是 list；它既不校验列表内容，也不要求 experiments 或 macro，空列表仍可通过。

**修复建议**

用同一确定性生成逻辑构造当前 Commit 的完整 expected summary，并逐字段比较 macro、suites、experiments、negative_results 与 registry。

---

### 2. [P1][逻辑错误] SHA256SUMS 被当作不透明文件，错误清单可通过且缺失时不产出分类 Evidence

- 位置：`demo/run_release_candidate_benchmark.py:576-588`
- 置信度：10/10

**问题描述**

development checks 未解析 SHA256SUMS；任意错误内容不会令 gate 失败，文件缺失则 _release_artifact_evidence 抛出 FileNotFoundError，导致 per-case 分类和输出文件丢失。

**修复建议**

解析 SHA256SUMS，要求条目集合、相对路径和实际 digest 精确匹配；缺失或格式错误应返回稳定分类并保留失败 Evidence。

---

### 3. [P1][业务语义问题] 开发产物校验未证明两次构建可复现

- 位置：`demo/validate_benchmark_registry.py:1924-1948`
- 置信度：10/10

**问题描述**

函数只分别验证文件自声明哈希和大小，不比较两组 build 与最终 package；package 路径可逃出 artifact_root，无关 SHA256SUMS 也可通过。

**修复建议**

对 package/build/record 使用精确 schema；约束全部路径在 artifact_root；要求 first、second 与最终 package 的 Wheel/sdist SHA256 相等，并解析 SHA256SUMS。

---

### 4. [P1][逻辑错误] Release acceptance 提前返回导致 Registry Commit 未绑定 payload

- 位置：`demo/validate_benchmark_registry.py:2827-2833`
- 置信度：10/10

**问题描述**

wrapper 校验 Registry acceptance Commit 后提前调用专用函数，未把该 Commit 传入；专用函数改用 payload 自报 Commit，因此可把门禁归属到未执行门禁的 Commit。

**修复建议**

把 acceptance artifact Commit 传入 release 专用验证器并要求 payload.memoryforge_commit 严格相等。

---

### 5. [P1][业务语义问题] Evaluator 未执行 builder 定义的 provenance schema

- 位置：`demo/run_release_candidate_benchmark.py:369-390`
- 置信度：10/10

**问题描述**

删除 provenance 的 schema_version、runtime、dependencies 后 evaluator 仍返回 6/6，Registry 保存的 release_artifacts 也无法恢复这些契约。

**修复建议**

复用精确 release-provenance schema，校验顶层键、schema_version、runtime、dependencies 与 checks，并在 release_artifacts 中绑定 provenance 摘要。

---
