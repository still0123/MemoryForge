# MemoryForge v0.3.0 Evidence Claims

<!-- memoryforge-release-claim: version=0.3.0; status=release_candidate; active_candidate=11; platform_gate_candidate=11; platform_gate_status=accepted; review_status=rejected; macos_passed=609; linux_passed=606; linux_skipped=3; windows_confirmation=not_run; confirmation=not_run; holdout=not_run -->

本页只列可由仓库内 Evidence 直接核验的主张。局部结果不得外推为任意仓库、任意语言或生产 SLA。

## 发布门禁

| 主张 | Evidence | 边界 |
| --- | --- | --- |
| v0.2.1 的完整测试、Ruff、Mypy、四矩阵 CI 通过 | 历史 GitHub Actions `31074527750` | 仅证明已发布的 v0.2.1 Commit |
| 后续变更使用本地完整门禁 | [`scripts/check_local.sh`](../scripts/check_local.sh) 与本地 SHA256 Evidence | GitHub Actions 已关闭；不得宣称远端 CI |
| v0.3.0 release-candidate 使用双隔离构建、Workspace drill 和严格 Registry | [`V030_RELEASE_CANDIDATE_SPEC.md`](V030_RELEASE_CANDIDATE_SPEC.md) | 原生 Windows confirmation 与 holdout 通过前不得宣称已发布 |
| v0.3.0 RC 在 macOS 574 passed，Linux 571 passed / 3 skipped，coverage 88%，Wheel/sdist 跨平台 SHA256 一致 | [`release_candidate_candidate_1_local_gates.json`](../demo/results/release_candidate_candidate_1_local_gates.json) | 固定 Commit `3980b47`；Candidate 1 已 superseded，不等于原生 Windows confirmation |
| Candidate 2 development 6/6，真实核验 Wheel/sdist metadata、bytes、Commit 和 sdist clean-room | [`release_candidate_development_candidate_2.json`](../demo/results/release_candidate_development_candidate_2.json) | 已 superseded；confirmation 与 holdout 未运行 |
| Candidate 2 在 macOS 583 passed，Linux 580 passed / 3 skipped，coverage 88%，Wheel/sdist 跨平台 SHA256 一致 | [`release_candidate_candidate_2_local_gates.json`](../demo/results/release_candidate_candidate_2_local_gates.json) | 已 superseded；固定 Commit `926832e`；不等于原生 Windows confirmation |
| Candidate 2 静态终审为 0 P0 / 5 P1 / 1 P2，结果 rejected | [`release_candidate_candidate_2_static_review_rejected.json`](../demo/results/release_candidate_candidate_2_static_review_rejected.json) | 失败保留；未运行 confirmation/holdout |
| Candidate 3 development 6/6，并使用结构化 release claim | [`release_candidate_development_candidate_3.json`](../demo/results/release_candidate_development_candidate_3.json) | 两套 isolated build bytes 未注册，不能独立核验其可复现性；confirmation/holdout 未运行 |
| Candidate 3 在 macOS 586 passed，Linux 583 passed / 3 skipped，coverage 88%，Wheel/sdist 跨平台 SHA256 一致 | [`release_candidate_candidate_3_local_gates.json`](../demo/results/release_candidate_candidate_3_local_gates.json) | 固定 Commit `40cabe1`；不等于原生 Windows confirmation |
| Candidate 4 development 因历史平台计数误报为文档冲突，结果 5/6 rejected | [`release_candidate_development_candidate_4_rejected.json`](../demo/results/release_candidate_development_candidate_4_rejected.json) | 固定 Commit `7e998d5`；失败与双构建字节均保留，未运行 confirmation/holdout |
| Candidate 5 development 6/6，双构建 Wheel/sdist 字节一致 | [`release_candidate_development_candidate_5.json`](../demo/results/release_candidate_development_candidate_5.json) | 固定 Commit `b42d6a8`；confirmation 与 holdout 未运行 |
| Candidate 5 在 macOS 586 passed，Linux 583 passed / 3 skipped，coverage 88%，Wheel/sdist 跨平台 SHA256 一致 | [`release_candidate_candidate_5_local_gates.json`](../demo/results/release_candidate_candidate_5_local_gates.json) | 固定 Commit `88a0b10`；不等于原生 Windows confirmation |
| Candidate 5 最终静态审查为 0 P0 / 10 P1 / 2 P2，结果 rejected | [`release_candidate_candidate_5_static_review_rejected.json`](../demo/results/release_candidate_candidate_5_static_review_rejected.json) | 原始 12 条与 Top 5 报告均保留；未运行 confirmation/holdout |
| Candidate 6 development 6/6，固定源码快照并绑定完整 release artifact closure | [`release_candidate_development_candidate_6.json`](../demo/results/release_candidate_development_candidate_6.json) | 固定 Commit `9a6c145`；本地门禁、confirmation 与 holdout 未运行 |
| Candidate 6 在 macOS 594 passed，Linux 591 passed / 3 skipped，coverage 88%，Wheel/sdist 跨平台 SHA256 一致 | [`release_candidate_candidate_6_local_gates.json`](../demo/results/release_candidate_candidate_6_local_gates.json) | 固定 Commit `4440b05`；不等于原生 Windows confirmation |
| Candidate 6 最终静态审查为 0 P0 / 9 P1 / 3 P2，结果 rejected | [`release_candidate_candidate_6_static_review_rejected.json`](../demo/results/release_candidate_candidate_6_static_review_rejected.json) | development 与 local-gate 制品不一致；未运行 confirmation/holdout |
| Candidate 7 development 6/6，固定 package 输入并绑定完整 release artifact closure | [`release_candidate_development_candidate_7.json`](../demo/results/release_candidate_development_candidate_7.json) | 固定 Commit `80b111b`；confirmation 与 holdout 未运行 |
| Candidate 7 macOS 首次本地门禁为 598 passed / 1 failed，结果 rejected | [`release_candidate_candidate_7_local_gate_contract_rejected.json`](../demo/results/release_candidate_candidate_7_local_gate_contract_rejected.json) | 旧测试误要求已移除的 `sdist.exclude`；该次 Linux gate 未运行，失败保留 |
| Candidate 7 在 macOS 599 passed，Linux 596 passed / 3 skipped，coverage 88%，Wheel/sdist 与 development 字节一致 | [`release_candidate_candidate_7_local_gates.json`](../demo/results/release_candidate_candidate_7_local_gates.json) | 固定 Commit `249a89b`；终审 rejected；不等于原生 Windows confirmation |
| Candidate 7 静态终审为 0 P0 / 10 P1 / 3 P2，结果 rejected | [`release_candidate_candidate_7_static_review_rejected.json`](../demo/results/release_candidate_candidate_7_static_review_rejected.json) | 固定范围 `569685c...a044337`；原始 13 条保留；confirmation/holdout 未运行 |
| Candidate 8 development 6/6，Summary schema 2 与真实 Workspace replay 门禁通过 | [`release_candidate_development_candidate_8.json`](../demo/results/release_candidate_development_candidate_8.json) | 固定 Commit `2451f2d`；终审 rejected；confirmation/holdout 未运行 |
| Candidate 8 在 macOS 604 passed，Linux 601 passed / 3 skipped，coverage 88%，retained SHA256SUMS 可重放 | [`release_candidate_candidate_8_local_gates.json`](../demo/results/release_candidate_candidate_8_local_gates.json) | 固定 Commit `4c6f8e6`；终审 rejected；不等于原生 Windows confirmation |
| Candidate 8 静态终审为 0 P0 / 4 P1 / 2 P2，结果 rejected | [`release_candidate_candidate_8_static_review_rejected.json`](../demo/results/release_candidate_candidate_8_static_review_rejected.json) | 固定范围 `569685c...f4dde09`；6 条发现保留；confirmation/holdout 未运行 |
| Candidate 9 development 6/6，完整 Code Wiki Evidence 与历史评审范围均闭合验证 | [`release_candidate_development_candidate_9.json`](../demo/results/release_candidate_development_candidate_9.json) | 固定 Commit `63326fb`；终审 rejected；confirmation/holdout 未运行 |
| Candidate 9 在 macOS 607 passed，Linux 604 passed / 3 skipped，coverage 88%，raw Code Wiki Evidence 跨平台同字节 | [`release_candidate_candidate_9_local_gates.json`](../demo/results/release_candidate_candidate_9_local_gates.json) | 固定 Commit `53caa51`；终审 rejected；不等于原生 Windows confirmation |
| Candidate 9 静态终审为 0 P0 / 10 P1 / 4 P2，结果 rejected | [`release_candidate_candidate_9_static_review_rejected.json`](../demo/results/release_candidate_candidate_9_static_review_rejected.json) | 固定范围 `569685c...c23de59`；14 条发现保留；confirmation/holdout 未运行 |
| Candidate 10 development 因旧 consumer 拒绝 Workspace drill schema 2 而 5/6 rejected | [`release_candidate_development_candidate_10_rejected.json`](../demo/results/release_candidate_development_candidate_10_rejected.json) | 固定 Commit `a4c74bd`；Summary schema 3 与新 drill bytes 保留；confirmation/holdout 未运行 |
| Candidate 11 development 6/6，Summary schema 3 与可重算 Workspace query Evidence 通过 | [`release_candidate_development_candidate_11.json`](../demo/results/release_candidate_development_candidate_11.json) | 固定 Commit `f174d1d`；终审 rejected；confirmation/holdout 未运行 |
| Candidate 11 在 macOS 609 passed，Linux 606 passed / 3 skipped，coverage 88%，raw Code Wiki Evidence 跨平台同字节 | [`release_candidate_candidate_11_local_gates.json`](../demo/results/release_candidate_candidate_11_local_gates.json) | 固定 Commit `ee298f0`；终审 rejected；不等于原生 Windows confirmation |
| Candidate 11 静态终审为 0 P0 / 8 P1 / 4 P2，结果 rejected | [`release_candidate_candidate_11_static_review_rejected.json`](../demo/results/release_candidate_candidate_11_static_review_rejected.json) | 固定范围 `569685c...43d5c80`；12 条独立发现保留；confirmation/holdout 未运行 |
| Candidate 2 首次 sdist preflight 因 macOS 路径 alias 失败且未生成输出目录 | [`release_candidate_sdist_probe_regression_rejected.json`](../demo/results/release_candidate_sdist_probe_regression_rejected.json) | 保留失败；修复使用 canonical path，未放宽 ownership gate |
| 开发与构建依赖可冻结重放 | [`constraints/dev.txt`](../constraints/dev.txt)，SHA256 `48debebcfd2da302201688e0582c676cb571d66e96cd7a2a2e0654f49a544571` | 仅覆盖声明的平台与 Python 版本 |
| Wheel 和 sdist 可独立安装 | 本地门禁与 [`run_release_check.py`](../demo/run_release_check.py) | 需要在精确 Commit 上重跑 |
| Release 产物有 SHA256 | 本地 `SHA256SUMS` 与手动回下载校验 | 不再生成 hosted attestation |

