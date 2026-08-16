# Progressive Structure & Retrieval Specification

## Status

`DRAFT_FOR_REVIEW`

- 阶段：提案评审（未进入开发门禁）
- 基线锚点 commit：`11e2285e03736e2490d31e31a39b37e8313f4b5`
- 参考基线（沿用 `EXACT_SYMBOL_ROUTING_SPEC.md` 冻结结果）：
  - Answer accuracy: 60.0%
  - page route recall@3: 100.0%
  - Source recall@3: 100.0%
  - Citation grounding: 88.9%
  - abstention accuracy: 0.0%
- 本 spec 的冻结输入（套件 SHA256、confirmation 拆分）在每阶段进入开发前按
  `EXACT_SYMBOL_ROUTING_SPEC` 同样的流程补齐，不在提案阶段预填。

## 1. 背景与问题

当前 Wiki 是**单层平面结构**（模块页/文件页/会话页 + 关系边），检索是**多泳道平面召回**
（exact / lexical / relation / cross-repository，见 `query/retrieval_v2.py`）。这留下三类缺口：

1. **全局性问题没有落点。** "这个项目为什么这样设计 / 整体架构如何" 类问题只能靠
   `same_project` 边拉回一组平级页面，没有"先总览、再下钻"的抽象层。
2. **中文召回受限。** `source_fts` / `wiki_fact_fts` 使用 `tokenize='unicode61'`
   （`storage/workspace_contract.py:85`），CJK 按单字切分，长中文查询召回不稳定。
3. **渐进式只有两级跳。** INDEX → 页面 → 证据；缺少"预算化地图"这一跳，AI 第一跳
   就可能读到过重的页面内容。

同时存在三个工程性短板：页面文件名是 `<sha256>.md`（git diff 与深链不友好）、项目
总览页没有架构图（DeepWiki 类产品的核心观感差异）、页面新鲜度
（`compiler/freshness.py`）没有参与排序与展示。

对标项目：DeepWiki-Open / RepoWiki（Mermaid 架构图、PageRank 阅读路径）、
Aider repo map（符号图 + PageRank + token 预算二分）、RAPTOR（递归摘要树）、
LightRAG / GraphRAG（局部/全局双级检索）、Letta / mem0（分层记忆、事实衰减）。

## 2. 目标与非目标

### 目标

- G1 全局问题路由：新增摘要层后，全局类问题的 route recall@3 ≥ 80%。
- G2 中文检索：新建 CJK 评测套件，bigram 改造后 route recall@3 相对 baseline
  提升 ≥ 15 个百分点。
- G3 渐进式三跳：`memoryforge_context` 支持 map-first 返回，首轮上下文体积
  下降 ≥ 50%（按字符预算度量）。
- G4 观感与可读性：项目总览页含确定性 Mermaid 架构图；页面路径语义化。

### 非目标（沿用项目非目标清单）

- 不引入向量数据库与 embeddings 检索；语义泛化仍走确定性 `routing_text` /
  `search_terms` 路线。
- 不改变"AI 止步于 PROPOSED"与"Citation 哈希互锁"两条不变量（见 §8）。
- 不做实时在线 LLM 编译；LLM 摘要仅为可选 provider 且输出必为提案。
- 不做跨 workspace 联邦检索。

## 3. 总体设计：三阶段

| 阶段 | 内容 | 性质 | 预估 |
| --- | --- | --- | --- |
| Phase A | Mermaid 架构图、CJK bigram、slug 文件名 | 纯确定性、无行为语义变化 | 1–2 周 |
| Phase B | Atlas 摘要层、双级路由、PageRank 路径、rerank+新鲜度 | 结构与检索行为变化 | 3–4 周 |
| Phase C | 社区摘要（Leiden）、核心记忆块 | 研究向，不承诺 | — |

每个 Phase 独立走发布链（changeset 可单独 revert），并按"冻结基线 → 实现 →
confirmation 套件"的既有流程过门禁。

---

## 4. Phase A 详细设计

### A1 项目总览页 Mermaid 架构图

**数据来源（确定性）**：tree-sitter 已解析 import/依赖；新增 collector 在
`compiler/code_wiki_compiler.py` 编译期产出 `module_dependency` 边表
（from_module, to_module, weight=import 计数），写入 `wiki_facts.relation_type='module_dep'`
或独立表 `module_edges`（倾向独立表，避免污染 facts 语义）。

**渲染**：项目总览页（`repository-*.md`）生成节固定追加一个 `flowchart TD` 代码块：

- 节点 = 模块（display name），边 = 依赖方向；权重 ≥ 2 的边才画，抑制噪声；
- 上限 30 节点：超出时按模块页数排序保留 Top 30，其余折叠进 "other (N)" 节点；
- 语法由模板保证（标识符经 slug 化，禁止 `[]{}()` 出现在 label）。

