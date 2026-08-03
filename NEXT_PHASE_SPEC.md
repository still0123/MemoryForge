# MemoryForge 下一阶段 Spec：公开可复跑证据包

> 本阶段的目标不是继续堆功能，而是证明已经完成的 Wiki 内核真的可用、可复跑、可回源。

| 字段 | 内容 |
|---|---|
| 阶段 | Phase 5 收口：评测、复现与项目包装 |
| 执行方式 | 适合 Goal 模式连续执行；阶段必须按顺序完成 |
| 主仓基线 | `0feb379 feat: build progressive knowledge wiki` |
| 公开源仓基线 | `demo-sources/AgentSkill-Eval` 的 `93f5dc05229da250b041850ad8deeeec886ef304` |
| 已知测试基线 | `pytest --collect-only -q` 为 260 个测试 |
| 交付范围 | 仅 `MemoryForge` 主仓库；不修改或提交 `showcase/` 独立仓库 |

## 0. 这次到底要完成什么？

让任何人只用一个**已经克隆好的公开 Git 仓库**，就能从零跑出这条真实链路：

```text
公开 Git 文档
  -> git-add / git-sync
  -> ingest / review / apply
  -> ask --debug --verify
  -> lint / eval
  -> 一份可提交、可检查的公开证据 JSON
```

最终需要在主仓中留下四样东西：

1. 扩展后的公开题集：至少 8 道人工可核验的问题；
2. 一个不依赖 API Key、飞书登录或模型调用的复现脚本；
3. 脚本在指定公开 commit 上真实生成的证据 JSON；
4. README 中能在五分钟内复跑的说明。

这会让项目的核心卖点变得具体：不是“我写了很多 Adapter”，而是“我把多仓文档编译成 Wiki，并证明回答遵守页面预算、可回到原文、结果可复现”。

## 1. 明确不做什么

本阶段不要实现以下内容，即使它们看起来很酷：

- 不加 Embedding、向量数据库、重排模型或 LLM Judge；
- 不加 MCP、Agent Loop、后台任务、Web API、登录或多用户能力；
- 不加新的资料来源 Adapter；
- 不修改 SQLite schema、Blob/Manifest、ChangeSet 或路径安全机制；
- 不把 `showcase/` 变成子模块，也不改它的部署、页面或测试；
- 不调用 `--llm`，不读取 `.env`，不发送任何资料到模型；
- 不为了凑满分放宽评测规则、伪造运行产物或手写展示数字；
- 不实现新的 `benchmark`、`demo` 或 `export` CLI 命令。

现有的导入、编译、审核、渐进式查询、Lint、Refresh 和 Eval 都是本阶段的**既有能力**，只能复用，不能重写一遍。

## 2. Phase 0：事实核对与允许使用的接口

本阶段先只读检查。若基线不成立，停止后续改动并报告，而不是在错误状态上继续堆代码。

### 2.1 必须读取的现有实现

| 用途 | 参考位置 | 本阶段允许复用的真实接口 |
|---|---|---|
| 公开题集与指标 | `src/memoryforge/evaluation.py` | `run_evaluation(workspace_root, config_path)`；当前题集字段为 `id`、`question`、`expected_source_path`、`required_terms` |
| 日常查询 | `src/memoryforge/query.py` | `answer_question(workspace_root, question, debug=True, verify=True, max_pages=...)` |
| CLI 流程 | `src/memoryforge/cli.py` | `init`、`git-add`、`git-sync`、`ingest --pending`、`review`、`apply --approve`、`ask`、`lint`、`eval` |
| Git 文档同步 | `src/memoryforge/git_adapter.py`、`src/memoryforge/workspace.py` | 只读取本地 checkout 的 `HEAD`；不 clone、不 fetch、不访问远端 |
| 评测测试范式 | `tests/test_evaluation.py` | 使用 `CliRunner` 建立小 workspace，再调用真实 `eval` 命令 |
| 渐进式查询约束 | `tests/test_query_workflow.py` | 默认不读 Raw；`--verify` 才读 citation 对应原文；`max_pages` 为 1 到 10 |
| 公开源材料 | `demo-sources/AgentSkill-Eval/docs/*.md` | 只能选择公开、已提交、实际会被 GitAdapter 导入的 Markdown 文档 |

### 2.2 禁止假设的接口

- 不使用文档中已经过时的 `source add-local`、`source add-git`、`source list`；实际 CLI 没有这些命令。
- 不使用 `reject`、`history`、`rollback` 或 `ask --as-of`；它们目前是不可用占位。
- 不把 Citation 写成行号；当前可核验定位格式是 `chars:<start>-<end>`。
- 不从 `showcase/` 读取、复制或相信 `56/7/0`、changeset ID 等硬编码展示数据。
- 不在脚本中调用 `git clone`、`git fetch`、`topics` 或任何 Provider。

