# Progressive Structure & Retrieval Specification (v3)

## Status

`DRAFT_FOR_REVIEW`

- 版本：v3。取代 v2（归档 `docs/research/progressive-structure-retrieval-spec-v2.md`）。
- 评审轮次：2（第二轮 5 个 P0 阻断项全部吸收，见 §12）。
- 基线锚点 commit：`11e2285e03736e2490d31e31a39b37e8313f4b5`。
- 既有冻结基线（`EXACT_SYMBOL_ROUTING_SPEC.md`）：answer accuracy 60.0%；
  page route recall@3 100.0%；source recall@3 100.0%；citation grounding 88.9%；
  abstention 0.0%（含 1 个已知未拒答失败，冻结不动）。

## 0. 实施前置（W0）

1. 干净 worktree 自 `11e2285`；远端不出现中间态，v3 与归档一次性提交。
2. 基线全绿：`ruff` / `mypy` / `pytest`。当前 mypy 有 2 个既有错误
   （`retrieval_v2.py` no-any-return、`index_rendering.py` redundant-cast），
   属 W0 修复项。

## 1. 问题陈述（v3：按层实测重写）

CJK 检索现状逐层核实（基线 commit 实测，Portal 查询 "存策略" 等纯 bigram 命中 4 页）：

| 层 | 实现 | CJK 状态 |
| --- | --- | --- |
| 页面搜索 `source_fts` | `search_terms` 列写入单字 + bigram（`workspace.py` `_search_terms`） | **正常** |
| 检索泳道 `retrieval_v2._tokenize` | 内存 TF-IDF，查询侧 bigram | **正常** |
| 事实页定位 `find_fact_pages` | `instr()` 子串匹配 | **正常** |
| 事实 FTS `search_applied_wiki_facts`（`wiki_fact_fts`） | external-content trigger 索引原文列，CJK run 成整 token | **子短语失效**；且经 grep 确认 src/tests/demo 均无调用方（休眠路径） |

残余风险：(a) `_search_terms` 与 `_tokenize` 两套分词实现并存，漂移风险；
(b) 无 golden 分词测试；(c) 休眠的事实 FTS 路径一旦被启用即踩坑。

结构性缺口不变：全局性问题无落点（P2）；渐进式缺"地图"跳（P3，
目标四跳 `INDEX → map → 页面 → 证据`）。

## 2. 目标与非目标

- G1 **abstention 正向门禁**：新增 CJK/全局/map-first 套件各内嵌 ≥5 条
  无本地证据的"不可答"问题，要求 100% 不编造项目事实（`no_local_evidence`
  或拒答）；既有符号路由套件的 wrong-abstention 数 ≤ 冻结基线（1）。
  通用 abstention 提升仍属 support-score 阶段（显式 de-scope，不做隐式承诺）。
- G2 **分词等价与休眠路径修复**：共享分词重构行为等价（golden 测试证明）；
  `wiki_fact_fts` 子短语召回修复并有单测覆盖。撤销 v2 的 "+15pp"——
  页面级中文检索本已正常，无真实基线可提升。
- G3 全局问题：`global_questions_dev_v001` route recall@3 ≥ 80%（map-first 生效）。
- G4 map-first：冻结 map-first 基准上**首轮响应字符数 median −50%**、
  p95 不劣化；查询延迟 p95 容差 +10%。

非目标：无向量库；无持久 Atlas 页面/新 Citation 类型/slug/增量 PageRank；
不改变 PROPOSED 与 Citation 互锁不变量；首版**不设 `mcp.map_first` 配置开关**
（无配置加载器，为一个布尔建配置系统不值得；路由由 route_rules 决定）；
Phase C 搁置。

## 3. 变更通道边界

- 内容通道（ChangeSet/Git）与派生通道（migration 命令）分离，同 v2。
- **迁移版本管理（v3 新增）**：`PRAGMA user_version` 记录库结构版本；
  代码现状（表存在性探测）保持兼容读取，迁移命令按 user_version 判断是否执行。
- **撤销"保留旧 FTS 表一版"**：失败回滚由单事务保证；代码版本回退 =
  重跑重建命令。与"派生态可重建 + 体积预算"一致。

## 4. W1：共享分词 + 事实 FTS 修复

