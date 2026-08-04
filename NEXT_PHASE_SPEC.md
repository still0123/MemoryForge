# MemoryForge 下一阶段 Spec：Compounding Wiki

> 把当前“资料可以编译成 Wiki、可以问答”的系统，补成一套会持续维护、会用评测证明效果的个人技术记忆系统。

| 字段 | 内容 |
| --- | --- |
| 状态 | Implemented v1；核心阶段已完成 |
| 基线 | 当前 `main` 工作树 |
| 主目标 | 完成“导入 → 编译 → 查询 → 发现 → 提议更新 → 审核应用 → 再查询”的知识复利闭环 |
| 主仓范围 | 仅 MemoryForge 主仓；不修改 `showcase/` 独立仓库 |
| 实现原则 | 复用现有模块；先补质量闭环，再考虑向量检索；所有稳定 Wiki 修改必须审核 |

## 1. 当前基线：已经有的能力不要重写

当前代码已经完成以下主链路：

```text
本地文件 / Git / 飞书文档 / 网页
              │
              ▼
       不可变 SourceVersion
              │
              ▼
         WikiCompiler
              │
              ▼
   PROPOSED ChangeSet → review → apply
              │
              ▼
   INDEX.md + Wiki 页面 + 来源引用
              │
              ▼
     ask / MiniClaude / 飞书机器人
```

可直接复用的真实接口：

| 能力 | 现有入口 |
| --- | --- |
| Wiki 编译 | `compile_pending_sources(...)`、`compile_repository_topics(...)` |
| 变更审核 | `ChangeSetStore`、`review`、`apply --approve`、`reject` |
| 渐进式查询 | `answer_question(...)` |
| 最小 Agent | `run_agent(...)`，工具为 `search_wiki`、`read_evidence`、`final` |
| 结构检查 | `lint_workspace(...)` |
| 公开评测 | `run_evaluation(...)` |
| 数据接入 | `import`、`git-add/git-sync`、`feishu-import`、`web-import` |
| 飞书展示 | `feishu-reply`、`feishu-serve` |
| Workspace 规则 | 根目录 `AGENTS.md`、`.memoryforge/schema.yaml` |

因此，下一阶段不再增加新的资料接入器，也不再做另一个聊天壳子。

## 2. 参考项目和文章，具体借什么

