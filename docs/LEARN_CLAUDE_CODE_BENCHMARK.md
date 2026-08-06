# learn-claude-code 外部 Code Wiki 评测

## 目标

使用公开仓库
[`shareAI-lab/learn-claude-code`](https://github.com/shareAI-lab/learn-claude-code)
验证 MemoryForge 对真实 Python Agent Harness 教程代码的确定性索引、关系解析、模块归属和
Citation grounding。

## 冻结范围

- Source Commit：`7b564c3ee6996039cb4e13a53024dfe2d4388d35`
- License：MIT
- 选定文件：
  - `s01_agent_loop/code.py`
  - `s03_permission/code.py`
  - `s05_todo_write/code.py`
  - `s06_subagent/code.py`
  - `s12_task_system/code.py`
- 人工标签：20 个 Symbol、15 条核心 Relation、5 个 Module assignment

只选择五个 canonical lesson 文件。没有导入 449 个仓库文件，也没有把 legacy track、Web
前端或翻译文档计入代码覆盖声明。

## 结果

MemoryForge 实际索引 5 个 Source、72 个 Symbol、111 条 Relation、10 个 Module，并生成
10 个 Code Wiki 页面。

| 指标 | 结果 |
| --- | ---: |
| Expected source coverage | 100% |
| Frozen Symbol recall | 100% |
| Frozen core Relation recall | 100% |
| Module assignment accuracy | 100% |
| Citation grounding accuracy | 100% |
| Deterministic replay | 100% |
| Wiki lint | clean |

选定文件之间没有跨 Module relation，因此 Mermaid edge、Architecture Citation 和 Mermaid
determinism 标记为不适用，不以 0% 解释为失败。

完整机器证据：
[`external_code_wiki_learn_claude_code_v031.json`](../demo/results/external_code_wiki_learn_claude_code_v031.json)。

## 复现

```bash
git clone https://github.com/shareAI-lab/learn-claude-code.git \
  /tmp/learn-claude-code
git -C /tmp/learn-claude-code checkout \
  7b564c3ee6996039cb4e13a53024dfe2d4388d35

.venv/bin/python demo/run_external_code_wiki_benchmark.py \
  --manifest demo/evaluation/external_code_wiki_learn_claude_code_sources_v031.json \
  --source-repo learn_claude_code=/tmp/learn-claude-code \
  --workdir /tmp/learn-claude-code-benchmark-v031 \
  --output demo/results/external_code_wiki_learn_claude_code_v031.json
```

该评测是非穷举正样本检查，不证明全仓 precision、跨语言 Web 前端覆盖或任意 Agent
代码库上的普遍性能。