1. 新建 `core/tokenization.py`：
   - `index_terms(text) -> list[str]`：Latin/数字小写原词；CJK run 保留单字
     + bigram（与现 `_search_terms` 语义**逐字节等价**）。
   - `bigram_tokens(text) -> list[str]`：CJK run 仅 bigram（供
     `retrieval_v2` TF-IDF，与现行为等价）。
2. `storage/workspace.py::_search_terms` 与 `query/retrieval_v2.py::_tokenize`
   改为委托共享实现；行为不变，golden 单测锁定（纯 CJK / 混合 / 空串 /
   标点边界 / latin 大小写）。
3. **事实 FTS 修复（派生列方案，v3 采纳评审建议）**：
   - `wiki_facts` 增列 `search_terms TEXT NOT NULL DEFAULT ''`（写入投影时
     由 `index_terms(routing_text + quote + section_path + symbol)` 计算）；
   - `wiki_fact_fts` 重建为**单列** external-content 表（仅 `search_terms` 列，
     `content='wiki_facts'`），trigger 复制 `new.search_terms`；
   - `search_applied_wiki_facts` 查询改为列限定 `search_terms:(...)`；
   - 休眠状态记录在案：修复期间无调用方，风险受控。
4. `memoryforge reindex-fts --workspace [--dry-run]`：单事务、幂等、
   `PRAGMA user_version` 提升；重算 `wiki_facts.search_terms` 后对两张
   FTS 表执行 `rebuild`；输出前后体积对比。
5. `workspace_contract.py` 同步新列与虚拟表定义。

## 5. W2：map-first 响应协议（v3 重塑）

**目标契约：全局问题走 map-only envelope，非全局问题行为完全不变。**

`memoryforge_context(question, ...)` 新增可选参数 `page_paths: list[str]`：

- **全局问题**（W3 判定）且未提供 `page_paths` → 返回
  `{status: ok, mode: "map", navigation_only: true, map: [...], guidance: ...}`：
  仅含地图（title / page_path / summary 行 / kind），**不含 answer 与 Citation**，
  并指引 AI 以 `page_paths=[...]` 二跳下钻；map 条目标记
  `navigation_only`，**不得作为回答证据**（工具描述与 MCP prompt 同步声明）。
- **下钻**：提供 `page_paths` → 仅返回这些页面的摘要 + Citation；
  repository scope 与 sensitivity/`visible_source` 过滤在**展开前**执行。
- 非全局问题：现行为不变（answer + pages + citations）。

**地图组装（首版，零 Portal 依赖）**：解析 `wiki/INDEX.md` 行
（`- [title](path) — summary`）+ 当前 repository 页面清单；
排序 = INDEX 分组（Entities/Concepts/…）内稳定序；**贪心前缀装填**至
4,000 字符。落点：`query/context_map.py`（新）。
Portal 关系边（direct/exact_mentions/same_project）留在
`portal_catalog.py`，首版不复用；若 G3 不达标再抽共享 `query/page_relations.py`。

## 6. W3：route_rules 全局判定

- 新建 `query/route_rules.py`：中英全局疑问词表（为什么 / 整体 / 架构 /
  how does X work overall …），数据与逻辑分离；
- 归一化：大小写、标点、词边界（英文按词匹配，中文按子串）；
- 判定：含全局词 **且** 无 `_EXPLICIT_CODE_IDENTIFIER` 符号命中 → global；
- golden 单测四类：纯全局词 / 全局词+符号 / 纯符号 / 普通词。

## 7. W4：Mermaid 架构图（独立 UI 轨道）

- **复用既有 `compiler/module_planner.py::build_architecture_graph()`**
  （v3 撤销 v2 的 `module_edges` 新表——确定性节点与边已存在）；
- 编译期在项目总览页追加 `flowchart TD` 块（≤30 节点，label slug 白名单）；
- Portal 端 `\`\`\`mermaid` → `<pre class="mermaid">`，同源 vendor
  mermaid.min.js（MIT）；**不触碰检索与路由**，独立合入独立回滚。

## 8. 检索质量兜底：全量 PageRank（条件触发，同 v2）

仅当 G3 未达标：页面关系图全量确定性重算，仅作 map/Top-N tie-breaker。

## 9. freshness 展示（v3 简化）

