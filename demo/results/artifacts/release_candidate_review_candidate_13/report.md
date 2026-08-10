# 代码评审报告

- 仓库：MemoryForge-v030-release-candidate
- 检测模式：通用检测
- 检测范围：569685c2f0bf790819820b821b4768d180c4ee0d...2fff47c47f0cc3c4bbb98eb02c0acb77bce1718e
- 生成时间：2026-08-10 12:12
- 检查文件：172
- 变更行数：39853

## 缺陷统计

- P0：0
- P1：5
- P2：0
- 合计：5

## 缺陷详情

### 1. [P1][业务语义问题] POSIX 门禁把测试结果与另一份 Commit 快照拼成同一 Evidence

- 位置：`scripts/check_local.sh:62-67`
- 置信度：10/10

**问题描述**

完整 pytest 在调用者实时 worktree 中执行，随后才用 git worktree add HEAD 创建构建快照，且没有 clean-worktree 前置检查。未提交修复可让测试通过，而 Wheel/sdist 仍来自旧 HEAD；有限 release smoke 未必覆盖该回归，最终 Evidence 会把两套源码错误归因到同一 Commit。

**修复建议**

在任何检查前要求 git status --porcelain 为空并固定 HEAD；更稳妥的是在固定 Commit 快照中执行 Ruff、Mypy、Registry 和完整 pytest，结束时复核 Commit 与清洁状态。

---

### 2. [P1][业务语义问题] POSIX 门禁接受绑定到另一 checkout 的 Python 环境

- 位置：`scripts/check_local.sh:14-18`
- 置信度：10/10

**问题描述**

脚本只把 PYTHON_BIN 转成绝对路径，不验证该解释器中的 editable memoryforge 指向当前仓库。Candidate13 Linux 门禁实测使用前一 checkout 的 .venv；625 tests 通过，但 coverage 路径来自旧 checkout。当源码不同时，完整 pytest 可验证旧源码，却把结果登记给新 HEAD。

**修复建议**

固定 HEAD 快照后，让 pytest 显式从该快照的 src 导入，或在专用环境安装该快照；门禁开始时断言 memoryforge.__file__ 位于被测快照。

---

### 3. [P1][安全漏洞] POSIX 门禁仍允许 UV_EXTRA_INDEX_URL 覆盖固定包源

- 位置：`scripts/check_local.sh:71-74`
- 置信度：10/10

**问题描述**

新增代码只设置 UV_DEFAULT_INDEX，并使用 --no-config --default-index；--no-config 不禁用 UV_EXTRA_INDEX_URL 和 UV_FIND_LINKS。额外索引可提供 constraints 固定版本的替代 build/hatchling，在构建前执行并被后续 SHA256 当作发布 Evidence。

**修复建议**

构造只保留明确 allowlist 的子进程环境，删除全部 PIP_*/UV_* 后再设置唯一索引；uv 调用同时显式禁止额外 index/find-links。

---

### 4. [P1][健壮性问题] Workspace drill 继承全局签名与 hook 配置，固定身份在常见环境失效

- 位置：`demo/run_release_workspace_drill.py:371-383`
- 置信度：10/10

**问题描述**

fixture git commit 继承用户级 commit.gpgSign、core.hooksPath 与 init.templateDir。强制签名但无私钥时会确定失败；pre-commit hook 还可改写 fixture，使 repository_commit、repository_id、source_id 偏离 revision 14 固定身份。

**修复建议**

清除全部 GIT_*，设置 GIT_CONFIG_NOSYSTEM/GIT_CONFIG_GLOBAL，并在命令行固定 commit.gpgSign=false、core.hooksPath=<null-device> 及 author/committer 身份。

---

### 5. [P1][安全漏洞] 拒绝 Evidence 条目中的未声明字段

- 位置：`demo/validate_benchmark_registry.py:1802-1804`
- 置信度：10/10

**问题描述**

Registry artifact 只经通用 path/sha256 校验，额外键不会被拒绝或扫描。给 Candidate13 Evidence 增加 authorization: Basic dXNlcjpwYXNz 后，validate_registry_payload 仍返回 valid，凭证可进入公开 Registry。

**修复建议**

按 Evidence 状态构造允许字段集合并要求 set(artifact) 完全相等；对 sidecar 分别执行精确 schema 校验或统一隐私扫描。

---
