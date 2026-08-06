# Code Wiki 数据契约

本文档定义 MemoryForge 在 CodeWiki C0-C3 阶段使用的事实层契约。Python、Go 和
TypeScript/TSX Tree-sitter Adapter 已接入 `git-sync`、`ingest --code-wiki` 和稳定 Wiki
写入；本文档描述的是这些入口共享的事实层边界，而不是未来接口草案。

## 1. 基本原则

1. 代码结构由本地解析器产生，LLM 不能创建 Symbol 或 Relation。
2. 每个代码事实必须绑定一个不可变 SourceVersion 和非空 `chars:start-end`。
3. Symbol ID 跨 Commit 稳定，Snapshot ID 绑定具体 Commit。
4. 模块规划必须让每个输入 Symbol 恰好归属一个模块。
5. 架构图只聚合已存在的 CodeRelation，不接受无证据边。
6. 后续生成的 Wiki 仍须经过 `review -> approve -> apply`。

## 2. 支持范围

首批语言：

- Python
- Go
- TypeScript

首批 Symbol：

- module/package
- class/interface/struct
- function/method
- type alias/constant

首批 Relation：

- `contains`
- `imports`
- `calls`
- `extends`
- `implements`
- `tests`

## 3. 确定性 ID

所有 ID 都使用 SHA-256，对字段以 `NUL` 分隔后计算摘要：

```text
symbol_id =
  sha256("code-symbol", repository_id, relative_path, language, kind, qualified_name)

relation_id =
  sha256("code-relation", repository_id, relation_type, source_symbol_id, target_symbol_id)

index_id =
  sha256("code-index", repository_id, commit_sha)

module_id =
  sha256("code-module", repository_id, module_path)

module_plan_id =
  sha256("module-plan", code_index_id)
```

Commit 不进入 Symbol ID，因此同一个函数只修改实现时仍保持身份；`signature_sha256` 和
`body_sha256` 用于判断其内容变化。

## 4. 模型边界

### CodeLocation

保存 SourceVersion、内容哈希、仓库相对路径、字符区间和一基行号。字符区间是权威定位，
行号只用于展示。

### CodeSymbol

保存稳定身份、语言、类型、可见性、签名及签名/实现哈希。模型会校验 `symbol_id` 和
`signature_sha256`，拒绝伪造或不一致记录。

### CodeRelation

保存有向关系和至少一个 `CodeLocation` 证据。除递归调用外，关系不能自指。

### CodeIndexSnapshot

表示一个仓库 Commit 的完整代码索引。它校验：

- Symbol/Relation ID 唯一；
- 所有对象属于同一个仓库 Commit；
- Relation 端点均存在；
- SourceVersion 精确覆盖所有代码证据。

### ModulePlan

表示层级模块树。模块路径使用小写 kebab-case。为避免多个 Git 仓库拥有同名模块时互相覆盖，
Wiki 路径包含仓库 ID 前缀：

```text
wiki/pages/code/<repository-id-prefix>/<module-path>.md
```

计划必须完整且无重叠地分配所有声明 Symbol。

### ArchitectureGraph

表示可交给确定性 Mermaid 编译器的模块图。每条边必须携带一个或多个
`CodeRelation.relation_id`，图模型会拒绝未知模块端点。

## 5. 代码 Wiki 入口

C1 Tree-sitter 输出 `CodeIndexSnapshot`；C2 模块规划器消费 Snapshot 并输出
`ModulePlan`；C3 Wiki 编译器消费二者生成普通 `PROPOSED` ChangeSet。当前 CLI 入口是：

```text
git-add -> code-add -> git-sync -> ingest --code-wiki
  -> review -> approve -> apply -> lint
```

代码 Wiki 仍然只处理已注册且已同步的代码模块，不会扫描未显式选择的目录，也不会绕过审批
直接写入稳定 Wiki。

在 C3 完成前，这些模型不得写入活动 Wiki，也不得成为查询结果的事实来源。

## 6. C1 Python / Go / TypeScript Adapter

`build_code_index(...)` 是唯一公开入口。它只读取最近一次 `git-sync` 写入的不可变
SourceVersion，不读取 checkout 当前文件，并把所有已实现语言合并为一个 Snapshot。