"落后 N commits" 现有数据不可计算，撤销。徽章只展示
**freshness state + base/current 短 SHA**（"基于 6ceeace · 当前 74c67f2"），
数据全部来自 `compiler/freshness.py` 现有投影；不参与排序权重。

## 10. 评测与门禁（v3 补全）

| 套件 | 指标 | 类型 |
| --- | --- | --- |
| 符号路由冻结套件 | route recall / accuracy / source recall / grounding / **fact selection / multi-source coverage / repository path isolation** | **硬门禁：全不回退** |
| 符号路由冻结套件 | wrong-abstention 计数 | **硬门禁：≤ 冻结基线（1）** |
| sensitivity 隔离既有断言 | local_only 不外泄 | **硬门禁** |
| 新三套件（CJK / global / map-first）各内嵌 ≥5 不可答问题 | 编造项目事实数 | **硬门禁：0** |
| 分词 golden + wiki_fact 子短语单测 | 等价性 / 命中 | **硬门禁：全绿** |
| `global_questions_dev_v001`（≥30 条） | route recall@3 | 目标 ≥80% |
| map-first 基准 | 首轮字符 median / p95；延迟 p95 | 目标 −50% / 容差 +10% |
| 工程 | ruff / mypy / pytest | **硬门禁：全绿** |

## 11. 交付顺序

```text
W0 基线（mypy 2 错误修复）
 └─ W1 共享分词 + 事实 FTS 修复（独立合入）
     └─ W3 route_rules → W2 map-first 协议（同批评测）
         └─ 条件触发：§8
W4 Mermaid（独立轨道，随时并行）
```

## 12. v2 → v3 变更归因（第二轮评审吸收）

| v2 条目 | 处置 | 原因 |
| --- | --- | --- |
| §1 P1 "子串/跨词全失败" | **重写为分层实测表** | source_fts/泳道/instr 路径本已正常；失效仅 wiki_fact_fts 且无调用方 |
| G2 "+15pp" | **撤销，改等价性目标** | 页面级中文检索无真实劣化基线 |
| W1 wiki_fact_fts 重建 | **改派生列方案** | external-content trigger 无法调用 Python；派生列 + 列限定 MATCH 是可稳定重建解 |
| W2 "新增 map 字段" | **重塑为 map-only envelope + page_paths 下钻** | 加字段与 G4 −50% 矛盾；四跳需要替换式响应与下钻参数；navigation_only 禁作证据 |
| 迁移"保留旧表一版" | **删除** | 与派生态可重建/体积预算冲突；事务回滚 + 重建即回退；引入 PRAGMA user_version |
| abstention "不回退(0%)" | **改正向门禁** | 0% 基线下"不回退"永真（空门禁） |
| `mcp.map_first` 开关 | **删除** | 无配置加载器 |
| W2 复用 portal 关系边 | **首版改 INDEX + repo 页面** | W2 不得依赖 Portal 层 |
| W4 `module_edges` 新表 | **删除，复用 build_architecture_graph()** | module_planner.py:104 已提供确定性节点与边 |
| freshness "落后 N commits" | **简化为 state + SHA** | 现有数据不可计算 |
| G4/延迟 | **改 median/p95 + 容差** | 笼统 −50% 不可测 |
| §10 | **补 4 项硬门禁** | fact selection / multi-source / path isolation / sensitivity |

## 13. 影响面清单

```
src/memoryforge/core/tokenization.py          (新, W1)
src/memoryforge/storage/workspace_contract.py  (W1 列/虚拟表/触发器)
src/memoryforge/storage/workspace.py           (W1 委托共享分词 + 派生列写入 + 迁移)
src/memoryforge/query/retrieval_v2.py          (W1 委托 + W0 mypy)
src/memoryforge/query/route_rules.py           (新, W3)
src/memoryforge/query/context_map.py           (新, W2)
src/memoryforge/interface/mcp_server.py        (W2 envelope + page_paths)
src/memoryforge/interface/cli.py               (W1 reindex-fts)
src/memoryforge/compiler/module_planner.py     (W4 复用, 不改)
src/memoryforge/compiler/code_wiki_compiler.py (W4 mermaid 注入)
src/memoryforge/portal/portal_assets.py        (W4 mermaid 渲染 / §9 徽章)
src/memoryforge/compiler/index_rendering.py    (W0 mypy)
tests/test_tokenization.py 等                  (新, 各 W)
```