**Portal 渲染**：`portal/showcase.py` 已有 `_MERMAID` 解析（`showcase.py:33`），
静态导出侧已支持；动态 Portal 的 markdown 渲染器（`portal_assets.py` CONTROL_JS
`body.innerHTML=data.html` 服务端渲染）新增：`\`\`\`mermaid` 块转成
`<pre class="mermaid">`，由**同源自托管的 vendored mermaid.min.js**（MIT）渲染。
vendor 文件随包分发（`portal_assets.py` 增加 `/vendor/mermaid.js` 路由），
不引入外部 CDN，维持"无外部脚本"的安全边界。

**验收**：fixture 仓库 → golden mermaid 文本快照单测；lint 增加规则：mermaid 块
label 字符集校验。

### A2 CJK bigram 分词

**公共模块**：新建 `memoryforge/core/tokenization.py`：

```python
def index_terms(text: str) -> list[str]:
    """Latin/数字: 原词小写化; CJK 连续段: bigram(长度1保留单字)。"""
```

**写入侧**：`storage/workspace.py` 写 `source_fts.search_terms` 与
`wiki_fact_fts.routing_text` 时过 `index_terms`。
**查询侧**：`query/retrieval_v2.py::_tokenize` 复用同一函数（单一实现，防止两侧漂移）。

**迁移**：`workspace_contract.py` schema 版本 bump；提供
`memoryforge reindex-fts --workspace` 命令重建两张虚拟表（旧表保留一个版本再删，
支持回滚）。索引体积预期增长约 2×（仅 CJK 段），可接受。

**评测**：新建 `demo/evaluation/cjk_retrieval_dev_v001.json`（≥ 50 条中文问题，
含"策略/设计/排查"类长查询），先跑旧分词记录 baseline，再验证提升；
confirmation 拆分沿用既有冻结流程。

### A3 Slug 文件名

**方案**：`wiki/pages/<project-slug>/<module-path>.md`；frontmatter 保留
`page_id: <sha256>`（身份不变，路径只是展示寻址）。
**兼容**：sqlite 新表 `page_redirects(old_path, new_path)`；`/api/page` 与 MCP
读路径命中旧路径时 302/重写。`INDEX.md` 由编译器统一重生成。
**迁移**：一次性 migration 走**正常 ChangeSet 发布链**（自举：项目自己审自己的
路径迁移），不提供旁路脚本。
**影响面清单**：`storage/workspace.py`（寻址）、`compiler/index_rendering.py`、
`portal/portal_catalog.py`、`interface/mcp_server.py`（返回 path）、
`portal_assets.py`（前端路由无需变，hash 路由按 path 透传）。

---

## 5. Phase B 详细设计

### B1 Atlas 摘要层（RAPTOR-lite）

**新页面类型**：`kind='atlas'`（每项目一页）与 `kind='library_overview'`（每
workspace 一页）。

**确定性 rollup 算法**（首版无 LLM）：

1. 每模块页取 `summary` 字段 + 各 H2 节首句（已有 `wiki_facts.quote` 可复用）；
2. atlas 页按模块依赖图（A1 的边表）分节：入口模块 → 核心模块 → 外围；
3. 每节 ≤ 8 条要点，超限按 B3 的 PageRank 排序截断。

**SummaryCitation 扩展**（Citation 哈希互锁的延伸，需同步 SPEC.md）：

```python
class SummaryCitation(BaseModel):
    child_page_id: str          # 指向子页面 page_id
    child_content_sha256: str   # 锁定子页面内容哈希 —— 子页变更即失配
    section_anchor: str         # 子页面内锚点
```

摘要的可重放语义 = "重放时先验子页面哈希，失配则声明摘要过期"。这与现有
`quote_sha256` 互锁同构，不破坏原 Citation（两种类型并存）。

### B2 双级路由（借 LightRAG）

`retrieval_v2.py` 新增 `_atlas_lane`；router 为**确定性规则**（可测试、可复现）：

- 查询含符号 token 且 `wiki_facts.symbol` 命中 → 走 exact lane（现状）；
- 查询含全局疑问词（为什么 / 整体 / 架构 / how does X work overall）**且**无符号命中
  → atlas lane 先行，atlas 的 SummaryCitation 供二跳下钻；
- 其余 → 既有 lexical/relation 泳道。

规则词表与判定函数独立成 `query/route_rules.py`，配 golden 单测。

### B3 PageRank 阅读路径 + 预算化地图（借 Aider / RepoWiki）

**图定义**：节点 = 已应用页面；边权 `direct=1.0`、`exact_mentions=0.5`、
`same_project=0.1`。迭代 20 轮，damping 0.85。结果写 sqlite `page_rank`
（apply 时增量重算受影响连通片）。

