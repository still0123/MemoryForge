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

题目与 Source expectation 在运行前冻结于 clean Commit `fbc86e7`。最终 Evidence 在同步远端
主线并移植独立的 PR #5 基线修复后，由 clean Commit `293bd8f` 生成。development 和 holdout
各 10 题，均覆盖 single-hop、multi-source、paraphrase 和 unanswerable。完整源版本和 split 路径见
[`click_docs_sources_v021.json`](../demo/evaluation/click_docs_sources_v021.json)。

## 结果

| 指标 | Development | Holdout |
| --- | ---: | ---: |
| Answer accuracy | 10.0% | 0.0% |
| Source recall@3 | 0.0% | 0.0% |
| Citation grounding | 100.0% | 100.0% |
| Multi-source coverage | 0.0% | 0.0% |
| Abstention accuracy | 0.0% | 0.0% |
| Raw FTS source recall@3 | 75.0% | 62.5% |
| Raw FTS multi-source coverage | 50.0% | 50.0% |

Wiki lint 为 clean。development 只有 1/10 的回答满足冻结的状态和事实要求，holdout 为 0/10；
两套 split 均未命中冻结的 Source expectation。100% Citation grounding 只表示已返回的引用
可回读，不表示回答正确。

## 诊断与决策

development 的 9 个失败显示三个共享缺口：

1. Wiki 页面路由在通用英文文档上弱于现有 raw FTS；
2. 单个 Citation 常命中相关段落，但不足以覆盖问题所需的相邻事实；
3. 常见词存在局部重叠时，系统缺少可靠的 unanswerable 置信门禁。

holdout 已在任何查询代码修改前运行并揭盲。之后同步的查询改动来自独立远端批次和 PR #5 的旧
30 题基线修复，不使用 Click 标签调参；同步后 Click Source recall 降为 0%。本阶段不再修改检索
逻辑，不重写题目，不把 Citation grounding 当作 answer accuracy。后续修复必须重新冻结独立确认集。

## 原基线回归

在同一 MemoryForge Commit 上，使用 AgentSkill-Eval 固定 Commit
`93f5dc05229da250b041850ad8deeeec886ef304` 重跑原 30 题：

- Answer accuracy：100.0%
- Source recall@3：96.2%
- Citation grounding：100.0%
- Multi-source coverage：100.0%
- Abstention accuracy：100.0%

相对 v0.2.0，Answer accuracy 和 Citation grounding 分别提高 3.3 和 3.8 个百分点，Source
recall@3、多来源覆盖和拒答均无回退。唯一来源失败是正确回答 Vue，但命中另一篇同样含有效 Vue
证据的文档。

## Evidence

- [`click_docs_dev_v021.json`](../demo/results/click_docs_dev_v021.json)
  SHA256：`8c7c18ccb9049a83f95f9c6a36a02979577ff95e43c687d77461fccdd113e06d`
- [`click_docs_holdout_v021.json`](../demo/results/click_docs_holdout_v021.json)
  SHA256：`a9b9f47bc805987384ae5d006498e8780e69c8b1ae15d00fdba49abad792efe1`
- [`agent_skill_eval_v021_regression.json`](../demo/results/agent_skill_eval_v021_regression.json)
  SHA256：`8291b022588091c2389acec0c0dd9060dd2f766a548e632e4d5a9edeb364258e`
