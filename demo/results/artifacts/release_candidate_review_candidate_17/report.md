# 代码评审报告

- 仓库：MemoryForge
- 检测模式：通用检测
- 检测范围：569685c2f0bf790819820b821b4768d180c4ee0d...b0dfd3ac91d6f97b3cfd1e8dfad3160237bfdec0
- 生成时间：2026-08-10 13:32
- 检查文件：216
- 变更行数：54853

## 缺陷统计

- P0：0
- P1：4
- P2：0
- 合计：4

## 缺陷详情

### 1. [P1][业务语义问题] Development gate 仍从可隐藏改动的实时 worktree 执行 evaluator

- 位置：`demo/run_release_candidate_benchmark.py:150-153`
- 置信度：10/10

**问题描述**

Candidate 14 只清除了 GIT_* 与 global config，但 main 仍把 `git status --porcelain` 当作实时文件等于 HEAD 的证明，并在此前第 119-136 行直接从 REPO_ROOT 导入 evaluator。Git index 的 `assume-unchanged`/`skip-worktree` 位不属于环境变量；对 demo/validate_benchmark_registry.py 或文档设置该位后再修改，status 前后均可为空，runner 却执行未提交 validator/读取未提交输入，最终仍写 memoryforge_worktree_dirty=false 和 HEAD Commit。由此可用未进入所报 Commit 的评估逻辑放行 release artifacts，development Evidence 的 Commit 绑定仍可被绕过。

**修复建议**

像 check_local/build_release 一样先固定 HEAD 并创建独立 detached worktree，再从该快照加载 validator、summary builder、manifests 和文档并执行全部 suite；或逐个把所有 evaluator/input 字节与 `git show <commit>:<path>` 比对。

---

### 2. [P1][业务语义问题] 公开 Benchmark summary 仍可把隐藏的 Registry/validator 改动归因到 HEAD

- 位置：`demo/build_benchmark_summary.py:29-34`
- 置信度：10/10

**问题描述**

新增隔离 Git helper 可防 GIT_* 重定向，但两次 clean 检查仍信任当前 index。对 demo/evaluation/registry.json 或 demo/validate_benchmark_registry.py 标记 assume-unchanged/skip-worktree 后修改，`git status --porcelain` 继续为空；第 45-46 行却读取/执行实时未提交字节，summary 仍声明 memoryforge_commit=HEAD。该脚本是独立公开 Evidence producer，因此可发布不属于声明 Commit 的 suite、状态或聚合结果；build_release 的快照调用安全，不能保护这里支持的直接调用路径。

**修复建议**

从固定 detached snapshot 执行 summary producer，或用 `git show <commit>` 读取 registry、validator 输入和 pyproject，并拒绝 live 文件摘要与对应 Git blob 不同。

---

### 3. [P1][业务语义问题] [P1] 精确解析通过态报告的状态与统计

- 位置：`demo/validate_benchmark_registry.py:4491-4509`
- 置信度：10/10

**问题描述**

这里仍是子串存在性和两个窄正则：只拒绝单词 `failed`/“失败”和 `P1: 4` 这种冒号格式，不拒绝 `status: rejected`、`P1 = 4`、额外的错误 Commit/scope，且完全不核对报告中的文件数和行数。实测在满足当前零计数/空 defect-list 模板后，向 HTML 和 Markdown 同时追加 `status: rejected`、`P1 = 4` 及另一组 Commit scope，helper 仍返回 True。因而 Candidate 17 的 accepted Evidence 可与公开报告的状态、缺陷计数、Commit 和统计直接矛盾。

**修复建议**

将期望的 status、P0/P1/P2、base/source Commit、reviewed_files、diff_files、added/deleted/changed_lines 传入 helper，按报告生成器的结构精确解析且要求唯一值逐项相等；不要用禁止少量字符串的黑名单代替一致性校验。

---

### 4. [P1][安全漏洞] [P1] 对原始 HTML 归档执行隐私扫描

- 位置：`demo/validate_benchmark_registry.py:4487-4495`
- 置信度：10/10

**问题描述**

代码在隐私扫描前删除全部 `<script>`/`<style>` 和 HTML 标签，随后只扫描 `visible_html`。这会把归档中仍真实存在、可下载查看的脚本内容和标签属性一起抹掉。可复现：在一个其余完全合法的通过态 HTML 末尾加入 `<script>Authorization: Basic Zm9vOmJhcg==</script>`，Markdown 保持合法，`_accepted_review_reports_valid` 仍返回 True。敏感凭证因此可进入已哈希、公开保留的 accepted report 而不触发隐私门禁。

**修复建议**

语义检查可以使用可见文本，但隐私检查必须先对原始 `html` 执行 `_payload_private_detail_leaks`，并单独检查标签属性、URL userinfo、script/style/comment 内容；Markdown 继续扫描原文。

---
