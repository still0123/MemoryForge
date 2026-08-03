# MemoryForge 飞书私聊 MVP Spec

> 目标：让你在飞书私聊机器人时，得到来自**已应用本地 Wiki**的答案和来源页面。它是
> MemoryForge 的展示入口，不是另一个通用 RAG 项目。

## 1. 这次做什么

```text
飞书私聊文本
  -> lark-cli 事件监听
  -> 本地已应用 Wiki 查询
  -> 回复原消息 + Wiki 页面引用
```

项目已经提供 `memoryforge feishu-serve`。本阶段重点是完成自建应用配置和真实验收，
不是再写 Webhook、Flask、向量库或新的 Agent 框架。

## 1.1 展示路径

`feishu-serve` 就是项目对外展示的飞书入口：它直接接收消息、调用 MemoryForge 的 Wiki 查询并回复。
`lark-cli` 只负责飞书的登录、事件和发消息协议；检索、证据与回答逻辑仍由本项目拥有。这样展示的是
MemoryForge，而不是某个通用 Agent 平台的外壳。

公司仓库、飞书正文和 `local_only` Wiki 不进入 GitHub 仓库，也不能被自动提交、上传或变成公开
Demo 内容。模型可按用户明确授权使用（包括外部模型）；`local_only` 资料仍默认不出现在模型上下文，
需要时通过显式授权放行。

## 2. 为什么不直接复制“飞书 RAG 机器人”脚手架

内部现成脚手架使用 `Flask + LangChain + FAISS + 定时同步`。它可以参考飞书接入流程，
但会重复实现 MemoryForge 已有的资料导入、增量更新、检索和引用能力。

本项目继续使用：

- `lark-cli event consume im.message.receive_v1 --as bot` 收消息；
- 现有渐进式 Wiki 问答生成答案和页面引用；
- `lark-cli im +messages-reply --as bot` 回复原消息；
- 飞书资料仍通过显式 `feishu-import -> ingest -> review -> apply` 进入本地 Wiki。

## 3. MVP 边界

第一版只支持：

- 你和机器人之间的私聊文本；
- 已经 `apply` 的本地 Wiki；
- 默认确定性、带页面标题引用的回答；显式启用模型后可把本次命中的证据整理成自然答案；
- 本机运行，不要求公网域名、Docker 或 Web 服务。

第一版明确不做：

- 群聊、@ 提及解析、多用户资料隔离；
- 自动抓取所有飞书资料、后台定时同步；
- Flask / FastAPI / 向量数据库 / LangChain；
- 默认向模型发送 `local_only` 的内部资料；只有 `--llm --allow-local-llm` 明确授权时才允许；
- 自动写飞书文档或修改代码仓库。

## 4. 外部配置（需要你在飞书开发者后台完成）

1. 创建自建应用，开启机器人能力，并把测试范围限制为你自己。
2. 在事件订阅中添加 `im.message.receive_v1`。
3. 在权限管理中添加：
   - `im:message.p2p_msg:readonly`
   - `im:message:send_as_bot`
4. 发布测试版本，并在飞书中与机器人建立私聊。
5. 在运行 MemoryForge 的机器上执行：

   ```bash
   lark-cli config init --new
   ```

   App ID 和 App Secret 仅保存到 `lark-cli` 的本机配置；不写入项目文件，也不提交 Git。

## 5. 本地运行与验收

先确保目标 Workspace 至少有一份已经应用的来源：

```bash
memoryforge ask '一个已知问题' --workspace ./my-wiki
```

再启动机器人：

```bash
memoryforge feishu-serve --workspace ./my-wiki
```

验收问题：在飞书私聊机器人发送一个可由该 Wiki 回答的问题。

通过标准：

- 终端显示监听已启动；
- 机器人只回复一次；
- 回复内容能回答问题，并含 `来源 Wiki：wiki/pages/...`；
- 给机器人发图片或机器人自身消息时，不产生循环回复。

## 6. 真实试用后再决定的两项增强

| 反馈 | 后续动作 |
|---|---|
| 直接引用原文不够自然 | 增加显式 `--llm` 模式，复用现有 Provider；内部资料仍要求 `--allow-local-llm`。 |
| 确实需要群聊协作 | 增加 @ 机器人过滤与群聊事件权限，不与私聊 MVP 混在同一轮。 |

这两项都不是本阶段验收条件。
