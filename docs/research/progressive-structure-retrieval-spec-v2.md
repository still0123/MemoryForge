# Progressive Structure & Retrieval Specification (v2)

## Status

`DRAFT_FOR_REVIEW`

- 版本：v2。取代 v1（归档于 `docs/research/progressive-structure-retrieval-spec-v1.md`，
  保留作对比证据）。
- 评审轮次：1（外部评审意见已吸收，关键变更见 §12）。
- 基线锚点 commit：`11e2285e03736e2490d31e31a39b37e8313f4b5`。
- 参考基线（沿用 `EXACT_SYMBOL_ROUTING_SPEC.md` 冻结结果）：
  - Answer accuracy: 60.0%
  - page route recall@3: 100.0%
  - Source recall@3: 100.0%
  - Citation grounding: 88.9%
  - **abstention accuracy: 0.0%（本版升为硬门禁，见 §10）**
- 各冻结输入（套件 SHA256、confirmation 拆分）在进入开发前按既有流程补齐。

## 0. 实施前置（W0）

1. 从 `11e2285` 新建**干净 worktree** 开发，不叠加任何未提交改动。
2. 记录全量基线：`ruff` / `mypy` / `pytest` 全绿快照 + 既有四套冻结指标。
   任何候选合入前提是基线本身无红灯。

## 1. 问题陈述（v2 修正版）

### P1 中文检索失败模式（已实证，修正 v1 的错误前提）

v1 错误声称 unicode61 "按单字切分"。实测（SQLite 内存库，`tokenize='unicode61'`）：

```text
INSERT: '我们讨论了缓存策略的调整方案'
MATCH '缓存':    0 hits
MATCH '缓存策略': 1 hits
MATCH '策略':    0 hits
MATCH '方案':    0 hits
```

**真实行为：CJK 连续段被索引为一个完整 token。** 因此子串查询、跨词边界查询、
部分短语查询全部失败；只有查询 run 与索引 run 完全一致才命中。文档写
"缓存策略的调整"而用户查"调整 缓存策略"即 miss。这比 v1 描述的失败模式更严重，
bigram 方案的必要性反而更强。

### P2 全局性问题没有落点

"为什么这样设计 / 整体架构" 类问题只能靠 `same_project` 边拉回平级页面，
没有"先总览、再下钻"的第一跳。

### P3 渐进式缺少"地图"跳

当前三跳为 `INDEX → 页面 → 证据`；AI 第一跳就可能读到过重的页面正文。
目标形态为四跳：`INDEX → map（预算化地图） → 页面 → 证据`。

## 2. 目标与非目标

### 目标

- G1 **abstention 硬门禁**：所有候选在所有冻结套件上 abstention 不回退
  （baseline 0%）；改进目标在 CJK/全局套件冻结后设定。
- G2 中文检索：`cjk_retrieval_dev_v001` 上 route recall@3 相对 baseline +15pp。
- G3 全局问题：`global_questions_dev_v001` 上 route recall@3 ≥ 80%。
- G4 map-first：`memoryforge_context` 首轮返回字符数 −50%（4,000 字符预算）。

### 非目标（v1 基础上扩充）

- 不引入向量数据库 / embeddings。
- **不做持久 Atlas 页面、不做新 Citation 类型**（v1 的 SummaryCitation 撤销：
  只锁子页哈希无法证明摘要陈述正确；摘要文本必须直接携带原始 Source Citation，
  而首版根本不落盘摘要——见 W2）。
- **不做 slug 文件名迁移**（展示收益不抵 redirect 永久债务与 Citation/projection
  影响面；深链可读性由 INDEX 链接文本承担）。
- **不做增量 PageRank**（全局不动点算法局部重算会产生漂移性错误排名；
  仅在 §8 条件触发时做全量重算）。
- 不改变"AI 止步于 PROPOSED"与"Citation 哈希互锁"不变量。
- Phase C（社区摘要、核心记忆块）整体搁置。

## 3. 变更通道边界（v2 新增核心节）

项目存在两类变更，事务边界不同，**不得混用**：

| 通道 | 管辖对象 | 机制 | 回滚 |
| --- | --- | --- | --- |
| 内容通道 | wiki pages、INDEX.md | ChangeSet 发布链（Git commit） | revert commit |
| 派生通道 | schema、FTS 索引、rank 表 | 事务性 migration 命令 | 重算/重建 |