### 2.3 Phase 0 验收

```bash
git rev-parse HEAD
.venv/bin/memoryforge --help
.venv/bin/memoryforge eval --help
.venv/bin/pytest --collect-only -q

git -C ../demo-sources/AgentSkill-Eval status -sb
git -C ../demo-sources/AgentSkill-Eval rev-parse HEAD
```

继续条件：主仓基线没有意外未提交代码；公开源仓干净，且当前 commit 与本 Spec 的基线一致。

若公开源仓已经前进：可以继续在它的当前 `HEAD` 上运行，但不得覆盖本仓已提交的证据 JSON；最终报告必须写出实际 commit，并把“更新基线”留给用户确认。

## 3. Phase 1：把公开题集从 3 题扩展到 8 题

### 3.1 要改的文件

- `demo/evaluation/agent_skill_eval.json`
- `tests/test_evaluation.py`
- 只有出现可复现的通用查询缺陷时，才允许修改 `src/memoryforge/evaluation.py` 或 `src/memoryforge/query.py`。

不要因为某一题写得模糊就修改检索规则；先检查问题是否真的能由生成的 Wiki 页面回答。

### 3.2 题集要求

保留当前四字段 schema，不新增题型、标签、模型评分或人工评分字段。题目必须来自源文档的实际明确表述，并尽量覆盖不同主题。

目标题目如下；实现时必须重新读取对应文档，确认每个 `required_terms` 真正出现在可回答的事实中。

| id | 问题意图 | `expected_source_path` | 初始 `required_terms` |
|---|---|---|---|
| `statistics-truth-source` | 统计器如何避免把模拟结论当真实结论 | `docs/statistics-and-reports.md` | `Manifest`, `RunMeasurement` |
| `trace-boundary` | Trace Intelligence 不保存什么 | `docs/trace-intelligence.md` | `隐藏思维过程`, `unavailable` |
| `real-agent-boundary` | Real Agent 模块会不会引入新的 Provider 编排 | `docs/real-agent-evidence.md` | `Provider`, `模拟结果` |
| `local-engine-boundary` | 本地配对实验引擎依赖哪些服务 | `docs/local-experiment-engine.md` | `Redis`, `Celery` |
| `evidence-skill-activation` | 为什么“配置声明了 Skill”不足以证明 Skill 生效 | `docs/evidence-and-replay.md` | `Skill`, `Secret` |
| `promotion-simulation-boundary` | Promotion Workflow 当前接受什么证据、不会做什么 | `docs/promotion-workflow-integration.md` | `simulated=true`, `真实 Agent` |
| `memory-rag-boundary` | Memory/RAG MVP 刻意不用哪些能力 | `docs/memory-rag-evaluation.md` | `LLM Judge`, `Embedding` |
| `observed-failure-boundary` | Observed Failure Evidence Bridge 是否重新运行 Agent | `docs/observed-failure-evidence-bridge.md` | `FailureEvidenceBundle`, `不重新运行` |

题集允许在读取后的确切措辞上做小幅调整；不允许为了让 FTS 容易命中而把问题直接复制成原文整句。

### 3.3 必须新增的测试

在 `tests/test_evaluation.py` 增加两个最小测试：

1. 两道题中一道答对、一道路由或引用错误时，`answer_accuracy` 或 `citation_accuracy` 会真实降低；
2. Eval 仍然走日常 `answer_question(..., debug=True)` 路径，正常查询的 `average_raw_sources_read` 为 `0`；Citation audit 的 Raw 读取单独计入 `average_citation_audit_characters`。

不得为 Eval 新建独立的检索或评分管线。它必须继续调用现有 `answer_question`、`search_sources` 和 `read_source_excerpt`。

### 3.4 如果新题失败，按这个顺序排查

1. 用 `ingest -> review` 查看该文档产生的 Wiki 页面，确认事实确实进入了 `## Verified facts`；
2. 用 `ask <question> --debug --verify` 看是 INDEX 路由、页面事实匹配还是 citation 出错；
3. 如果多个文档都因为“确定性编译器只保留一段事实”而不可答，才允许把确定性编译的事实提取从 1 段扩到最多 3 段，并为每段保留现有 `chars:` Citation；
4. 如果只是某道题选择了页面里没有的事实，修改题目或换成同一文档的明确事实，不修改核心检索。

禁止跳到 Embedding、切块 RAG 或模型评分。

### 3.5 Phase 1 验收