## 代码 Wiki

| 主张 | Evidence | 边界 |
| --- | --- | --- |
| Click/Cobra/Zod 共索引 2,445 Symbols、3,718 Relations | [`external_code_wiki_v021.json`](../demo/results/external_code_wiki_v021.json) | 固定 87 个 Source |
| learn-claude-code 索引 72 Symbols、111 Relations，冻结 20/15/5 标签均为 100% | [`external_code_wiki_learn_claude_code_v031.json`](../demo/results/external_code_wiki_learn_claude_code_v031.json) | 固定 5 个 canonical lesson Source；非穷举正样本 |
| learn-claude-code QA development Answer 为 60%、Citation 为 88.9%、Abstention 为 0% | [`learn_claude_code_qa_dev_v031.json`](../demo/results/learn_claude_code_qa_dev_v031.json) | 未过门禁，confirmation 未运行 |
| support-score development Answer/Selective Accuracy 为 100%，Coverage 为 90%，Risk 为 0% | [`support_score_development_candidate_14.json`](../demo/results/support_score_development_candidate_14.json) | 固定 10 题 development；confirmation 未运行 |
| multi-source development Selection/Source/Term Coverage 为 100%，重复来源率为 0% | [`multi_source_coverage_development_candidate_1.json`](../demo/results/multi_source_coverage_development_candidate_1.json) | 固定 6 个确定性案例；confirmation 未运行 |
| 60 Symbol、45 Relation、15 Module 正样本全部命中 | 同上，SHA256 `9073b96cad920df5bddcd46eadbbe507b0b673e8d714a0619ad0913fb6f6c496` | 非穷举正样本；不等于全仓 precision/recall |
| Zod 64 条 Architecture edge 的 Mermaid/Citation 为 100% | 同上 | Click/Cobra 无跨模块边，相关指标 N/A |
| 缓存决策为 `NO_CHANGE` | [`external_code_wiki_profile_v021.json`](../demo/results/external_code_wiki_profile_v021.json) | macOS 单机、每仓 3 次中位数 |
| Cobra 单文件变化页面比例为 50% | 同上 | 真实负结果；当前仅 2 个 Module |