规则：

1. 派生状态必须能从「内容 + manifests」完全重建（FTS 从 pages/facts 重建，
   rank 从关系边重算）；因此派生通道**不进发布链**，v1 §7 "FTS 迁移不绕过发布链"
  的表述作废。
2. migration 命令要求：单事务包裹、幂等（可重跑）、`--dry-run` 预览、
   输出前后体积对比、失败自动回滚。
3. 任何候选不得提供绕过 ChangeSet 直接改写 pages 的路径（不变量不变）。

## 4. W1：共享分词 + 事务性 FTS 重建

1. 新建 `memoryforge/core/tokenization.py`：

```python
def index_terms(text: str) -> list[str]:
    """Latin/数字原词小写化；CJK 连续段生成 bigram（单字段保留单字）。"""
```

2. 写入侧（`storage/workspace.py` 写 `source_fts.search_terms` 与
   `wiki_fact_fts.routing_text`）与查询侧（`query/retrieval_v2.py::_tokenize`）
   **复用同一实现**，禁止两侧漂移。
3. `storage/workspace_contract.py` schema 版本 bump；新增
   `memoryforge reindex-fts --workspace [--dry-run]`：单事务重建两张虚拟表，
   旧表保留一个版本后删除（支持一次回退）。

**验收**：golden 分词单测（含纯 CJK / 混合 / 空串）；CJK 套件 +15pp；
索引体积增幅 ≤ 2.5×；查询延迟不回退。

## 5. W2：即时预算化 map（合并 v1 的 B1+B3，砍掉持久层）

**不新增任何页面、表或 Citation 类型。** map 在查询时由既有数据即时组装：

- 数据源：`INDEX.md` 结构 + `wiki_facts`（quote/symbol）+ 页面关系边
  （direct / exact_mentions / same_project）。
- 组装算法（全确定性）：
  1. 种子 = 查询命中的 top 页面（既有泳道结果）；
  2. 扩展 = 种子沿关系边一跳；
  3. 排序 = 关系类型优先级（direct > exact_mentions > same_project），
     同级按段落数稳定排序；
  4. **贪心装填**：按排序逐项累加 `title + summary 首句` 至 4,000 字符预算即止
     （排序后贪心前缀装填，无需求解优化）。
- 落点：新建 `query/context_map.py`；`memoryforge_context` 返回结构新增 `map`
  字段（`config.yaml: mcp.map_first` 可关闭）；工具签名向后兼容。
- Portal 不消费 map（它是 AI 视图，不是人视图）。

**验收**：首轮字符 −50%；符号路由四指标不回退；trace 记录 map 组成的页面清单
（可复现）。

## 6. W3：route_rules 全局优先路由

1. 新建 `query/route_rules.py`：全局疑问词表（为什么 / 整体 / 架构 /
   how does X work overall …）+ 确定性判定函数；词表数据与逻辑分离。
2. 规则：查询含全局词 **且** 无符号命中 → map-first（W2 地图先行，AI 按需二跳）；
   有符号命中 → 既有 exact lane 优先（不回退符号路由）。
3. golden 单测覆盖：纯全局词 / 全局词+符号 / 纯符号 / 普通词 四类。

**验收**：`global_questions_dev_v001`（≥30 条）route recall@3 ≥ 80%；
abstention 与 citation grounding 硬门禁不回退。

## 7. W4：Mermaid 架构图（独立 UI 增强轨道）

- 设计同 v1 A1：tree-sitter 模块依赖边表（编译期产出，独立表 `module_edges`）→
  项目总览页 `flowchart TD` 块（30 节点上限、label slug 化白名单）；
  Portal 端 `\`\`\`mermaid` → `<pre class="mermaid">`，**同源 vendor**
  mermaid.min.js（MIT），禁外部 CDN。
- **v2 定位变化**：纯展示增强，不触碰检索与路由；独立 changeset、独立回滚，
  可与 W1–W3 并行或延后。

## 8. 检索质量兜底：全量 PageRank（条件触发）

仅当 W2+W3 评测未达 G3 时启用：

