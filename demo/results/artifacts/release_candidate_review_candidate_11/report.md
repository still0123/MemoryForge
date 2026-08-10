# 代码评审报告

- 仓库：MemoryForge
- 检测模式：通用检测
- 检测范围：569685c2f0bf790819820b821b4768d180c4ee0d...43d5c80852595c7b49e46c66f70ced82c53cf7d0
- 生成时间：2026-08-08 13:13
- 检查文件：136
- 变更行数：28902

## 缺陷统计

- P0：0
- P1：5
- P2：0
- 合计：5

## 缺陷详情

### 1. [P1][业务语义问题] Wheel 与 sdist 的 metadata 差异被误判为匹配

- 位置：`demo/validate_benchmark_registry.py:2401-2425`
- 置信度：10/10

**问题描述**

_validate_release_development_artifacts 把 _package_archives_match 的 True 当作保留包 metadata 闭包，但该函数只分别检查 Name 和 Version，既不要求规范 member 路径，也不比较 Wheel 与 sdist 的其余 metadata。定向构造中，Wheel 使用 other-0.3.0.dist-info/METADATA 且 Requires-Dist 为 safe-dependency，sdist 使用 other-0.3.0/PKG-INFO、Requires-Python >=99 且 Requires-Dist 为 different-dependency，函数仍返回 True。此时两个发布渠道具有不同安装合同，却可被 Registry 认定为同一 MemoryForge 0.3.0 包。

**修复建议**

要求精确的 memoryforge-0.3.0.dist-info/METADATA 和 memoryforge-0.3.0/PKG-INFO 路径，并按规范化多值字段比较 Requires-Python、Requires-Dist、Provides-Extra、Summary、Description-Content-Type 等发布 metadata，必要时同时核对 Wheel 的 WHEEL tag。

---

### 2. [P1][业务语义问题] Workspace drill 可在缺失或伪造 verify Evidence 与 support 时通过

- 位置：`demo/validate_benchmark_registry.py:2981-3014`
- 置信度：10/10

**问题描述**

schema 2 consumer 只检查 Citation 外壳及 support 的三个字段，不要求 --verify 产生 evidence，也不校验 evidence 与 Citation/SourceVersion 的对应关系及完整 support 结构。对 Candidate 11 留存物删除 evidence、将 evidence 改成 source_version=999 的伪造记录，或删除 support.score/threshold/components 后，_release_drill_contract(..., evidence_revision=12) 均仍返回 true；producer 的两个 query validator 同样通过。具体回归路径是 ask --verify 不再回读原文或 support 输出残缺，development 仍把 workspace-release-drill 记为 passed/6-of-6。

**修复建议**

对 answered/unknown payload 使用精确 schema；answered 必须包含一条 evidence，并要求其 source_id/source_version/locator 与 Citation 一致且 text 为固定夹具片段；对 support 固定完整键集、threshold、score/components，并从 score、components 重算 sufficient 与 failed_hard_gates。producer 的 _answered_query_valid/_unknown_query_valid 应复用同一验证器。

---

### 3. [P1][安全漏洞] 查询 Evidence 隐私扫描漏掉常见绝对用户路径

- 位置：`demo/run_release_candidate_benchmark.py:639-668`
- 置信度：10/10

**问题描述**

隐私门禁只枚举 /Users、/home、/private、/tmp 和 C:\Users；schema 2 query consumer 又允许未声明的 payload 字段。给 answered original/restored 同时加入 debug_path=/root/private/workspace、/var/folders/... 或 D:\Users\private\workspace 后，_release_drill_contract 仍返回 true，_private_detail_leaks 仍返回 0。若 ask 在这些主机路径下泄出调试字段，builder 会把它写入并哈希为公开 workspace-drill.json，development 的 private_detail_leaks gate 仍通过。

**修复建议**

对 schema 2 query payload 使用精确字段和值域白名单，并在 producer、development runner、retained consumer 三处复用跨平台隐私校验；至少识别任意 POSIX 绝对路径和任意盘符/UNC Windows 路径，而不是枚举少量目录前缀。

---

### 4. [P1][安全漏洞] retained provenance 接受 Windows 绝对 import_path 并伪造 clean-room 归属

- 位置：`demo/validate_benchmark_registry.py:3341-3354`
- 置信度：10/10

**问题描述**

新增 consumer 只校验 package 的键集、Wheel 名称/摘要和 import_from_fresh_venv=true，既不要求 import_path 为相对路径，也不验证其位于 fresh venv；相邻隐私扫描也只列举少量目录前缀。定向变异把 Candidate 11 provenance 的 import_path 改为 D:/builds/Alice/private/repo/src/memoryforge/__init__.py，并同步重算 provenance、SHA256SUMS、artifacts 与 artifact_files 摘要后，_validate_bound_gate_artifacts(..., require_replayable_sums=True, require_code_evidence=True) 仍返回 true。因此 Windows 非 C:\Users 盘符或 UNC 私有路径既可泄露到 retained Evidence，也可让仓库外导入冒充 clean-room 导入。

**修复建议**

把 import_path 作为结构化 clean-room 合同校验：拒绝任何 POSIX/Windows 绝对路径、盘符和 UNC 路径，并要求规范化相对路径严格位于 .venv 的 site-packages/memoryforge 下；隐私扫描同时覆盖任意 Windows drive-root 与 UNC 形式。

---

### 5. [P1][健壮性问题] raw Code Wiki 固定 Commit 合同与生产端继承 GIT_* 身份不兼容

- 位置：`demo/validate_benchmark_registry.py:3420-3426`
- 置信度：10/10

**问题描述**

新增 consumer 要求 fixture.initial_commit/updated_commit 精确等于 CODE_WIKI_FIXTURE_COMMITS，但调用链 check_local -> run_release_check -> run_code_wiki_benchmark 中的 Git 生产端只固定日期，仍继承 GIT_AUTHOR_NAME、GIT_AUTHOR_EMAIL、GIT_COMMITTER_NAME 和 GIT_COMMITTER_EMAIL；这些变量会覆盖生产端写入的仓库配置。同一 fixture 在默认环境生成 initial Commit c7a312631c0f4122e82e08d23d69498b1c2881c4，设置调用者姓名变量后则生成 25ab9c8d181cec098e0bcea604e026d067e79a7a，且 release_check._validate_code_evidence 对被替换的 fixture Commit 仍判通过。于是特定 CI/开发者环境可让本地门禁成功产出随后必被 retained consumer 拒绝的 Evidence。

**修复建议**

在 Code Wiki producer 的每个 Git 子进程中显式固定 author/committer name、email、date，清除相关 GIT_* 覆盖，并禁用全局配置、签名和 hooks；同时让 run_release_check 在发布 raw Evidence 前执行与 retained consumer 相同的 fixture 身份校验。

---