```bash
.venv/bin/pytest -q tests/test_evaluation.py tests/test_query_workflow.py
.venv/bin/ruff check src tests
.venv/bin/mypy src/memoryforge
```

完成条件：题集为 8 题；新增测试证明坏答案或坏引用会降低指标；没有新增依赖。

## 4. Phase 2：新增一个可复跑的公开证据脚本

### 4.1 要新增的文件

- `demo/run_public_demo.py`
- `tests/test_public_demo.py`
- `demo/README.md`

这个脚本不是新产品功能，也不注册新的 CLI 命令。它只是把已经存在的 CLI 按真实用户路径串起来。

### 4.2 脚本接口

脚本必须只接受显式路径，建议形式：

```bash
.venv/bin/python demo/run_public_demo.py \
  --source-repo /absolute/path/to/AgentSkill-Eval \
  --workspace /absolute/path/to/empty-memoryforge-workspace \
  --output /absolute/path/to/evidence.json
```

规则：

- `--source-repo` 必须是已经存在的本地 Git checkout；脚本只读取它的 `HEAD`，不 clone/fetch/checkout；
- `--workspace` 必须由调用者明确给出，并且必须不存在或为空；脚本绝不删除已有目录；
- 使用 `sys.executable -m memoryforge` 调用现有 CLI，避免依赖用户 shell 中的 `memoryforge` 路径；
- 默认走确定性 `ingest --pending`，不传 `--llm`；
- 正常执行 `review`，但不把巨大的完整 Diff 写入结果；
- 对题集运行 `lint`、`eval`，并对一条固定问题运行 `ask --debug --verify`；
- 任一步骤非零退出就保留 workspace 并清晰失败，不生成“成功”证据文件。

### 4.3 脚本必须编排的命令顺序

```text
memoryforge init <workspace>
memoryforge git-add <source-repo> --public --workspace <workspace>
memoryforge git-sync <repository-id> --workspace <workspace>
memoryforge ingest --pending --workspace <workspace>
memoryforge review <changeset-id> --workspace <workspace>
memoryforge apply <changeset-id> --approve --workspace <workspace>
memoryforge lint --workspace <workspace>
memoryforge ask <固定问题> --debug --verify --workspace <workspace>
memoryforge eval demo/evaluation/agent_skill_eval.json --workspace <workspace>
```

`repository-id` 和 `changeset-id` 必须解析上一步的真实 JSON 输出，不能写死。

### 4.4 证据文件格式

输出必须是稳定、可提交的 JSON，建议如下。不要写时间戳、绝对路径、API Key、环境变量、内部资料路径或 workspace 内部 Git commit。

```json
{
  "schema_version": 1,
  "memoryforge_commit": "<主仓 HEAD>",
  "source_repository": {
    "remote_url": "https://github.com/ranmaoxia0123/AgentSkill-Eval.git",
    "commit": "<源仓 HEAD>"
  },
  "workflow": {
    "source_count": 0,
    "wiki_file_count": 0,
    "lint": {}
  },
  "sample_query": {
    "question": "...",
    "answer": "...",
    "citations": [],
    "trace": []
  },
  "evaluation": {}
}
```

`source_count`、`wiki_file_count` 和所有指标必须来自本次真实命令输出或实际生成文件，不能是页面展示常量。

### 4.5 最小脚本测试

`tests/test_public_demo.py` 只需要一个端到端 smoke test：

- 在 `tmp_path` 创建只有 README 和一份 `docs/` Markdown 的微型 Git 仓库；
- 调用脚本并生成临时 workspace、证据 JSON；
- 断言 JSON 包含源仓 commit、非空 `sample_query.citations`、`evaluation` 和 clean Lint；
- 不运行真实 `AgentSkill-Eval`、不访问网络、不使用模型。

不要 mock 脚本内部每一个子进程；这个测试的价值就是证明真实 CLI 串联正确。

### 4.6 Phase 2 验收

```bash
.venv/bin/pytest -q tests/test_public_demo.py tests/test_evaluation.py
.venv/bin/ruff check demo src tests
.venv/bin/mypy src/memoryforge
```

完成条件：微型仓库 smoke test 全绿；脚本在没有 API Key 的环境也可以运行。

## 5. Phase 3：生成并提交真实公开证据

### 5.1 执行目标

对本 Spec 固定的公开源仓 commit 运行一次脚本，并把真实结果提交到：

```text
demo/results/agent_skill_eval_public.json
```

脚本的输入 workspace 应使用 `/private/tmp` 下一个明确的新目录；不要删除 `memoryforge-demo` 或其他已有工作区。

### 5.2 可复现性检查

用两个不同的空临时 workspace 连续运行两次脚本。比较两个 JSON：

