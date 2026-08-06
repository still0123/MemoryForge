# C4 可信架构图 SPEC

## 1. 状态与目标

- 状态：已完成（2026-08-06）
- 基线分支：`feat/c0-relation-resolution`
- 实施分支：`feat/c4-trusted-architecture`
- 目标：把现有 `ArchitectureGraph` 编译为确定性、可引用、可审核的 Mermaid。

C4 不增加新的代码分析能力。它只展示 C1-C3 已验证的 Module、CodeRelation 和
SourceVersion，禁止模型补边或直接生成 Mermaid。

## 2. 问题

当前代码 Wiki 页面以文字列出跨模块 Dependencies，但存在三个缺口：

1. 页面没有可视化模块关系；
2. 列出的依赖没有直接指向 Relation evidence；
3. Benchmark 只验证 Graph 内部关系存在，没有验证 Wiki 是否完整呈现 Graph。

## 3. Ponytail 决策

按最小可行路径实施：

- 复用 `ArchitectureGraph`，不增加图数据库；
- 复用 Markdown Mermaid fenced block，不增加渲染依赖；
- 复用页面现有 Source ownership，不允许一个 Source 被多个页面占有；
- 复用 Relation 的首个确定性 evidence 作为页面脚注；
- 复用现有 `review -> approve -> apply -> lint`；
- 只增加一个端到端 Benchmark gate，不建立第二套测试框架。

## 4. 数据流

```text
CodeIndexSnapshot
  -> ModulePlan
  -> ArchitectureGraph
  -> source module page
       -> deterministic Mermaid
       -> architecture-edge marker
       -> Verified dependencies
       -> relation evidence footnote
  -> PROPOSED ChangeSet
  -> review -> approve -> apply -> lint
```

## 5. 页面契约

### 5.1 源码模块页

有跨模块出边时必须生成：

````markdown
## Architecture

```mermaid
flowchart LR
  m_<module-id>["Source"]
  %% architecture-edge:<edge-id>
  m_<module-id> -->|calls (1)| m_<target-id>
```

## Verified dependencies

- `calls` -> Target (1 verified relation) [^relation-<relation-id>]
````

脚注格式继续使用现有可回读 Citation：

```text
[^relation-<relation-id>]: source `<source-id>` · revision `<version>` · `chars:<start>-<end>`
```

### 5.2 纯导航模块页

无直接 Symbol ownership 的父模块只生成 Child hierarchy Mermaid，不声明 Source，
不生成 Relation Citation，不把目录层级伪装为代码调用。

### 5.3 确定性

- Node 按 module path 排序；
- Edge 按 relation type、target module ID 排序；
- Mermaid node ID 直接来自完整 module ID；
- 每条 Edge 写入完整 `architecture-edge:<edge-id>` marker；
- 每个 Relation 使用完整 `relation-<relation-id>` footnote label；
- 相同 Snapshot 连续编译必须字节一致。

## 6. Evidence 与 ownership

对源模块页中的每条 ArchitectureEdge：

1. `relation_id` 必须存在于当前 `CodeIndexSnapshot`；
2. Relation 的 source symbol 必须归属当前模块；
3. 选用的 evidence.source_id 必须属于当前页面 Frontmatter `sources`；
4. evidence 必须通过现有不可变 Blob 与 locator 校验；
5. 任一条件不满足，编译失败，不降级为无引用边。

Relation 可能包含多个 call-site evidence。C4 页面展示首个规范排序 evidence；完整 evidence
仍保留在 `CodeIndexSnapshot` 并由编译前校验覆盖。

## 7. 功能验收

固定 C0 题集不得修改。新增指标：

| 指标 | 门禁 |
| --- | ---: |
| `mermaid_edge_coverage` | 100% |
| `architecture_citation_coverage` | 100% |
| `architecture_mermaid_determinism` | 100% |

既有指标必须保持：

- Source、Symbol、Core Relation、原 Known-gap Relation、总体 Relation：100%；
- Module assignment、Citation grounding、Architecture edge grounding：100%；
- deterministic replay：100%；
- 单文件增量 `changed_page_ratio <= 25%`。

## 8. 失败关闭

以下情况必须拒绝生成 ChangeSet：

- ArchitectureGraph 不是当前 Snapshot/Plan 的确定性结果；
- Edge 引用未知 Relation；
- Relation evidence 不属于源模块页面；
- Mermaid node ID 冲突；
- Relation 没有 evidence；
- 页面生成后缺少任一 Edge marker 或 Relation footnote。

## 9. 非目标

- 不生成全仓独立 Architecture 页面；
- 不引入 Mermaid CLI、Node、浏览器截图或 SVG；
- 不加入交互式图、缩放、搜索；
- 不实现 RepoDoc 影响传播；
- 不修改 Source ownership 数据模型；
- 不调用 LLM；
- 不合并 `main`、不部署。

## 10. 质量门禁

```text
fixed benchmark twice with byte-identical output
ruff
mypy
pytest
uv pip check
wheel build
git diff --check
```

C4 已随 PR #2 合入 `main`，并包含在 `v0.2.0` Release。
