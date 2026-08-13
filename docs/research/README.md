# Research Archive

这里保存 MemoryForge 的实验设计、被拒绝或被替代的 Candidate，以及固定 Evidence 的解释。
它们用于审计历史，不代表当前产品状态或支持范围。

## 当前入口

- 产品状态与 Quickstart：[README](../../README.md)
- v0.4.0 发布范围：[CHANGELOG](../../CHANGELOG.md)
- 当前公开指标：[Benchmark](../BENCHMARK.md)
- 可审计主张：[Evidence Claims](../EVIDENCE_CLAIMS.md)

## 研究主题

- v0.3 发布链：[Release Delivery](V030_RELEASE_DELIVERY.md) 与
  [Release Candidate Specification](../V030_RELEASE_CANDIDATE_SPEC.md)
- Benchmark Registry：[BENCHMARK_REGISTRY.md](BENCHMARK_REGISTRY.md)
- 查询与拒答：[WIKI_FACT_INDEX.md](WIKI_FACT_INDEX.md)、
  [EXACT_SYMBOL_ROUTING.md](EXACT_SYMBOL_ROUTING.md)、
  [MULTI_SOURCE_COVERAGE.md](MULTI_SOURCE_COVERAGE.md)、
  [SUPPORT_SCORE.md](SUPPORT_SCORE.md)
- 来源接入：[FOLDER_IMPORT.md](FOLDER_IMPORT.md)、
  [GITHUB_THREAD_IMPORT.md](GITHUB_THREAD_IMPORT.md)
- Code Wiki：[CODE_MODULE_NARRATIVE.md](CODE_MODULE_NARRATIVE.md)
- 展示与交付：[STATIC_SHOWCASE.md](STATIC_SHOWCASE.md)、
  [CROSS_PLATFORM_WORKSPACE_LOCK.md](CROSS_PLATFORM_WORKSPACE_LOCK.md)

## 原始 Evidence

机器可读结果仍保存在 `demo/results/`，冻结题集和 Registry 保存在 `demo/evaluation/`。
`demo/results/artifacts/` 中的旧 Wheel、sdist、provenance 和静态审查报告由本地验证器直接重放，
因此不为缩短目录而删除。当前工作树约 42 MiB，但 GitHub 仓库对象已按重复内容去重。

功能冻结后不再为展示继续增加 Candidate 或 benchmark。只有真实用户反馈暴露新的错误回答、
召回缺口或交付问题时，才新增对应的最小复现和 Evidence。
