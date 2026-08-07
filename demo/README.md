# 公开证据脚本

`demo/run_public_demo.py` 把 MemoryForge 现有 CLI 按真实用户路径串起来，对一个或多个**已经克隆好的**本地公开 Git 仓库跑出一份可提交、可复现的证据 JSON。它不注册新命令、不 clone/fetch/checkout、不调用模型、不读取 `.env`。

## 运行

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --workspace /private/tmp/memoryforge-public-demo \
  --output demo/results/agent_skill_eval_public.json
```

- `--source-repo`：已存在的本地 Git checkout；脚本只读它的 `HEAD`，可重复传入以汇总多个公开仓库。
- `--workspace`：必须不存在或为空；脚本绝不删除已有目录。
- `--output`：证据 JSON 写到哪里。
- `--eval-config`（默认 `demo/evaluation/agent_skill_eval.json`）、`--question`（默认一条固定问题）可覆盖。

脚本依次编排 `init → git-add --public → git-sync → ingest --pending → review → approve → apply → lint → ask --debug --verify → eval`，并从每一步的真实 JSON 输出解析 `repository-id`、`changeset-id` 等值。任一步非零退出即保留 workspace 并清晰失败，不生成“成功”证据文件。

## 证据 JSON 字段

- `memoryforge_commit`、`source_repositories[].{repository_id,remote_url,commit}`：主仓与每个源仓的真实身份和 commit。
- `workflow.source_count`、`workflow.wiki_file_count`、`workflow.lint`：本次 `git-sync` / `apply` / `lint` 的真实结果。
- `sample_query`：固定问题的答案、引用、渐进式查询 trace。
- `evaluation`：题集 `eval` 的完整汇总。

不写时间戳、绝对路径、API Key、环境变量或 workspace 内部 Git commit，因此两个不同临时 workspace 连续运行结果一致。

## 零 Key 静态 Showcase

不准备外部仓库也可以直接运行三语言公开演示：

```bash
.venv/bin/python demo/run_showcase_demo.py \
  --workdir /private/tmp/memoryforge-showcase-demo \
  --output /private/tmp/memoryforge-showcase
open /private/tmp/memoryforge-showcase/index.html
```

脚本复用固定 Code Wiki 夹具，真实执行导入、索引、ChangeSet 审核应用、查询和静态导出。页面同时
引用已提交的 Click 外部迁移失败和 AgentSkill-Eval 正确拒答案例；这些结果来自固定 Evidence，
不是为展示临时合成的模拟指标。生成文件不含模型调用、远程脚本、绝对 Workspace 路径或凭据。

## 这份基线证明什么

它证明公开 Git 文档可以沿真实路径完成 `导入 → 编译 → 审核 → 写入 → 检索 → 回源 → 评测`，并记录
实际页面预算和引用核验成本。当前提交的结果固定使用 `AgentSkill-Eval@93f5dc0`，30 道公开题的
结果已写入 `demo/results/agent_skill_eval_public.json`。

当前公开基线（不调用模型）如下：

| 指标 | 结果 |
| --- | ---: |
| 题目数 | 30 |
| 回答准确率 | 96.7% |
| Top-3 来源召回率 | 96.2% |
| 引用落地准确率 | 100.0% |
| 多来源覆盖率 | 100% |
| 拒答准确率 | 100% |
| 平均展开 Wiki 页面 | 3.0 |
| 平均读取原文 | 0.0 |
| 平均证据字符数 | 159.2 |

同一题集上的 Raw FTS 基线使用任意词匹配和 BM25 Top-3，来源召回率为 57.7%，多来源完整覆盖率为
20.0%，平均暴露 728 个候选文本字符。它不比较不同模型，也不证明所有知识库都比 Raw FTS 更准确；
公开题集中保留了 1 道预期来源未命中：Citation 可落地到另一篇包含 Vue 证据的文档，但严格回答
准确率要求同时命中冻结来源，因此该题计为错误。启用模型后，模型可以在命中的候选证据上进行受控
整理，但不能凭空补充来源。
Agent 的证据终止约束、增量主题扩展、
`local_only` 模型授权和飞书回复桥接由仓库测试覆盖，因为这些功能不应把公司内部资料写进公开 Demo。

完整方法和失败案例见 [`docs/BENCHMARK.md`](../docs/BENCHMARK.md)。

## 代码 Wiki C0-C4

```bash
.venv/bin/python demo/run_code_wiki_benchmark.py \
  --workdir /private/tmp/memoryforge-code-wiki-c0 \
  --output demo/results/code_wiki_public.json
```

脚本从固定 Python/Go/TypeScript 夹具创建临时 Git 仓库，执行代码 Wiki 的同步、审核、应用、
lint、可信 Mermaid 架构图、确定性评测和单文件增量更新。它不调用模型。提交结果见
[`results/code_wiki_public.json`](results/code_wiki_public.json)。

## Wheel clean-room

```bash
uv build --wheel --out-dir dist
.venv/bin/python demo/run_release_check.py \
  --wheel dist/memoryforge-0.3.0-py3-none-any.whl \
  --workdir /private/tmp/memoryforge-release-check \
  --output /private/tmp/memoryforge-v030-release-provenance.json \
  --code-evidence-output demo/results/code_wiki_public.json \
  --public-evidence-output demo/results/agent_skill_eval_public.json \
  --public-source-repo /absolute/path/to/AgentSkill-Eval
```

脚本创建全新 venv，只安装 Wheel，清除 `PYTHONPATH`，验证实际 import 位于新环境，再复用上述两套
Demo。`--public-source-repo` 必须位于固定 Commit `93f5dc0`；省略时只运行无网络的代码 Wiki 门禁。

## 多仓库个人资料演示

仓库里的 [`evaluation/personal_public_repositories.json`](evaluation/personal_public_repositories.json)
是一套只引用公开资料的个人题集：`AgentSkill-Eval`、广告视频分析系统和 MemoryForge 本身。它不是
对公司资料或飞书正文的评测，也不会把本机路径写入结果。

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --source-repo /absolute/path/to/ad-video-analysis-system \
  --source-repo "$PWD" \
  --workspace /private/tmp/memoryforge-personal-demo \
  --output /private/tmp/memoryforge-personal-result.json \
  --eval-config demo/evaluation/personal_public_repositories.json \
  --question '广告视频分析系统默认依赖外部模型吗？'
```

题集用每个公开远程仓库的稳定 `repository_id` 区分同名的 `README.md`，因此不会把不同仓库的同名文件混为一谈。