- 必须有相同 `memoryforge_commit`、源仓 commit、题集结果、评测汇总、sample query answer/citation/trace；
- 不同的临时目录绝不能进入 JSON；
- 如果输出不同，先找时间戳、绝对路径、随机顺序或未排序集合，修正脚本输出；
- 不要通过删除有价值的字段来掩盖不稳定性。

### 5.3 Phase 3 验收

```bash
python -m json.tool demo/results/agent_skill_eval_public.json > /dev/null
git diff --check
```

完成条件：证据 JSON 来自真实运行、仅含公开材料、两次运行一致；正常查询的 `average_raw_sources_read` 必须为 `0`。Raw FTS 的召回指标只作基线记录，不要求它比 Wiki 更差或更好。

## 6. Phase 4：更新文档，只陈述已经证明的事实

### 6.1 要改的文件

- `README.md`
- `SPEC.md`
- `demo/README.md`

### 6.2 README 必须新增的内容

增加“从公开仓库复跑”的小节，给出：

1. 准备 `AgentSkill-Eval` 本地 checkout 的路径；
2. 一条运行 `demo/run_public_demo.py` 的命令；
3. 生成的证据 JSON 在哪里；
4. 读者能看到的指标含义：回答正确率、引用正确率、平均展开 Wiki 页数、正常查询读取 Raw 数、citation audit 字符数；
5. 明确不需要模型 API Key，且不可将公司内部仓库或飞书文档用于公开复现。

不要声称“MemoryForge 比 Raw FTS 准确”；当前评测只证明现有 Wiki 流程在这组公开问题上的行为和成本边界。

### 6.3 SPEC 必须校正的内容

只修正和本次交付直接相关的过时描述：

- 基线 commit 更新为本次实际提交；
- 真实统一输入模型名称是 `LocalDocument`，不是文档示例中的 `SourceDocument`；
- 实际 workspace 使用 `raw/`、`wiki/`、`.memoryforge/`，不要再写 `purpose.md`、`sources/`、`memoryforge.db`、`wiki/log.md` 为当前目录契约；
- 当前 CLI 使用平铺的 `import`、`git-add`、`git-sync` 等命令；
- Citation 定位写为 `chars:<start>-<end>`；
- Phase 5 标记为“公开证据包与五分钟复现已完成”仅在 Phase 3 的真实结果已提交时才允许更新。

不要在这轮补写完整的跨仓自动综合、历史查询或冲突处理；这些仍是后续方向。

### 6.4 Phase 4 验收

```bash
rg -n 'source add-|memoryforge\.db|wiki/log\.md|L12-L24' README.md SPEC.md
git diff --check
```

完成条件：README 的命令可以直接复跑；文档没有陈述未实现功能；所有展示指标都能回到 `demo/results/agent_skill_eval_public.json`。

## 7. Phase 5：最终验证与停止条件

最后必须按以下顺序完成。任一失败都要保留失败输出并修复直接原因；不要跳过验证。

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src/memoryforge
.venv/bin/pytest -W error::ResourceWarning -q
git diff --check

.venv/bin/python demo/run_public_demo.py \
  --source-repo ../demo-sources/AgentSkill-Eval \
  --workspace /private/tmp/memoryforge-public-demo-final \
  --output /private/tmp/memoryforge-public-demo-final.json
```

完成后再核对：

- 主仓只有本阶段预期的改动；
- `showcase/` 仍是未触碰的独立仓库；
- 没有 `.env`、token、绝对内部路径、飞书内容或公司代码进入 diff；
- 证据 JSON 的源仓 remote 和 commit 都真实存在；
- 测试数量不少于 260，并新增的测试均通过；
- 不创建提交、不 push、不部署；这些由用户之后单独确认。

## 8. Goal 模式的执行纪律

执行此 Spec 时，每完成一个 Phase 都应在最终报告中记录：修改文件、实际运行命令、测试结果、未解决事项。

遇到下面任一情况立即停止，并把事实交给用户决定：

- Phase 0 时主仓已有与本阶段无关的未提交改动；
- 公开源仓不干净或 commit 与基线不同；
- 需要 API Key、飞书权限、网络模型或远端 Git 操作才能继续；
- 要修改 `showcase/`、需要创建子模块、提交、推送或部署；
- 需要引入 Embedding、LLM Judge、新数据库或新服务才能让题集通过。

本阶段真正完成的标志不是“功能又多了”，而是这句项目介绍有了可检查的证据：

> MemoryForge 将公开 Git 文档编译为可审阅 Markdown Wiki；查询先展开有限页面、按需读取原文，并通过可复现题集验证答案、引用和读取成本。
