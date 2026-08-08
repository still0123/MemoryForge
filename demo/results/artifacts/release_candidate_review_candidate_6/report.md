# 代码评审报告

- 仓库：MemoryForge
- 检测模式：通用检测
- 检测范围：origin/main...9588c2fb6a41225515165f0114ce61f23f51d921
- 生成时间：2026-08-08 08:51
- 检查文件：62
- 变更行数：10918

## 缺陷统计

- P0：0
- P1：5
- P2：0
- 合计：5

## 缺陷详情

### 1. [P1][业务语义问题] Candidate 6 的开发与本地门禁绑定了不同发布制品

- 位置：`demo/validate_benchmark_registry.py:3385-3394`
- 置信度：10/10

**问题描述**

development 制品 ff9af054/4f65684f 与 local gate 制品 a0ddda54/dfb59503 不同，Registry 却合并为 accepted。

**修复建议**

要求 local-gate Wheel/sdist SHA256 与 development release_artifacts.package 严格相等。

---

### 2. [P1][业务语义问题] Registry 将 retained 支撑 JSON 的哈希闭包误当成语义闭包

- 位置：`demo/validate_benchmark_registry.py:2051-2063`
- 置信度：10/10

**问题描述**

三份支撑 JSON 可替换为只含最小字段的空壳并在重算摘要后通过 Registry。

**修复建议**

复用 producer/evaluator 精确验证 provenance、summary、drill 语义。

---

### 3. [P1][业务语义问题] SHA256SUMS 闭包未拒绝目录中的未登记文件

- 位置：`demo/run_release_candidate_benchmark.py:360-375`
- 置信度：10/10

**问题描述**

新增未写入清单的文件后 artifact gate 仍通过，发布目录可携带未登记私有资产。

**修复建议**

将实际文件集合、清单集合、固定 expected_files 三方精确比较，并禁止 output 位于 release_dir。

---

### 4. [P1][安全漏洞] 隐私门禁漏检 /private/tmp 绝对路径

- 位置：`demo/run_release_candidate_benchmark.py:644-668`
- 置信度：10/10

**问题描述**

debug_path=/private/tmp/private-workspace 不会被当前 prefixes 命中，private_detail_leaks 仍为 0。

**修复建议**

检测任意 POSIX/Windows 绝对私有路径并严格限制 workspace-drill schema。

---

### 5. [P1][逻辑错误] Commit 专属 artifact_root 可通过目录符号链接别名复用其他目录

- 位置：`demo/validate_benchmark_registry.py:1949-1950`
- 置信度：10/10

**问题描述**

预期 Commit 目录可为指向仓库内其他目录的 symlink，Registry 仍接受。

**修复建议**

拒绝 artifact_root 任一路径段为符号链接，再做 canonical containment。

---
