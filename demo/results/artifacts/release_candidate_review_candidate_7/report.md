# 代码评审报告

- 仓库：MemoryForge
- 检测模式：通用检测
- 检测范围：569685c2f0bf790819820b821b4768d180c4ee0d...a044337347b9c6884ea660c7568c4e3911c84521
- 生成时间：2026-08-08 09:59
- 检查文件：79
- 变更行数：14125

## 缺陷统计

- P0：0
- P1：5
- P2：0
- 合计：5

## 缺陷详情

### 1. [P1][业务语义问题] worktree 换行转换让同一 Commit 产出不同发布制品

- 位置：`scripts/build_release.py:50-52`
- 置信度：10/10

**问题描述**

这里通过 `git worktree add` 物化源码，但仓库没有 `.gitattributes`，因此文件字节受调用仓库的 `core.autocrlf` 影响，后续 Hatch 直接打包工作树字节。实测同一 a044337：LF checkout 连续两次均得到 Wheel fd3a0ab7...、sdist 2cbe6178...，CRLF checkout 连续两次均得到 Wheel c4aab138...、sdist 1e756085...；两边各自都能通过当前双构建相等检查并写出 `reproducible_artifacts=true`，但同一 Commit 的发布制品身份不同。Windows 常见的 `core.autocrlf=true` 即可触发。

**修复建议**

固定发布输入的 EOL（例如提交 `.gitattributes` 为文本文件声明 `eol=lf`），或用禁用 checkout 转换的 Git blob/archive 物化快照；增加 `core.autocrlf=false/true` 两种临时 checkout 的跨配置哈希一致性测试。

---

### 2. [P1][业务语义问题] Retained benchmark summary 只校验容器形状，伪造内容仍可通过

- 位置：`demo/validate_benchmark_registry.py:2247-2254`
- 置信度：10/10

**问题描述**

`_validate_release_development_artifacts()`依赖此函数证明 retained summary 的语义，但这里仅要求 macro/negative_results 非空以及 suites/experiments 长度为 12/8。实测把实际 summary 的首个 suite、experiment、negative result 和整个 macro 替换为伪造对象后，`_release_summary_contract()`仍返回 True，因此错误的 Evidence、split、Commit 和 SHA256 内容可以被 Registry 接受。

**修复建议**

针对 summary 内 macro、suite、experiment 和 negative-result 条目执行严格 schema 与身份校验；历史 summary 应按其生成 Commit 的 Registry 快照重建并逐字段比较，或固定该 Commit 对应的完整身份合同。

---

### 3. [P1][业务语义问题] Confirmation 总数信任组件自报 case_count，未与被哈希文件闭合

- 位置：`demo/validate_benchmark_registry.py:4163-4167`
- 置信度：10/10

**问题描述**

`_validate_artifact()`只验证组件文件路径和摘要，随后直接累加 confirmation manifest 自报的 case_count。实测只把首组件 case_count 从 5 改为 500、保持组件路径和 SHA256 不变，函数返回 521；只要 Registry 外层也写 521，就能用未实际存在的 confirmation cases 通过验证。

**修复建议**

在验证每个 component 的 SHA256 后解析其 payload，按实际 cases 长度计算数量，并校验组件 ID 唯一且与固定七组件集合一致。

---

### 4. [P1][业务语义问题] artifact 闭包把符号链接当作自有文件

- 位置：`demo/run_release_candidate_benchmark.py:374-382`
- 置信度：10/10

**问题描述**

将顶层 Wheel/sdist 替换为指向 reproducibility-first 资产的符号链接后，_check_reproducible_artifacts 仍返回 passed；再加入指向 /private/tmp 的未登记目录符号链接也仍通过。path.is_file() 会跟随文件链接且忽略目录链接，因此门禁既未证明资产所有权，也未关闭私有路径入口。

**修复建议**

使用 lstat 拒绝 release_dir 内的任何符号链接，并枚举全部目录项而非仅 path.is_file()；要求每个预期资产都是自有普通文件。

---

### 5. [P1][业务语义问题] 查询门禁不验证答案且未比较恢复前后的重放结果

- 位置：`demo/run_release_workspace_drill.py:174-179`
- 置信度：10/10

**问题描述**

当前条件只要求两次 status 都是 answered 且 citations 非空。原查询返回错误签名和 citation-a、恢复后返回不同答案和 citation-b 时该表达式仍为真，因此错误回答和非确定性恢复重放都会被记为 passed。

**修复建议**

验证答案包含固定的 cache_ttl 签名并校验引用的 source/locator/quote；对原 Workspace 与 restored Workspace 的规范化查询 payload 做精确相等或摘要比较。

---
