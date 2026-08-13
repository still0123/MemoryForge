# MemoryForge

<!-- memoryforge-release-claim: version=0.4.0; status=released; supported_platforms=macos; macos_passed=656; linux_status=not_rerun; windows_status=not_verified; release_verification=passed -->

> 把代码、文档和 AI 对话编译成可审核、可追溯、可查询的本地技术 Wiki。

MemoryForge 是一个本地优先的知识编译器。它不把所有资料直接塞给模型，而是先生成可读、
可版本化的 Markdown Wiki；每次更新都经过 `review → approve → apply`，查询结果可以回到固定
SourceVersion 和原文位置。

**正式版本：[`v0.4.0`](https://github.com/still0123/MemoryForge/releases/tag/v0.4.0)。**
该版本在 macOS 完成 `656 passed` 发布门禁；Linux 未在本版本重跑，Windows 尚未验证。
当前 `main` 另含未发布的本地 Portal 控制面和冻结期加固，最近一次完整本地门禁为
`731 passed`。

[5 分钟跑通](#5-分钟跑通) · [60 秒演示](#60-秒公开演示) ·
[公开证据](#公开证据) · [演示讲稿](docs/PORTFOLIO_DEMO.md) ·
[设计文档](SPEC.md)

![MemoryForge 本地知识门户首页](assets/memoryforge-portal-overview.png)

## 一眼看懂

| 入口 | 看什么 |
| --- | --- |
| **核心产品** | Source → ChangeSet → 人工审核 → Markdown Wiki → Query / Agent / Portal |
| **公开证据** | 零 Key 端到端 Demo、固定题集、Citation、拒答、负结果和 Release SHA256 |
| **历史研究** | 被拒绝或被替代的 Candidate、实验边界和完整 Benchmark Registry |

项目适合个人开发者、技术负责人和长期维护多个仓库的人。它不是公网 SaaS、企业权限平台，
也不是拥有 Shell 和任意写权限的通用编码 Agent。

## 核心产品

MemoryForge 解决四个问题：

- **资料分散**：接入本地 Git、文件夹、Markdown/TXT/HTML、飞书文档、公开网页和 GitHub
  Issue/PR。
- **知识会过期**：来源变化只生成待审核 ChangeSet，不直接覆盖正式 Wiki。
- **回答不可核验**：Citation 固定到 SourceVersion、原文 locator、Commit 和 SHA256。
- **AI 会话会丢失**：对话先保存为 `local_only` 草稿，审核后才进入长期记忆。

运行后得到的是普通文件和本地索引：

```text
my-wiki/
├── raw/                 # 不可变来源快照
├── wiki/
│   ├── INDEX.md         # Wiki 总目录
│   └── pages/           # 可读 Markdown 页面
├── .memoryforge/        # SQLite 索引、任务和编译路由
└── .git/                # Wiki 变更历史
```

### 和普通 RAG 的区别

| 普通 RAG | MemoryForge |
| --- | --- |
| 原文切片后直接召回 | 先编译为可读、可审核、可版本化的 Wiki |
| 更新直接替换索引 | 先生成 ChangeSet，再 `review → approve → apply` |
| 主题相关就尝试回答 | support score 不足时返回 `unknown` |
| 很难重放一次结论 | Citation 固定到来源版本、locator、Commit 和 SHA256 |

## 5 分钟跑通

环境要求：Python 3.11+。当前正式支持范围以 v0.4.0 的 macOS 发布证据为准。

只安装 CLI：

```bash
python3.11 -m pip install memoryforge-wiki
```

从源码运行 Portal 和公开 Demo：

```bash
git clone https://github.com/still0123/MemoryForge.git
cd MemoryForge

python3.11 -m venv .venv
.venv/bin/python -m pip install .
MF=.venv/bin/memoryforge

$MF init ./my-wiki
$MF start --workspace ./my-wiki
```

浏览器中按“添加来源 → 后台任务 → 知识更新 → 批准并应用”完成首份资料入库，再从首页搜索或
提问。Portal 只绑定 `127.0.0.1`；自动更新只生成待审核内容，永不自动批准。

CLI 等价流程：

```bash
$MF import ./docs --workspace ./my-wiki
$MF ingest --pending --workspace ./my-wiki
$MF review <changeset-id> --workspace ./my-wiki
$MF approve <changeset-id> --workspace ./my-wiki
$MF apply <changeset-id> --workspace ./my-wiki
$MF ask '这个项目为什么这样设计？' --workspace ./my-wiki
```

## 60 秒公开演示

不需要模型 Key、数据库服务或外部仓库：

```bash
.venv/bin/python demo/run_showcase_demo.py \
  --workdir /tmp/memoryforge-showcase-demo \
  --output /tmp/memoryforge-showcase
.venv/bin/python -m webbrowser \
  file:///tmp/memoryforge-showcase/index.html
```

Demo 使用仓库内固定的 Python、Go、TypeScript 夹具，真实执行：

```text
import → Code Index → ChangeSet → review → approve → apply → query → showcase
```

页面展示来源版本、Wiki 树、Diff、Citation Trace、正确拒答、已知失败和 Mermaid 架构图；
生成文件不包含远程脚本、模型调用、绝对 Workspace 路径或凭据。

完整的 3 分钟讲解顺序和常见追问见
[秋招演示与面试说明](docs/PORTFOLIO_DEMO.md)。

## 工作原理

```mermaid
flowchart LR
    A["代码 / 文档 / 飞书 / AI 对话"] --> B["不可变来源快照"]
    B --> C["WikiCompiler 生成 ChangeSet"]
    C --> D["review：查看页面与 Diff"]
    D --> E["approve：独立授权"]
    E --> F["apply：写入 Wiki + Git Commit"]
    D -->|"不接受"| G["reject：保留记录，不改 Wiki"]
```

查询遵循 `INDEX → 少量 Wiki 页面 → 必要时原文`。MiniClaude Agent 只有
`search_wiki`、`read_evidence`、`final` 三个工具；没有 Shell、通用写文件、Subagent 或 MCP
权限。

代码 Wiki 使用 Tree-sitter 为显式选择的 Python、Go、TypeScript/TSX 文件构建确定性 Symbol、
Relation、ModulePlan 和 Mermaid 图。可选模型只补充带 Citation 的父模块叙事，不生成架构边。

## 公开证据

以下数字只适用于各自冻结的数据集，不能外推为任意仓库或生产 SLA。

| Evidence | 结果 | 边界 |
| --- | ---: | --- |
| 当前 `main` 本地门禁 | macOS **731 passed** | CPython 3.11；Linux/Windows 未重跑 |
| v0.4.0 发布门禁 | macOS **656 passed** | Linux 未重跑，Windows 未验证 |
| AgentSkill-Eval 文档 Wiki | Answer **96.7%**；Citation **100%**；拒答 **100%** | 单一公开仓库，30 题 |
| 三语言 Code Wiki | Symbol / Relation / Mermaid / Citation **100%** | 固定公开夹具 |
| Code Module Narrative | 事实覆盖 **90%**；Citation **100%** | 未发布开发集，20 题 |
| Click 外部迁移 | development / holdout Answer **10% / 0%** | 真实负结果，未隐藏 |

公开复现方法见 [Benchmark](docs/BENCHMARK.md)，可审计主张见
[Evidence Claims](docs/EVIDENCE_CLAIMS.md)，正式制品、provenance 和 `SHA256SUMS` 见
[v0.4.0 Release](https://github.com/still0123/MemoryForge/releases/tag/v0.4.0)。

`demo/` 只负责可运行脚本、固定夹具和机器可读 Evidence。大量 rejected/superseded Candidate
属于研究历史，阅读入口统一放在
[Research Archive](docs/research/README.md)。

## 隐私边界

- 公开仓库只提交代码、公开或虚构 Demo、脱敏评测结果。
- 公司代码、真实飞书正文、Token、App Secret 和运行日志只留在本机。
- Git、飞书、代码和会话来源默认 `local_only`，不会发送给模型。
- 只有显式传入 `--allow-local-llm`，当前命中的本地证据才会交给已配置模型。
- Showcase 默认隐藏 `local_only` 来源、页面、ChangeSet 和 Citation 细节。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [SPEC.md](SPEC.md) | 架构、数据模型和安全边界 |
| [本地 Portal](docs/LOCAL_DYNAMIC_PORTAL_SPEC.md) | 阅读、任务、审核和控制面 |
| [公开 Benchmark](docs/BENCHMARK.md) | 方法、结果、负案例和复现命令 |
| [Evidence Claims](docs/EVIDENCE_CLAIMS.md) | 主张、证据和不能外推的边界 |
| [秋招演示](docs/PORTFOLIO_DEMO.md) | 3 分钟讲解、命令和常见追问 |
| [简历表述](docs/RESUME.md) | 可直接使用的项目描述和指标解释 |
| [研究归档](docs/research/README.md) | Candidate、负结果和历史 Evidence |
| [CHANGELOG](CHANGELOG.md) | 版本范围和验证状态 |

飞书接入见 [FEISHU_MVP_SPEC.md](FEISHU_MVP_SPEC.md)，完整 CLI 运行
`memoryforge --help`，本地发布门禁见
[LOCAL_FREE_TOOLING.md](docs/LOCAL_FREE_TOOLING.md)。

## 当前边界

功能范围已冻结，后续只修 Bug、错误回答和文档。项目刻意不做公网部署、群聊、多用户权限、
向量数据库、知识图谱、通用编码 Agent 或多 Agent 编排。

<!-- memoryforge-release-claim: version=0.3.0; status=released; supported_platforms=macos+linux; active_candidate=19; platform_gate_candidate=19; platform_gate_status=accepted; review_status=accepted; macos_passed=642; linux_passed=639; linux_skipped=3; windows_status=not_verified; release_verification=passed -->

v0.3.0 的 Candidate 与跨平台门禁是历史研究，不代表当前支持范围；其完整记录已下沉到研究文档。