Python 首版提取：

- 每个非空 Python 文件的 module；
- 顶层 class/function 和 class method；
- module/class 到成员的 `contains`；
- 可解析的本文件函数调用和 `self`/`cls` 方法调用。

Go 首版提取：

- 每个非空 Go 文件的 package；
- struct、interface、type、function 和 receiver method；
- package/type 到成员的 `contains`；
- 同包跨文件函数调用和 receiver 方法调用。

TypeScript 首版提取：

- `.ts`/`.tsx` 文件 module；
- class、interface、type alias、const、普通函数、箭头函数和 class method；
- module/class 到成员的 `contains`；
- 相对路径 `imports`、同文件调用、具名导入调用和 `this.method()`。

语法错误会阻断索引；无法静态解析的动态调用不会伪造关系。Symbol ID 不含 Commit，因此
实现修改后身份保持稳定，而 Snapshot ID、`body_sha256` 和 SourceVersion 会随证据更新。

## 7. C2 确定性模块规划

`build_module_plan(snapshot)` 不调用模型，按代码事实生成层级模块：

- Python 文件按模块路径分组，`__init__.py` 折叠到目录；
- Go 文件按 package 目录分组；
- TypeScript 文件按模块路径分组，`index.ts(x)` 折叠到目录；
- 同级模块根据 `imports`/`calls` 等依赖进行 dependency-first 排序；
- 非 kebab-case 路径会规范化，冲突时使用原始路径哈希消歧；
- 每个 Symbol 必须且只能归属一个模块。

`build_architecture_graph(snapshot, plan)` 将跨模块 CodeRelation 聚合为 ArchitectureEdge。
同一模块对、同一关系类型的多条代码关系合并到一个边，但完整保留所有 `relation_id`，
因此后续 Mermaid 图上的每条边都能回溯到代码证据。

## 8. C3 代码 Wiki ChangeSet

`compile_code_wiki(workspace, snapshot, plan)` 会先重建并比对确定性 ModulePlan 和
ArchitectureGraph，再回读不可变 Blob 校验所有 Symbol/Relation 定位。输出仍是普通
`PROPOSED` ChangeSet，不直接修改稳定 Wiki。

- 直接拥有 Symbol 的模块页声明唯一 Source ownership，并为每个 Symbol 生成可回读脚注；
- 纯层级父模块页标记为 `code_module_overview`，只提供本地生成的导航，不重复占有 Source；
- 页面保存模块路径、语言、签名、子模块和跨模块依赖；
- `wiki/INDEX.md` 同步纳入嵌套代码页；
- 当前计划中消失的旧代码页生成 `ARCHIVE_PAGE`；
- 与稳定 Wiki 完全一致时返回 `None`，不制造空 ChangeSet。

完整发布路径保持：

```text
CodeIndexSnapshot -> ModulePlan -> PROPOSED
  -> review -> approve -> apply -> lint
```

## 9. C0 固定基线

`demo/evaluation/code_wiki_eval.json` 固定 Symbol、Relation、Module、Citation 和增量预期。
关系分为必须通过的 `core` 与禁止删除的 `known_gap`；复现脚本不调用模型，并要求连续两次
输出字节一致。

## 10. 外部实现参考

本实现只借鉴算法结构，没有复制以下仓库代码：

| 项目 | 固定 Commit | 参考点 | 许可证边界 |
| --- | --- | --- | --- |
| CodeWiki | `3c4b4c244a94848d7ceacbc2a8efd31184b0ed16` | canonical import binding、仓库级唯一解析、浅层 receiver type inference | README/pyproject 声明 MIT，但该 Commit 缺少 LICENSE 文件 |
| RepoDoc | `306becd0f143211c0dde2bdbd480578356280e28` | 语言分析后统一解析关系、按依赖传播增量影响 | 仓库未提供明确许可证 |

MemoryForge 使用自己的 Tree-sitter 数据契约、稳定 Symbol ID、不可变 SourceVersion 和
Citation 证据重新实现。没有采用 RepoDoc 的尾名模糊兜底，也没有引入其 NetworkX、LLM 或
增量框架。