- 图：页面节点，边权 direct=1.0 / exact_mentions=0.5 / same_project=0.1；
- **全量确定性重算**（apply 时触发；页面量级为几十至几百，毫秒级成本）；
- 融合方式：仅作为 map/Top-N 的**排序 tie-breaker**，不做跨泳道分数混合。
- 结果写 sqlite `page_rank`（派生通道，可随时重算）。

## 9. freshness 语义（v1 B4 精简）

freshness **不进入线性加权**（v1 的 0.2 权重撤销——过期精确证据不应输给新鲜
弱相关证据）：

1. 同分 tie-breaker：其余排序键完全相等时新者在前；
2. 过期标注：Portal 页面徽章 "基于 commit X · 落后 N commits"，
   trace 记录 freshness 状态（`compiler/freshness.py` 数据已有，只接展示与 trace）。

## 10. 评测与门禁

| 套件 | 指标 | 类型 |
| --- | --- | --- |
| 既有符号路由冻结套件 | route recall@3 / accuracy / source recall / grounding | **硬门禁：四项全不回退** |
| 既有符号路由冻结套件 | abstention accuracy | **硬门禁：不回退（0%）** |
| `cjk_retrieval_dev_v001`（新，≥50 条） | route recall@3 / MRR | 目标：+15pp |
| `global_questions_dev_v001`（新，≥30 条） | route recall@3 | 目标：≥80% |
| map-first 上下文基准（新） | 首轮字符数 | 目标：−50% |
| 全部 | 查询延迟 p95 / 索引体积 | 预算：不回退 / ≤2.5× |
| 工程 | ruff / mypy / pytest | **硬门禁：全绿** |

冻结流程沿用 `EXACT_SYMBOL_ROUTING_SPEC`：baseline 与 confirmation 拆分 SHA256
记入本 spec；confirmation 未跑不得声明收益。

## 11. 交付顺序与排期

```text
W0 基线清理（0.5 周）
 └─ W1 分词 + FTS 重建（1 周）        —— 独立合入
     └─ W2 即时 map + W3 路由规则（1.5 周，同一冻结批次评测）
         └─ 条件触发：§8 全量 PageRank
W4 Mermaid（0.5–1 周）                —— 独立轨道，随时可并行
```

## 12. v1 → v2 变更归因（评审吸收记录）

| v1 条目 | 处置 | 原因 |
| --- | --- | --- |
| A2 unicode61 "单字切分" 前提 | **改写** | 实证为整段单 token，失败模式更严重 |
| A3 slug 文件名 | **删除** | redirect 永久债务 + Citation/projection 影响面 > 展示收益 |
| B1 持久 Atlas 页面 | **撤销** | 即时 map（W2）无表无页面即可达成 G3/G4 |
| B1 SummaryCitation | **撤销** | 子页哈希不能证明摘要陈述正确；不落盘摘要则无需新类型 |
| B3 增量 PageRank | **撤销** | 全局算法局部重算产生漂移排名；改为条件触发的全量重算 |
| B3 token 预算"二分" | **改为贪心前缀装填** | 排序确定后贪心等价且更简单 |
| B4 freshness 0.2 权重 | **改为 tie-breaker + 标注** | 防止过期精确证据输给新鲜弱证据 |
| —（新增） | abstention 硬门禁 | baseline 0% 是最紧急缺口，v1 竟未设门禁 |
| —（新增） | §3 变更通道边界 | FTS/schema 是派生态，不进内容发布链 |
| Phase C | 搁置 | 研究向，评审确认延后 |

## 13. 影响面清单（精简后）

```
src/memoryforge/core/tokenization.py          (新, W1)
src/memoryforge/storage/workspace_contract.py  (W1 schema bump)
src/memoryforge/storage/workspace.py           (W1 写入侧)
src/memoryforge/query/retrieval_v2.py          (W1 查询侧)
src/memoryforge/query/context_map.py           (新, W2)
src/memoryforge/query/route_rules.py           (新, W3)
src/memoryforge/interface/mcp_server.py        (W2 map 字段)
src/memoryforge/compiler/code_wiki_compiler.py (W4 边表)
src/memoryforge/portal/portal_assets.py        (W4 mermaid 渲染 / §9 徽章)
src/memoryforge/compiler/freshness.py          (§9 仅接展示与 trace)
demo/evaluation/cjk_retrieval_dev_v001.json    (新, 评测)
demo/evaluation/global_questions_dev_v001.json (新, 评测)
```
