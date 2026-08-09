# 代码评审报告

- 仓库：MemoryForge-v030-release-candidate
- 检测模式：通用检测
- 检测范围：569685c2f0bf790819820b821b4768d180c4ee0d...e7d3fef9312b4b6c1683e12984ae00cd1b21b343
- 生成时间：2026-08-08 14:41
- 检查文件：154
- 变更行数：34294

## 缺陷统计

- P0：0
- P1：5
- P2：0
- 合计：5

## 缺陷详情

### 1. [P1][健壮性问题] pip 隔离模式使 sdist clean-room 约束文件失效

- 位置：`scripts/build_release.py:301-313`
- 置信度：10/10

**问题描述**

_check_sdist_clean_room 设置 PIP_CONSTRAINT 后又使用 pip --isolated；隔离模式忽略 PIP_* 环境选项，因此 hatchling 和 sdist 运行时依赖实际不受冻结约束。

**修复建议**

不要通过 PIP_CONSTRAINT 环境变量传约束；像 _isolated_build 一样，在两条 pip install 命令中显式加入 -c str(constraints)。

---

### 2. [P1][业务语义问题] Retained package bytes are not bound to the declared Commit

- 位置：`demo/validate_benchmark_registry.py:2406-2434`
- 置信度：10/10

**问题描述**

_package_archives_match 只比较 METADATA 与 PKG-INFO。Wheel 与 sdist 可包含不同源码，或包含不属于声明 Commit 的源码，只要同步更新自声明摘要仍会被 retained consumer 接受。

**修复建议**

比较 Wheel 与 sdist 的完整 memoryforge 源码成员，并把每个打包源码文件绑定到声明 Commit 的 Git blob；同时验证 Wheel RECORD。

---

### 3. [P1][业务语义问题] Published acceptance is not bound to the passed-review Commit

- 位置：`demo/validate_benchmark_registry.py:2582-2592`
- 置信度：10/10

**问题描述**

schema 3 发布只校验 development 与 acceptance_evidence ancestry，未校验 passed review Commit；在兄弟分支登记 accepted Evidence 时可能授权未被该 review 覆盖的源码。

**修复建议**

发布 accepted summary 时要求当前 Commit 同时后继 development、local-gate 和 passed-review 三个 Commit。

---

### 4. [P1][业务语义问题] Malformed final-review findings are treated as non-blockers

- 位置：`demo/validate_benchmark_registry.py:4150-4179`
- 置信度：10/10

**问题描述**

当前仅拒绝 severity 精确为 P0/P1/P2 的对象；缺失 severity、lowercase p1 和布尔计数可绕过，从而让损坏的 review sidecar 授权 accepted_development。

**修复建议**

严格验证 finding schema 与 severity 枚举，从 raw findings 重算 p0/p1/p2；passed review 遇到 malformed finding 必须 fail closed。

---

### 5. [P1][业务语义问题] Summary package version is outside the Commit snapshot binding

- 位置：`demo/build_benchmark_summary.py:43-51`
- 置信度：10/10

**问题描述**

Registry 已绑定声明 Commit，但 package_version 独立读取可变工作树；合法 Registry 可生成报告任意 package version 的 schema 3 summary。

**修复建议**

从声明 Git Commit 读取 pyproject.toml，并要求 project.version 等于 Registry package_release_target。

---
