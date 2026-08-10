# 代码评审报告

- 仓库：MemoryForge
- 检测模式：通用检测
- 检测范围：569685c2f0bf790819820b821b4768d180c4ee0d...be3560e37baa908e01a0404a88631ad1a1fa39d7
- 生成时间：2026-08-10 12:46
- 检查文件：189
- 变更行数：44569

## 缺陷统计

- P0：0
- P1：5
- P2：0
- 合计：5

## 缺陷详情

### 1. [P1][安全漏洞] [P1] 对通过态评审报告做内容级校验

- 位置：`demo/validate_benchmark_registry.py:4450-4458`
- 置信度：10/10

**问题描述**

新增 helper 只要求 HTML/Markdown 同时包含 scope 和“未发现缺陷”，未验证其他内容，也未调用 `_payload_private_detail_leaks`。可复现：两份报告都写入正确 scope 和“未发现缺陷”，再追加 `STATUS: FAILED`、`P1: 4`、`/Users/private/workspace` 或 `Authorization: Basic ...`，`_accepted_review_reports_valid` 仍返回 True。因此 Candidate 14 后续可被标为 accepted，同时保留明确失败、非零缺陷或敏感信息的公开归档；本次修复没有真正闭合报告一致性和隐私门禁。

**修复建议**

不要用两个肯定子串代表报告一致。至少拒绝 failed/P0/P1/P2 非零等矛盾状态，校验结构化状态、各级计数、base/source Commit 与 Evidence 完全一致，并对两份报告运行隐私/凭证扫描；更稳妥的是由已验证的结构化 Evidence 确定性生成归档后逐字比对。

---

### 2. [P1][健壮性问题] Code Wiki producer 只隔离写命令，读取 Commit 时重新继承 GIT_*

- 位置：`demo/run_code_wiki_benchmark.py:248-255`
- 置信度：10/10

**问题描述**

fixture 的 init/add/commit 已通过第 219 行 `_git` 使用清洁环境，但紧接着第 62、108 行调用此未隔离 helper 读取 initial/updated Commit。只要调用环境存在 GIT_DIR（run_release_check 的独立调用会原样传入），rev-parse 就会忽略 root fixture，读取外部仓库 HEAD；producer 因而把错误 Commit 写进 raw Evidence，随后固定身份 consumer 确定拒绝。相同 helper 还会把 memoryforge_commit/worktree_dirty 绑定到被重定向仓库，破坏 producer 自报身份。

**修复建议**

让 `_git_output` 与 `_git` 共用完全相同的 env 和 `-c` 参数，清除 GIT_*、禁用 system/global config、签名与 hooks。

---

### 3. [P1][逻辑错误] 相对输出路径在切换快照后写入临时 worktree 并被清理

- 位置：`scripts/check_local.sh:54-60`
- 置信度：10/10

**问题描述**

output 保留调用者传入的相对值，但第 59 行新增全局 `cd "$snapshot"`。文档明确推荐 `./scripts/check_local.sh local-evidence/v0.3.0`：第 54 行先在仓库创建空的 `local-evidence/v0.3.0/dist`，切换后第 91、111-114、124、146 行却把真实产物写到临时 snapshot 下的同名相对目录；脚本最后仍可打印 Local checks passed，EXIT cleanup 随即删除真正 Evidence，用户看到的仓库输出目录为空。默认绝对路径不触发，但文档给出的命名发布路径确定触发。

**修复建议**

在首次检查和 mkdir 前把 output 规范化为绝对路径，例如以初始 `$root` 为基准解析；之后所有构建、发布和摘要步骤只使用该绝对路径。

---

### 4. [P1][安全漏洞] 独立 Wheel clean-room 仍允许额外包源替换运行时依赖

- 位置：`demo/run_release_check.py:48-59`
- 置信度：9/10

**问题描述**

fresh venv 的安装环境只删除 PYTHONPATH，pip 命令没有 `--isolated`、固定 index 或显式 constraints。README/demo README 都指导用户直接运行该脚本；若宿主设置 PIP_EXTRA_INDEX_URL、PIP_FIND_LINKS 或 pip.conf，fresh venv 会从额外来源解析 Wheel 的依赖。提供满足版本范围（或与 constraints 同版本）的替代依赖即可在后续 CLI/benchmark 中执行，provenance 只记录版本字符串并仍会把 clean-room 标为 passed。check_local/build_release 上游虽已清环境，独立且公开支持的 release-check 路径仍不具备同一供应链契约。

**修复建议**

在 producer 内删除全部 PIP_*/UV_* 与用户配置影响，pip install 使用 `--isolated --index-url https://pypi.org/simple -c <fixed constraints>`；发布级验证最好使用带 hash 的约束或只读 wheelhouse。

---

### 5. [P1][业务语义问题] Wheel release check 的仓库与 public-source 身份可被 GIT_* 伪造

- 位置：`demo/run_release_check.py:268-274`
- 置信度：9/10

**问题描述**

该公开文档直接暴露的 release producer 用此 helper 校验 public_source 固定 Commit、校验 raw Code Wiki 的 MemoryForge Commit，并写 provenance.memoryforge_commit/worktree_dirty，但 `git -C` 仍会被 GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE 覆盖。调用者可令这些变量指向另一个干净 checkout，使错误 public-source 路径通过 pinned Commit 检查，或让真实 MemoryForge 未提交 evaluator 参与运行而 provenance 仍声称 clean HEAD；上游 check_local/build_release 当前会清理环境，但 README 中的独立调用没有该保护。

**修复建议**

所有 Git 查询使用清洁环境并禁用 global/system config；对 MemoryForge 和 public source 都显式绑定各自 git-dir/work-tree，且在运行前后复核同一 Commit 和 clean 状态。

---
