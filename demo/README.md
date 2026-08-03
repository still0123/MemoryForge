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