**Portal**：项目页新增"从这里读起"Top 5 卡片行。
**MCP map-first**：`memoryforge_context` 增加返回结构 `map` 字段：核心页面
`title + 一句话 summary` 列表，按 PageRank 序，**二分法拟合 4,000 字符预算**
（Aider repo map 的 budget-fit 思路）。AI 拿到地图后再按需读页面，实现
INDEX → **map** → 页面 → 证据的四跳渐进。工具签名向后兼容（新增字段）。

### B4 确定性 rerank + 新鲜度

**打分**（无模型参与）：

```
score = 0.5 * lane_score_normalized
      + 0.2 * relation_proximity      # 距当前项目/页面的边数
      + 0.2 * freshness               # compiler/freshness.py 的 page_freshness 归一
      + 0.1 * symbol_exactness        # 是否含精确符号命中
```

落点：`retrieval_v2.py` 召回后统一 rerank；trace 记录各项分值（延续确定性 trace
的既有设计）。Portal 页面新增徽章："基于 commit X · 落后 N commits"。

---

## 6. Phase C（研究向，不承诺）

- Leiden 社区聚类 + 社区摘要（GraphRAG 的 community summary）；
- 核心记忆块：每项目一页 PIN 的 MOC（预算受限），AI 可**提案**修改（走 ChangeSet）
  —— "自编辑记忆 × 人工审核治理" 的差异化组合。

## 7. 不变量守护（每个候选的硬约束）

1. 本 spec 产生的所有页面（atlas / mermaid / map）一律经 ChangeSet 发布，
   LLM 参与的内容必为 `PROPOSED`；
2. 不修改现有 `Citation` 的字段与校验语义，只新增 `SummaryCitation` 类型；
3. 检索行为变化必须携带确定性 trace（可复现、可评测）；
4. FTS 迁移与 slug 迁移不提供绕过发布链的旁路；
5. vendor mermaid.js 同源自托管，禁外部 CDN。

## 8. 评测计划

| 套件 | 用途 | 指标 | 目标 |
| --- | --- | --- | --- |
| 既有符号路由冻结套件 | 回归保护 | route recall@3 / accuracy | 不回退（100% / 60% 起） |
| `cjk_retrieval_dev_v001`（新） | A2 | route recall@3 / MRR | +15pp |
| `global_questions_dev_v001`（新，≥30 条全局问题） | B1/B2 | route recall@3 / answer accuracy | ≥80% / ≥70% |
| map-first 上下文体积基准（新） | B3 | 首轮字符数 | −50% |

流程：每阶段开发前冻结 baseline 与 confirmation 拆分（SHA256 记录进本 spec），
confirmation 未跑不得声明收益——沿用 `EXACT_SYMBOL_ROUTING_SPEC` 的纪律。

## 9. 交付与验收

- Phase A：golden 测试 + CJK 套件达标 + lint clean + release gate 全绿；
- Phase B：全局问题套件达标 + 符号路由回归不退 + trace 完整性抽查；
- 回滚：A2 保留旧 FTS 表一版；A3/B1 各为独立 changeset 可 revert；
  B3 map 字段可配置关闭（`config.yaml: mcp.map_first: false`）。

## 10. 风险表

| 风险 | 缓解 |
| --- | --- |
| bigram 索引膨胀 | 仅 CJK 段 bigram；迁移前后体积对比进 changeset 描述 |
| mermaid label 注入破坏语法 | slug 化 + 字符集白名单 + golden 快照 |
| atlas 摘要过期失配频繁 | 失配降级为"声明过期"而非报错；freshness 联动重编译 |
| slug 迁移破坏旧深链 | page_redirects 永久保留；MCP 返回新路径带旧路径别名 |
| PageRank 增量重算成本 | 只重算受影响连通片；全量重算仅发生在 migration changeset |

## 11. 影响面文件清单（实现时逐项核对）

```
src/memoryforge/core/tokenization.py        (新, A2)
src/memoryforge/storage/workspace_contract.py (A2 schema)
src/memoryforge/storage/workspace.py         (A2 写入 / A3 寻址 / B3 page_rank)
src/memoryforge/compiler/code_wiki_compiler.py (A1 边表 / B1 rollup)
src/memoryforge/compiler/index_rendering.py  (A3 INDEX 重生成)
src/memoryforge/compiler/freshness.py        (B4 接入排序)
src/memoryforge/query/retrieval_v2.py        (A2 查询侧 / B2 atlas lane / B4 rerank)
src/memoryforge/query/route_rules.py         (新, B2)
src/memoryforge/core/models.py               (B1 SummaryCitation)
src/memoryforge/interface/mcp_server.py      (B3 map 字段)
src/memoryforge/portal/portal_assets.py      (A1 mermaid 渲染 / B3 Top5 / B4 徽章)
src/memoryforge/portal/portal_catalog.py     (A3 API 兼容)
docs/SPEC.md                                 (B1 Citation 语义同步)
demo/evaluation/cjk_retrieval_dev_v001.json  (新, A2 评测)
demo/evaluation/global_questions_dev_v001.json (新, B 评测)
```
