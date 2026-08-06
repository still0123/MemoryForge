# MemoryForge v0.2.1 Click 文档外部评测

## 目标

v0.2.0 的 30 题文档评测来自 AgentSkill-Eval。该题集能防回归，但不能证明查询链能迁移到无关
领域。本实验选择 Click 的公开英文文档，测试同一套确定性 Wiki 编译、检索、回答和 Citation
校验链路。

## 数据与冻结

- 仓库：`pallets/click`
- Commit：`00e592cea702e0b2caa0dee42489fdb1c22cd845`
- 许可证：BSD-3-Clause
- 导入文档：38
- 生成 Wiki 页面：39
- 模型调用：0

题目与 Source expectation 在运行前冻结于 clean Commit `fbc86e7`。development 和 holdout
各 10 题，均覆盖 single-hop、multi-source、paraphrase 和 unanswerable。完整源版本和 split
路径见
[`click_docs_sources_v021.json`](../demo/evaluation/click_docs_sources_v021.json)。

## 结果

| 指标 | Development | Holdout |
| --- | ---: | ---: |
| Answer accuracy | 10.0% | 10.0% |
| Source recall@3 | 50.0% | 25.0% |
| Citation grounding | 100.0% | 100.0% |
| Multi-source coverage | 0.0% | 0.0% |
| Abstention accuracy | 0.0% | 0.0% |
| Raw FTS source recall@3 | 75.0% | 62.5% |
| Raw FTS multi-source coverage | 50.0% | 50.0% |

Wiki lint 为 clean。两套 split 都只有 1/10 的回答满足冻结的状态、事实和 Source 要求。100%
Citation grounding 只表示已返回的引用可回读，不表示回答正确。

## 诊断与决策

development 的 9 个失败显示三个共享缺口：

1. Wiki 页面路由在通用英文文档上弱于现有 raw FTS；
2. 单个 Citation 常命中相关段落，但不足以覆盖问题所需的相邻事实；
3. 常见词存在局部重叠时，系统缺少可靠的 unanswerable 置信门禁。

holdout 已在任何查询代码修改前运行并揭盲。为避免根据 holdout 调参，本阶段不修改检索逻辑，
不重写题目，不把 Citation grounding 当作 answer accuracy。后续修复必须重新冻结独立确认集。

## 原基线回归

在同一 MemoryForge Commit 上，使用 AgentSkill-Eval 固定 Commit
`93f5dc05229da250b041850ad8deeeec886ef304` 重跑原 30 题。结果与 v0.2.0 字节口径一致：

- Answer accuracy：96.7%
- Source recall@3：96.2%
- Citation grounding：96.2%
- Multi-source coverage：100.0%
- Abstention accuracy：100.0%

所有核心指标 delta 为 0。

## Evidence

- [`click_docs_dev_v021.json`](../demo/results/click_docs_dev_v021.json)
  SHA256：`397528f639bba18a078defcf154178079d44addb383403718ca5f5751fa6373d`
- [`click_docs_holdout_v021.json`](../demo/results/click_docs_holdout_v021.json)
  SHA256：`07af7b51289c15fb059d2d0d939b929a003497093012bcfead0780f1c5659c17`
- [`agent_skill_eval_v021_regression.json`](../demo/results/agent_skill_eval_v021_regression.json)
  SHA256：`010a0c6e30be1f03414b325835ce241e91153ce83b0f271028781b7fd4f832f7`