| 参考 | 借鉴内容 | MemoryForge 中的落点 |
| --- | --- | --- |
| [Karpathy：LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | Raw / Wiki / Schema 三层；ingest、query、lint；查询发现可以反哺 Wiki | 让现有 `AGENTS.md` 与 `schema.yaml` 真正进入编译/Agent 提示词；增加审核式知识回写 |
| [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki) | 先规划再生成、检索预算、删除来源后的级联维护 | 增加简短 `CompilationPlan`；完善来源删除后的清理 ChangeSet |
| [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) | 小循环、工具注册与分发、明确终止状态、上下文预算 | 保留一个 MiniClaude 循环，拆出小型工具表并限制模型可见字符数 |
| [内部 Wiki 记忆文章](https://bytetech.info/articles/7664794244280352822) | 渐进式披露；先导航、再页面、最后原文；飞书作为日常入口 | 保持 `INDEX → Page → Evidence`，飞书只做真实能力的展示层 |
| `qmd` 一类本地混合检索工具 | 知识量大时补语义召回 | 只在扩展评测证明 FTS5 召回不足后启用，不预先接入 |

这里不照搬任何项目的完整架构。借的是经过验证的几个内核设计，最终代码仍围绕 MemoryForge 当前的数据模型和 CLI 展开。

## 3. 下一阶段完成后的用户体验

用户应能完成下面这条闭环：

```bash
# 1. 同步并编译新增或更新的资料
memoryforge git-sync <repository-id> --workspace <workspace>
memoryforge ingest --pending --llm --allow-local-llm --workspace <workspace>
memoryforge review <changeset-id> --workspace <workspace>
memoryforge apply <changeset-id> --approve --workspace <workspace>

# 2. 用 Agent 查询多个页面和原始证据
memoryforge agent '为什么这个模块这样设计？' \
  --workspace <workspace>

# 3. 将这次查询得到的“跨资料总结”变成一个待审核 Wiki 更新
memoryforge agent '为什么这个模块这样设计？' \
  --propose-update \
  --allow-local-llm \
  --workspace <workspace>

# 4. 用户看 Diff 后决定是否沉淀
memoryforge review <changeset-id> --workspace <workspace>
memoryforge apply <changeset-id> --approve --workspace <workspace>

# 5. 检查知识库，并运行可复现评测
memoryforge lint --workspace <workspace>
memoryforge eval demo/evaluation/agent_skill_eval.json --workspace <workspace>
```

核心行为：Agent 生成的答案不能直接成为“事实”。它只能基于已经读取的原始引用，提出一个普通 `PROPOSED` ChangeSet，继续经过现有审核流程。

## 4. 优先级总览

| 优先级 | 阶段 | 结果 |
| --- | --- | --- |
| P0 | Phase 0–1 | 先建立可信的扩展评测，知道后续改动是否真的有帮助 |
| P1 | Phase 2–4 | 完成 Schema 驱动、知识回写、来源删除维护三条 Wiki 核心闭环 |
| P2 | Phase 5–6 | 把 MiniClaude 做扎实，并支持多仓范围检索 |
| P3 | Phase 7 | 只有指标触发时才增加混合检索；最后收口飞书演示 |

推荐第一次 Goal 执行到 Phase 4 为止。Phase 5–7 是第二批工作，不能为了“看起来完整”一次混在同一个超大提交里。

## 5. Phase 0：文档发现与基线记录

### 要做什么

在写代码前只读核对以下文件，记录当前行为和测试基线：

- `README.md`、`SPEC.md`、`AGENTS.md`；
- `src/memoryforge/compiler.py`；
- `src/memoryforge/query.py`；
- `src/memoryforge/agent.py`；
- `src/memoryforge/evaluation.py`；
- `src/memoryforge/linting.py`；
- `src/memoryforge/workspace.py`；
- `src/memoryforge/changesets.py`；
- 对应的 `tests/test_*.py`。

### 必须保留的约束

- Raw SourceVersion 不可被 Wiki 编译或 Agent 修改；
- 稳定 Wiki 只能通过 `review → apply --approve` 修改；
- `local_only` 内容只有显式 `--allow-local-llm` 才能发给已配置模型；
- 日常 `ask` 默认只读 Wiki，原文只用于核验；
- Citation 必须能回读到具体 SourceVersion 和 `chars:start-end`；
- 不把公司资料、Token、日志、Workspace 或生成结果提交到公开 GitHub。

### 验收

```bash
git rev-parse HEAD
.venv/bin/memoryforge --help
.venv/bin/pytest --collect-only -q
```

在实施记录中写出实际 commit、测试数量和已有未提交文件。不得覆盖用户原有改动。

## 6. Phase 1（P0）：把评测从“8 道关键词题”升级成真实质量闸门（已完成）

### 为什么先做

当前评测能证明最小链路可跑，但题量少，主要依赖关键词，无法回答下面这些问题：

- 多个仓库和文档进来后，正确来源还能不能进入前三页；
- 问题在资料里没有答案时，系统会不会硬答；
- 一个问题需要两份资料时，是否真的覆盖了两边证据；
- Wiki 更新后，旧事实是否退出答案；
- Agent 为了回答一题到底读了多少内容。

### 数据模型

直接将 `EvaluationCase` 迁移成一个明确的新结构，不维护两套长期兼容字段：

```python
class EvaluationCase(BaseModel):
    id: str
    category: Literal["single_hop", "multi_source", "unanswerable", "paraphrase"]
    question: str
    expected_status: Literal["answered", "unknown"]
    expected_source_paths: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
```

规则：

- `answered` 用例至少有一个 `expected_source_paths` 和一个 `required_terms`；
- `unknown` 用例不要求来源，必须检查系统是否明确拒答；
- `multi_source` 要求所有期望来源均被 Citation 覆盖；
- `forbidden_terms` 用于检查旧版本事实或明显错误事实没有出现在答案里；
- 不增加 LLM Judge，先保持评测确定、便宜、可复跑。

### 指标

`run_evaluation(...)` 至少输出：

- `answer_accuracy`：状态、必需词和禁止词是否正确；
- `source_recall_at_3`：期望来源是否进入实际展开的前三个 Wiki 页面；
- `citation_grounding_accuracy`：Citation quote 能否在对应 SourceVersion 中回读；
- `multi_source_coverage`：多资料题覆盖了多少期望来源；
- `abstention_accuracy`：无答案问题是否返回 `unknown/不知道`；
- `average_wiki_pages_read`；
- `average_raw_sources_read`；
- `average_evidence_characters`。

### 题集

将公开题集扩展到至少 30 题：

- 15 题 `single_hop`；
- 5 题 `multi_source`；
- 5 题 `unanswerable`；
- 5 题 `paraphrase`。

另外增加一个集成测试模拟“V1 资料 → 编译 → V2 资料 → 重新编译”，验证新答案包含 V2 事实且不包含配置的 V1 旧事实。该时序测试不强塞进静态 JSON 题集。

### 主要文件

- `src/memoryforge/evaluation.py`
- `demo/evaluation/agent_skill_eval.json`
- `demo/run_public_demo.py`
- `tests/test_evaluation.py`
- `tests/test_query_workflow.py`

### 验收目标

- Citation grounding 为 `100%`；
- `abstention_accuracy >= 90%`；
- `source_recall_at_3 >= 90%`；
- `multi_source_coverage >= 80%`；
- 平均展开 Wiki 页面数不超过 3；
- 默认查询的 Raw 读取数为 0；
- 指标没达到时如实保留结果，不准调整答案或题集来“刷分”。

### 验证

```bash
.venv/bin/pytest -q tests/test_evaluation.py tests/test_query_workflow.py
.venv/bin/python demo/run_public_demo.py \
  --source-repo <public-source-repo> \
  --workspace /private/tmp/memoryforge-eval \
  --output /private/tmp/memoryforge-eval.json
```

## 7. Phase 2（P1）：让现有 Schema 真正参与 Wiki 编译（已完成）

### 要做什么

MemoryForge 已在 Workspace 中生成 `AGENTS.md` 和 `.memoryforge/schema.yaml`，但主要规则仍硬编码在 Python 提示词里。本阶段不创建 `purpose.md`、`WIKI_SCHEMA.md` 或另一套配置，而是让已有文件成为运行时契约。

实现一个小函数，例如：

```python
def workspace_prompt_context(workspace: Workspace) -> str:
    """Read bounded AGENTS.md and schema.yaml text for model prompts."""
```

行为：

- 最多读取固定字符数，超出部分截断并在调试信息中标记；
- 编译器和 Agent 的 system prompt 都包含这段 Workspace 规则；
- 本地 Pydantic、路径、Citation、来源版本与敏感度校验仍是最终权威；
- 不解析成新的规则 DSL，不增加 YAML 依赖，不允许提示词绕过代码校验；
- 修正默认 `AGENTS.md` 的表述：允许 Workspace 保存个人获准使用的本地资料，但禁止将 `local_only` 资料公开提交；是否发送给模型继续由显式参数控制。

### 增加简短编译计划

借鉴 `llm_wiki` 的“先理解改哪里，再生成页面”，为 LLM ingest 增加一个小型 `CompilationPlan`：

```python
class PlannedPage(BaseModel):
    path: str
    action: Literal["create", "update"]
    source_ids: tuple[SourceId, ...]
    reason: str
    related_pages: tuple[str, ...] = ()


class CompilationPlan(BaseModel):
    pages: tuple[PlannedPage, ...]
    conflicts: tuple[str, ...] = ()
```

实现边界：

- 这是简洁的结构化计划，不保存或展示模型隐藏思维过程；
- 第一次 Provider 调用只产出计划，第二次调用根据计划生成 `PageChange`；
- 计划写进现有 ChangeSet 的 operation details，随 `review` 一起可见；
- `conflicts` 只是审核提示，不自动改旧知识；
- Provider 最多调用两次，不加重试框架、任务队列或缓存层；
- 确定性编译路径保持不变。

### 主要文件

- `src/memoryforge/models.py`
- `src/memoryforge/workspace.py`
- `src/memoryforge/provider.py`
- `src/memoryforge/compiler.py`
- `src/memoryforge/agent.py`
- `tests/test_llm_compiler.py`
- `tests/test_agent.py`
- `tests/test_foundation_integration.py`

### 验收

- 修改 Workspace `AGENTS.md` 中一条编译要求，Fake Provider 收到的新 prompt 中可看到它；
- 修改 `schema.yaml` 后，编译 prompt 可看到新内容；
- Provider 给出的计划引用未知来源或越界页面时，在生成稳定 Wiki 前失败；
- `review` 能显示每个页面为什么创建或更新；
- 确定性 `ingest` 不调用 Provider；
- 原有编译、Citation 和隐私测试继续通过。

### 验证

```bash
.venv/bin/pytest -q \
  tests/test_llm_compiler.py \
  tests/test_agent.py \
  tests/test_foundation_integration.py
```

## 8. Phase 3（P1）：完成“问答发现 → Wiki 更新提议”的知识复利闭环（已完成）

### 用户接口

在现有命令上增加一个参数，不另建 `learn` 子系统：

```bash
memoryforge agent '<question>' \
  --propose-update \
  --allow-local-llm \
  --workspace <workspace>
```

返回值在原有 Agent Payload 上增加：

```json
{
  "status": "answered",
  "answer": "...",
  "citations": [],
  "changeset_id": "chg_..."
}
```

没有值得沉淀的内容时，`changeset_id` 为 `null`，不能为了有输出而创建空页面。

### 行为规则

1. 先完整执行现有 evidence-first Agent；
2. 只有 `status=answered` 且至少一个 Citation 已经由 `read_evidence` 读过，才允许提议；
3. 候选页面只来自本次展开过的 Wiki 页面；
4. 首版最多创建或更新一个页面，优先更新已有 synthesis/concept 页面；
5. Provider 可阅读问题、最终答案、已读原文片段和候选页面，但页面中的事实必须引用原始 SourceVersion；
6. Agent 的自然语言答案不是 Source、不是 Citation，也不能单独成为 VERIFIED 事实；
7. 结果必须存为现有 `PROPOSED` ChangeSet；不能直接写 `wiki/`；
8. 用户继续使用现有 `review`、`apply --approve` 或 `reject`；
9. `local_only` 引用仍要求显式 `--allow-local-llm`；
10. 飞书聊天默认不自动触发知识回写。

### 复用方案

- 复用 `PageChange`、本地 Citation 校验、页面渲染和 `ChangeSetStore`；
- 将编译器中与“验证并渲染一个 PageChange”有关的逻辑提取成小函数即可；
- Provider 只增加一个返回 `PageChange | None` 的方法；
- 不新增数据库表、不建立 Conversation/Memory/Task 模型；
- 不实现自动合并多个 ChangeSet。

### 主要文件

- `src/memoryforge/agent.py`
- `src/memoryforge/provider.py`
- `src/memoryforge/compiler.py`
- `src/memoryforge/cli.py`
- `tests/test_agent.py`
- `tests/test_llm_compiler.py`
- `tests/test_compiler_workflow.py`

### 验收

- 跨两份资料提问后能得到带原始引用的答案和一个 `changeset_id`；
- `review` 显示最多一个页面的真实 Diff；
- `apply --approve` 后再次查询能命中新沉淀的综合页面；
- `unknown`、没有已读证据或 Provider 返回 `None` 时不创建 ChangeSet；
- 伪造 Source ID、过期 SourceVersion、未读 Citation 或越界页面路径会被现有本地校验拒绝；
- 命令不传 `--propose-update` 时行为与现在完全一致。

### 验证

```bash
.venv/bin/pytest -q \
  tests/test_agent.py \
  tests/test_llm_compiler.py \
  tests/test_compiler_workflow.py
```

## 9. Phase 4（P1）：补齐来源删除后的 Wiki 生命周期（已完成）

### 当前缺口

`git-sync` 已能把仓库里被删除的文档标记为不再 current，`lint` 也能指出页面引用了失效来源，但系统还不能生成一份可审核的清理方案。

### 要实现的行为

继续复用 `ingest --pending`，不要新增后台维护服务：

- 如果页面只依赖已删除来源，提出 `ARCHIVE_PAGE`；
- 如果页面同时依赖有效和已删除来源，使用剩余有效来源重编译该页；
- 同一个 ChangeSet 中重建 `wiki/INDEX.md` 并移除失效的生成链接；
- 所有删除或重写都必须出现在 `review` Diff 中；
- `apply` 显式批准后才删除稳定页面；
- 不删除 Raw Blob、历史 Manifest 或 Git 历史；
- 不静默删除仍被其他来源支持的共享知识。

### 最小数据改动

`ChangeOperationType.ARCHIVE_PAGE` 已存在，但 `apply` 目前只会写 candidate files。为它增加明确的 `deleted_files`/archive paths 语义即可；不要为此引入墓碑表或垃圾回收系统。

### Lint 增量

增加两个确定性检查：

- `orphan_page`：非仓库导航页既不在 INDEX，也没有其他页面链接；
- `cleanup_required`：页面仍引用已经没有 current version 的来源。

矛盾、过期语义和知识缺口可在以后用模型给出 advisory 报告，但本阶段不把不稳定的模型判断设成 apply 阻断条件。

### 主要文件

- `src/memoryforge/compiler.py`
- `src/memoryforge/changesets.py`
- `src/memoryforge/cli.py`
- `src/memoryforge/linting.py`
- `tests/test_git_sync.py`
- `tests/test_compiler_workflow.py`
- `tests/test_lint_workflow.py`

### 验收

- 删除单来源文档后产生可审核的归档 Diff，批准后页面和 INDEX 条目消失；
- 删除共享页面中的一个来源后，该页仍存在，只移除失效内容和 Citation；
- Reject 后稳定 Wiki 完全不变；
- 归档不会删除历史 SourceVersion 和 Raw；
- `lint` 能明确区分“需要清理”和普通断链。

### 实现结果

- 已实现 `cleanup_required` 和 `orphan_page` 两类确定性 Lint 问题；
- 已实现单来源页面归档、共享页面按剩余 current 来源重建；
- `INDEX.md`、仓库总览页和关系页会随归档/重建候选一起更新；
- `apply --approve` 支持 `ARCHIVE_PAGE`，`reject` 会归档提案且不触碰稳定 Wiki；
- Raw Blob、SourceVersion、Manifest 和 Git 历史均保留。

## 10. Phase 5（P2）：把 MiniClaude 做成小而完整的 Agent（已完成）

### 要借鉴 learn-claude-code 的部分

保留当前短循环，但把工具定义和执行从 `run_agent(...)` 的条件分支中拆成一个简单字典：

```python
TOOLS = {
    "search_wiki": search_wiki,
    "read_evidence": read_evidence,
}
```

只解决当前真实问题：

- 工具参数在分发前校验；
- 未知工具和非法参数作为 tool observation 返回，不让循环崩溃；
- 每次调用有稳定 `call_id`，多个 `read_evidence` 结果不会混淆；
- 终止状态明确为 `answered`、`unknown`、`max_steps`、`provider_error`；
- 最终 Citation 必须全部对应已读证据。

### 上下文预算

增加常量而非配置框架：

- 最多 3 个 Wiki 页面；
- 最多 6 个 Citation 候选；
- 每个 evidence excerpt 最多 2,000 字符；
- 单次工具结果最多 8,000 字符；
- 普通模型上下文不包含 debug trace；
- Agent Payload 记录 `wiki_pages_read`、`evidence_characters`、`tool_result_characters`。

不实现多轮会话压缩，因为当前 Agent 只处理一次问题，最多 4–8 步。

### 明确不加的工具

- Shell；
- 任意文件读写；
- Git push；
- 飞书文档写入；
- MCP；
- 子 Agent；
- 任务管理。

### 主要文件与验收

- `src/memoryforge/agent.py`
- `src/memoryforge/provider.py`
- `tests/test_agent.py`

验收覆盖：一次搜索后读取多个证据、非法工具、非法参数、Provider 错误、步数耗尽、未读 Citation、预算截断。随后运行：

```bash
.venv/bin/pytest -q tests/test_agent.py tests/test_provider.py
```

### 实现结果

- Agent action 改为由循环统一分发，未知工具和缺失参数会作为 observation 返回；
- Provider 错误会收敛为 `provider_error` 终态，不把一次模型失败变成 CLI 崩溃；
- 每轮有稳定 `call_id`，最终引用必须来自已读证据；
- 支持最多 6 个 Citation，并执行 3 页、2,000 字符 evidence、8,000 字符 tool result 上限；
- Agent Payload 记录 `wiki_pages_read`、`evidence_characters` 和 `tool_result_characters`。

## 11. Phase 6（P2）：知识变多时先做范围路由，不急着上向量库（已完成）

### 用户接口

为查询入口增加可选仓库范围：

```bash
memoryforge search '<query>' --repository <repository-id> --workspace <workspace>
memoryforge ask '<question>' --repository <repository-id> --workspace <workspace>
memoryforge agent '<question>' --repository <repository-id> --workspace <workspace>
```

### 实现方式

- 用现有 `git_source_revisions.repository_id`、`page_sources` 和页面映射过滤候选；
- 不指定范围时保持全局查询；
- 路由顺序保持 `INDEX/仓库概览 → 主题页面 → 最多三页 → 按需原文`；
- 两个仓库即使有同名模块，指定 repository 后也不能返回另一个仓库的页面；
- 飞书层只透传问题，首版不在聊天里设计复杂的仓库选择卡片。

### 验收

- 两个仓库含相同关键词时，范围查询只返回指定仓库；
- 全局查询行为不变；
- 页面预算仍然有效；
- 扩展题集增加多仓同名模块测试。

### 实现结果

- `search_sources` 和 `find_applied_page_paths` 支持按 Git `repository_id` 过滤；
- `answer_question` 会同时过滤 INDEX 候选和 FTS 候选，避免同名页面跨仓混入；
- `agent`、`ask`、`search` CLI 均支持 `--repository`；
- 不指定范围时保留原有全局行为，LocalFile 来源仍可参与全局查询；
- 增加了两个仓库含同名关键词时的隔离测试。

### 主要文件

- `src/memoryforge/workspace.py`
- `src/memoryforge/query.py`
- `src/memoryforge/agent.py`
- `src/memoryforge/cli.py`
- `tests/test_query_workflow.py`
- `tests/test_agent.py`
- `tests/test_evaluation.py`

## 12. Phase 7（P3，条件触发）：是否需要 qmd / 混合检索

这一阶段默认跳过。只有 Phase 1 的公开评测同时满足下面条件才开始：

1. `paraphrase` 类 `source_recall_at_3 < 85%`；
2. 错误分析证明是词法召回问题，而不是 Wiki 内容本身没生成好；
3. 在固定题集上，候选混合检索能提升至少 10 个百分点；
4. 平均展开页面仍不超过 3，延迟和依赖可接受。

如果触发，先做一个独立实验分支比较：

```text
当前 INDEX + FTS5
vs.
页面级 BM25/Embedding 候选合并 + 简单去重
```

实现原则：

- 只给 Markdown Wiki 页面建立语义索引，不把整个 Raw 切成海量向量块；
- 优先评估现成的本地 qmd 适配器，不先自研服务；
- 向量只负责召回，Citation 仍来自 SourceVersion；
- 没达到指标就删除实验代码，继续使用 FTS5；
- 不引入常驻服务、云向量库或知识图谱。

### Phase 7 实验结论（2026-08-04）

已用固定 30 题和公开 Demo workspace 跑完独立页面级实验。本机没有 qmd 或
sentence-transformers，因此只测试了标准库字符 n-gram 代理：

- 当前 INDEX/FTS5 页面候选：总体 source_recall_at_3=100.0%，paraphrase=100.0%；
- n-gram 代理：总体 100.0%，paraphrase=100.0%；
- 召回变化 0 个百分点，平均页面预算均为 3 页。经过后续引用选择优化，同期完整问答链路的
  答案准确率为 96.7%，source recall 为 96.0%。修正“空 Citation 也算成功”的口径后，引用落地准确率
  为 96.0%，对应同一条未命中的同义改写题。

结论：不把实验代理并入生产检索，继续使用当前 FTS5 路径。未来只有拿到真正的本地
语义后端，并在同一题集上满足本节门槛时，才重新评估。

## 13. 飞书在下一阶段的位置

飞书是项目功能，也是主要展示入口，但不是 Wiki 真值层。本阶段只做必要联动：

- 问答继续复用同一个 `answer_question/run_agent`；
- 回复展示自然语言答案、可读来源标题和简短定位，不显示长哈希路径；
- 记录耗时拆分：检索、原文读取、模型生成；
- Agent 失败或证据不足时明确回复“当前知识库没有足够证据”；
- 严格词法未命中但已有候选 Wiki 页时，只有显式启用模型才允许模型在这组有限事实内改写；
  不启用模型时仍返回“不知道”；
- 不自动把每条飞书聊天写回 Wiki；
- `--propose-update` 首版只在 CLI 显式触发，演示时再展示 review/apply。

不要在这个阶段做群聊权限、多轮上下文、交互卡片、Web 管理后台或定时同步。

## 14. 全局反过度设计规则

执行每个 Phase 时都要遵守：

- 能给现有函数加一个参数，就不创建新 service/repository/manager 层；
- 能复用 `PageChange` 和 `ChangeSet`，就不创建第二套“Memory”数据模型；
- 不为单测构造通用依赖注入框架；现有 Fake Provider 足够；
- 不为可能出现的未来客户端提前做 HTTP API 或 MCP；
- 不增加无需求的重试、缓存、队列、锁和后台任务；
- 不在业务层反复做哈希比较；哈希只保留在 SourceVersion、Citation 和 staging 完整性这些已有真值边界；
- 不捕获宽泛异常后吞错；CLI 只把已知错误转成可读信息；
- 新抽象至少被两个真实调用点复用，否则优先写普通函数；
- 一个 Phase 一个聚焦提交，不顺手重构无关代码。

## 15. 全量验收与交付物

### 必须通过

```bash
.venv/bin/ruff check src tests demo
.venv/bin/mypy src
.venv/bin/pytest -q
git diff --check
```

### 最终公开演示

使用公开仓库资料完成一次不依赖公司数据的演示：

```text
导入两个资料源
  → 编译并 review/apply
  → 问一个需要跨资料的问题
  → Agent 读取两份原始证据后回答
  → --propose-update 生成一份综合 Wiki Diff
  → review/apply
  → 再次查询命中新页面
  → lint
  → 30 题 eval
```

仓库最终留下：

- 更新后的 `README.md`；
- 本 Spec；
- 至少 30 题的公开评测集；
- 脱敏、可复跑的公开结果 JSON；
- 一张展示“知识复利闭环”的架构图；
- 不包含任何公司文档正文、内部仓库路径、飞书 Token 或模型 Key。

## 16. Definition of Done

只有同时满足以下条件，下一阶段才算完成：

- [x] Workspace 规则真实进入 LLM 编译和 Agent prompt；
- [x] LLM ingest 先产生可审核的简短页面计划，再生成页面；
- [x] Agent 能基于已读原始证据提出最多一个 Wiki ChangeSet；
- [x] 所有稳定 Wiki 修改仍经过 `review → apply --approve`；
- [x] Git 来源删除后可以生成可审核的页面清理方案；
- [x] MiniClaude 的工具调用、终止状态和上下文预算可测试；
- [x] 多仓查询可以按 repository 范围过滤；
- [x] 公开题集不少于 30 题，包含多来源、拒答和改写问题；
- [x] Citation grounding 不再把空引用算作成功；当前 96.0%，失败案例如实提交；
- [x] 语义实验没有达到增益门槛，因此不引入向量库、qmd、MCP 或知识图谱；
- [x] README 提供与 Raw FTS 的同题集对照，解释为什么它不是普通 RAG。

## 17. Goal 模式执行约定

每完成一个 Phase：

1. 只运行该阶段相关测试；
2. 输出新增行为、测试结果和仍未解决的问题；
3. 检查 `git diff --check`；
4. 形成一个聚焦提交；
5. 再进入下一 Phase。

遇到下面情况立即停止并报告，不要用更多防御代码绕过去：

- 需要改变 SourceVersion/Citation 的真值定义；
- 需要把 `local_only` 资料发给模型但用户没有显式授权；
- 需要覆盖现有未提交改动；
- 评测表明问题来自 Wiki 内容质量，而计划却准备堆检索基础设施；
- 一个 Phase 的实现开始引入第二套 Agent、第二套 ChangeSet 或通用任务框架。
