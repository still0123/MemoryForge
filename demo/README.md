# 公开证据脚本

`demo/run_public_demo.py` 把 MemoryForge 现有 CLI 按真实用户路径串起来，对一个**已经克隆好的**本地公开 Git 仓库跑出一份可提交、可复现的证据 JSON。它不注册新命令、不 clone/fetch/checkout、不调用模型、不读取 `.env`。

## 运行

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --workspace /private/tmp/memoryforge-public-demo \
  --output demo/results/agent_skill_eval_public.json
```

- `--source-repo`：已存在的本地 Git checkout；脚本只读它的 `HEAD`。
- `--workspace`：必须不存在或为空；脚本绝不删除已有目录。
- `--output`：证据 JSON 写到哪里。
- `--eval-config`（默认 `demo/evaluation/agent_skill_eval.json`）、`--question`（默认一条固定问题）可覆盖。

脚本依次编排 `init → git-add --public → git-sync → ingest --pending → review → apply --approve → lint → ask --debug --verify → eval`，并从每一步的真实 JSON 输出解析 `repository-id`、`changeset-id` 等值。任一步非零退出即保留 workspace 并清晰失败，不生成“成功”证据文件。

## 证据 JSON 字段

- `memoryforge_commit`、`source_repository.{remote_url,commit}`：主仓与源仓的真实 commit。
- `workflow.source_count`、`workflow.wiki_file_count`、`workflow.lint`：本次 `git-sync` / `apply` / `lint` 的真实结果。
- `sample_query`：固定问题的答案、引用、渐进式查询 trace。
- `evaluation`：题集 `eval` 的完整汇总。

不写时间戳、绝对路径、API Key、环境变量或 workspace 内部 Git commit，因此两个不同临时 workspace 连续运行结果一致。

## 这份基线证明什么

它证明公开 Git 文档可以沿真实路径完成 `导入 → 编译 → 审核 → 写入 → 检索 → 回源 → 评测`，并记录
实际页面预算和引用核验成本。当前提交的结果固定使用 `AgentSkill-Eval@93f5dc0`，30 道公开题的
结果已写入 `demo/results/agent_skill_eval_public.json`。

当前公开基线（不调用模型）如下：

| 指标 | 结果 |
| --- | ---: |
| 题目数 | 30 |
| 回答准确率 | 96.7% |
| Top-3 来源召回率 | 96.0% |
| 引用落地准确率 | 100% |
| 多来源覆盖率 | 100% |
| 拒答准确率 | 100% |
| 平均展开 Wiki 页面 | 3.0 |
| 平均读取原文 | 0.0 |
| 平均证据字符数 | 140.17 |

它不比较不同模型，也不证明所有知识库都比 Raw FTS 更准确；`raw_fts_baseline` 只记录当前数据集上的
检索边界。公开题集中保留了 1 道同义改写题未命中，避免用更宽松的规则把指标包装得更好；启用模型后，
模型可以在命中的候选证据上进行受控整理，但不能凭空补充来源。Agent 的证据终止约束、增量主题扩展、
`local_only` 模型授权和飞书回复桥接由仓库测试覆盖，因为这些功能不应把公司内部资料写进公开 Demo。
