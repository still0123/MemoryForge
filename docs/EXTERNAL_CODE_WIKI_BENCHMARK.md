# MemoryForge v0.2.1 外部 Code Wiki Benchmark

## 问题

v0.2.0 的 C0-C4 结果来自 6 个固定源码文件，适合防回归，但不能证明解析器能处理真实仓库。
本实验使用三个固定公开仓库，验证真实代码能否从空 Workspace 完成：

```text
git-add -> code-add -> git-sync -> CodeIndex -> ModulePlan
-> PROPOSED -> review -> approve -> apply -> lint -> evaluate
```

全流程不调用模型。

## 数据

| 仓库 | 语言 | 固定 Commit | 许可证 | 代码 Source |
| --- | --- | --- | --- | ---: |
| pallets/click | Python | `00e592c` | BSD-3-Clause | 17 |
| spf13/cobra | Go | `adbc881` | Apache-2.0 | 36 |
| colinhacks/zod | TypeScript | `912f0f5` | MIT | 34 |

完整 Commit、源码范围和许可证 SHA256 固定于
[`external_code_wiki_sources_v021.json`](../demo/evaluation/external_code_wiki_sources_v021.json)。

## 人工标签

标签在 Benchmark Runner 完成前人工核对源码，并固定为：

- 每仓 20 个 Symbol，共 60 个；
- 每仓 15 条 Relation，共 45 条；
- 每仓 5 个 Module assignment，共 15 个；
- 全部 87 个选定代码 Source。

这是非穷举正样本，不是从运行结果自动生成的全量真值，也不测错误 Relation 的 precision。

## 结果

| 指标 | 结果 |
| --- | ---: |
| 代码 Source 覆盖 | 100.0% |
| 人工 Symbol 标签命中 | 100.0% |
| 人工 Relation 标签命中 | 100.0% |
| 人工 Module 标签命中 | 100.0% |
| Citation grounding | 100.0% |
| deterministic replay | 100.0% |
| Wiki lint | clean |

三个仓库共索引 2,445 个 Symbol、3,718 条 Relation、60 个 Module，生成 60 个 Code Wiki
文件。Zod 包含 64 条跨模块 Architecture edge，Mermaid edge、Relation Citation 和顺序确定性均
为 100%。Click 与 Cobra 在当前模块粒度没有跨模块边，相关 Mermaid 指标记为不适用，不计为
0% 失败或 100% 成功。

连续两次从空 Workspace 运行结果字节一致，Evidence SHA256：

```text
9073b96cad920df5bddcd46eadbbe507b0b673e8d714a0619ad0913fb6f6c496
```

完整 Evidence：
[`external_code_wiki_v021.json`](../demo/results/external_code_wiki_v021.json)。

## 复现

先将三个仓库 checkout 到 manifest 固定 Commit，再执行：

```bash
.venv/bin/python demo/run_external_code_wiki_benchmark.py \
  --source-repo click=/absolute/path/to/click \
  --source-repo cobra=/absolute/path/to/cobra \
  --source-repo zod=/absolute/path/to/zod \
  --workdir /private/tmp/memoryforge-external-code-wiki \
  --output /private/tmp/external_code_wiki_v021.json
```

Runner 会校验 HEAD、origin 和许可证哈希。任一冻结标签、Citation、lint 或确定性门禁失败时以
非零状态退出。

## 边界

- 100% 只表示冻结的 60/45/15 条人工正样本全部命中，不能外推为全仓 precision 或 recall；
- 本轮不含负 Relation 标签，因此不宣称 Relation precision；
- Cobra 的 `.` 选择包含仓库测试文件；这是 manifest 明示范围，不代表生产源码专属结果；
- Click 文档评测与独立复评均得到负结果，见
  [`CLICK_DOCS_BENCHMARK.md`](CLICK_DOCS_BENCHMARK.md) 和
  [`EXTERNAL_DOCUMENT_WIKI_BENCHMARK.md`](EXTERNAL_DOCUMENT_WIKI_BENCHMARK.md)。

## 性能与单文件更新

在 `d90a129` 上，每仓从空 Workspace 运行 3 次，随后在临时 detached worktree 中给一个选定
Source 增加一个合成函数并提交。计时不包含生成该 Git Commit 的耗时；未清空操作系统文件
缓存。结果取中位数：

| 仓库 | 冷启动 | 单文件更新 | 更新/冷启动 | 峰值 RSS | 变化页面占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Click | 6.20s | 6.71s | 108.2% | 85.0 MiB | 5.88% |
| Cobra | 5.51s | 5.57s | 101.0% | 102.6 MiB | 50.00% |
| Zod | 5.40s | 6.40s | 118.5% | 99.6 MiB | 2.94% |

最大仓库按 Symbol 数选择 Zod。其单文件更新超过冷启动的 50%，但未超过 10 秒，因此未同时
触发预注册缓存门禁，决策为 `NO_CHANGE`。未增加文件级缓存。

Cobra 只有 2 个当前 Module，单页变化导致 50% 页面占比，未通过 25% 增量页面门禁。这是当前
模块粒度的真实负结果，不改写为成功。

完整性能 Evidence：
[`external_code_wiki_profile_v021.json`](../demo/results/external_code_wiki_profile_v021.json)。

```text
SHA256 cc3d7dcbbb47350bfe171b248646fda4fe29bfc8152346e98f0dc7f21661a1fa
```
