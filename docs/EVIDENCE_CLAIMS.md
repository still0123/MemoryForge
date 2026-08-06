# MemoryForge v0.2.1 Evidence Claims

本页只列可由仓库内 Evidence 直接核验的主张。局部结果不得外推为任意仓库、任意语言或生产 SLA。

## 发布门禁

| 主张 | Evidence | 边界 |
| --- | --- | --- |
| 完整测试、Ruff、Mypy、四矩阵 CI 通过 | GitHub Actions `31074527750` 与最终发布 PR | 不代表生产流量验证 |
| 开发与构建依赖可冻结重放 | [`constraints/dev.txt`](../constraints/dev.txt)，SHA256 `48debebcfd2da302201688e0582c676cb571d66e96cd7a2a2e0654f49a544571` | 仅覆盖声明的平台与 Python 版本 |
| Wheel 和 sdist 可独立安装 | tag workflow 与 [`run_release_check.py`](../demo/run_release_check.py) | 本地预演完成；正式 SHA 等 tag workflow |
| Release 产物有 attestation 和回下载校验 | [release workflow](../.github/workflows/release.yml) | 创建 `v0.2.1` 后产生真实远端证据 |

## 代码 Wiki

| 主张 | Evidence | 边界 |
| --- | --- | --- |
| Click/Cobra/Zod 共索引 2,445 Symbols、3,718 Relations | [`external_code_wiki_v021.json`](../demo/results/external_code_wiki_v021.json) | 固定 87 个 Source |
| 60 Symbol、45 Relation、15 Module 正样本全部命中 | 同上，SHA256 `9073b96cad920df5bddcd46eadbbe507b0b673e8d714a0619ad0913fb6f6c496` | 非穷举正样本；不等于全仓 precision/recall |
| Zod 64 条 Architecture edge 的 Mermaid/Citation 为 100% | 同上 | Click/Cobra 无跨模块边，相关指标 N/A |
| 缓存决策为 `NO_CHANGE` | [`external_code_wiki_profile_v021.json`](../demo/results/external_code_wiki_profile_v021.json) | macOS 单机、每仓 3 次中位数 |
| Cobra 单文件变化页面比例为 50% | 同上 | 真实负结果；当前仅 2 个 Module |

## 文档 Wiki

| 主张 | Evidence | 边界 |
| --- | --- | --- |
| 原 30 题 Answer/Citation 为 100%，Source recall@3 为 96.2% | [`agent_skill_eval_public.json`](../demo/results/agent_skill_eval_public.json) | 单一公开仓库固定题集 |
| Click development/holdout Answer accuracy 为 10%/0% | [`click_docs_dev_v021.json`](../demo/results/click_docs_dev_v021.json)、[`click_docs_holdout_v021.json`](../demo/results/click_docs_holdout_v021.json) | 外部迁移负结果 |
| Click 两套 split 的 Citation grounding 为 100% | 同上 | 只证明引用可回读，不证明答案正确 |
| Click Source recall@3 为 0% | 同上 | holdout 已揭盲；禁止据此继续调参 |

## 面试表述

可以说“系统具备可审核、可回溯、可复现的编译与发布链路”。不能说“通用文档问答已解决”、
“任意仓库 precision/recall 为 100%”或“达到生产 SLA”。
