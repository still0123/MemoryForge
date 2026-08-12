# 私有工作区用户评测指南

## 目的

在私有 Workspace 上建立一份 50 题手工 gold set，用真实已审核 Wiki 检查
MemoryForge 的回答、引用和拒答质量。题目和答案只属于本机，不提交到公开仓库，
也不放进任何展示/Release Evidence。

## 建题

先完成导入、`ingest`、`review`、`approve`、`apply`，确保题目引用的来源已进入
当前 Wiki。每道题填写：

- `id`：稳定编号，例如 `code-01`。
- `category`：使用现有枚举之一，最贴近的类型见下方分组。
- `question`：真实用户会问的问题。
- `expected_status`：`answered` 或 `unknown`。
- `expected_source_paths`：`answered` 必填；填入该答案应引用的一个或多个来源路径。
- `required_terms` / `forbidden_terms`：`answered` 必填 `required_terms`，用于判断
  回答与引用是否命中关键事实。

推荐 50 题分布：

| 组 | 题数 | 建议 `category` |
| --- | ---: | --- |
| 代码行为、符号路由、跨仓 | 20 | `exact_symbol`、`code_behavior`、`cross_repository` |
| AI/Feishu/Codex 会话结论与待办 | 15 | `single_hop`、`multi_source` |
| 运维、配置、失败与回滚 | 10 | `single_hop`、`temporal_update` |
| 无答案或需要澄清 | 5 | `unanswerable` |

`multi_source` 至少需要 2 个 `expected_source_paths`；`unanswerable` 必须设
`expected_status: "unknown"`。不要用记忆中的模糊印象代替当前 Workspace 中实际存在的
来源路径。

## 最小合法配置

```json
{
  "name": "private-workspace-gold-50",
  "cases": [
    {
      "id": "code-01",
      "category": "code_behavior",
      "question": "src/service.py 的 service 函数返回什么？",
      "expected_status": "answered",
      "expected_source_paths": ["src/service.py"],
      "required_terms": ["helper", "ready"]
    },
    {
      "id": "unknown-01",
      "category": "unanswerable",
      "question": "当前 Workspace 没有记录的部署密钥是什么？",
      "expected_status": "unknown"
    }
  ]
}
```

## 运行

```bash
memoryforge doctor --workspace <workspace>
memoryforge eval <private-gold-50.json> --workspace <workspace>
```

输出是 JSON。重点看顶层 `memoryforge`：

- `answer_accuracy`：答案状态、关键事实和来源都正确。
- `source_recall_at_3`：期望来源是否出现在当前检索结果中。
- `citation_grounding_accuracy`：引用原文能否从来源 locator 找回。
- `multi_source_coverage`：多来源题是否全部引用到。
- `abstention_accuracy`：`unanswerable` 题是否正确返回 `unknown`。这是拒答正确率，不是
  no-answer precision；后者还必须统计可回答题被错误拒答的次数。
- `selective_accuracy` 与 `coverage`：只回答有把握题目时的准确率和覆盖率。

## Recall@5 与 MRR 的真实边界

当前 `eval` 不直接输出 Recall@5 或 MRR，也不要把现有 `source_recall_at_3` 改名冒充。
若确需这两个指标，用 `memoryforge ask '<question>' --debug --max-pages 5
--workspace <workspace>` 收集每题 `trace` 中按实际读取顺序排列的 `L1` Wiki 页面，作为
有序候选；再把 `expected_source_paths` 手工对齐到 Wiki 页面后计算：

- `Recall@5`：前 5 个候选中命中的期望项数 / 总期望项数。
- `MRR`：每题取第一个命中期排名的倒数，再对所有题求平均。

没有真实有序候选时，不要补一份假 top-5。

## 建议门槛

一次 50 题验收建议至少达到：

- `answer_accuracy` >= 90%
- `source_recall_at_3` >= 90%
- `citation_grounding_accuracy` >= 95%
- `abstention_accuracy` >= 80%
- `multi_source_coverage` >= 80%

门槛只用于用户自评，不写进 Release、Evidence 或 benchmark registry。私有题集和答案
留在本机，不提交到公开 GitHub 仓库。