## 文档 Wiki

| 主张 | Evidence | 边界 |
| --- | --- | --- |
| 原 30 题严格 Answer 为 96.7%，Citation 为 100%，Source recall@3 为 96.2% | [`agent_skill_eval_public.json`](../demo/results/agent_skill_eval_public.json) | Answer 必须同时命中冻结来源；单一公开仓库固定题集 |
| Click development/holdout Answer accuracy 为 10%/0% | [`click_docs_dev_v021.json`](../demo/results/click_docs_dev_v021.json)、[`click_docs_holdout_v021.json`](../demo/results/click_docs_holdout_v021.json) | 外部迁移负结果 |
| Click 两套 split 的 Citation grounding 为 100% | 同上 | 只证明引用可回读，不证明答案正确 |
| Click Source recall@3 为 0% | 同上 | holdout 已揭盲；禁止据此继续调参 |

## 展示与跨平台

| 主张 | Evidence | 边界 |
| --- | --- | --- |
| Static Showcase development 4/4，本地详情泄漏与 Workspace 变更均为 0 | [`static_showcase_development_candidate_5.json`](../demo/results/static_showcase_development_candidate_5.json) | confirmation 未运行 |
| macOS 本地门禁 559 passed，Linux 本地门禁 556 passed / 3 skipped，覆盖率 88% | [`CROSS_PLATFORM_DELIVERY_SPEC.md`](CROSS_PLATFORM_DELIVERY_SPEC.md) | 不等于原生 Windows confirmation |
| Candidate 1-8 的失败或 superseded 结果仍保留 | [`registry.json`](../demo/evaluation/registry.json) | 不把历史 gate 重新标为最终成功 |

## 面试表述

可以说“系统具备可审核、可回溯、可复现的编译与发布链路”。不能说“通用文档问答已解决”、
“任意仓库 precision/recall 为 100%”或“达到生产 SLA”。
