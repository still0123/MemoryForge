# MemoryForge Agent

This repository contains MemoryForge and its own Feishu demonstration agent.

## 编码原则：保持简单，避免过度防御

实现功能时，以满足用户明确需求的最小、直接方案为准。

- 不要主动添加需求未提及的防御性代码、兜底分支、兼容层、重试机制、回退逻辑、输入清洗、权限校验或安全封装。
- 不要为纯理论、极低概率或当前上下文不可能出现的边界情况编写代码。
- 不要重复校验已经由类型系统、框架、调用方或现有约束保证的数据。
- 不要为了“以后可能会用”而提前增加抽象、配置项、扩展点、辅助函数或额外依赖。
- 测试只覆盖本次需求的核心行为和真实可发生的关键失败路径；不要添加与改动无关的穷举式边界测试、安全测试或重复测试。
- 优先复用现有约定和代码路径，保持改动范围小、逻辑线性、易读。
- 只有当某项检查对当前功能的正确性必不可少、需求明确要求，或缺失后会造成现实且明显的严重风险时，才添加它；必要时用一句话说明原因。
- 如果“更健壮”与“更简单”发生冲突，默认选择能正确完成当前需求的更简单实现。

在提交实现前，删除所有不能直接服务于当前需求的分支、校验、封装和测试。

- Every session must use Caveman full for terse communication and Ponytail full
  for implementation decisions. Apply both before any repository work unless
  the user explicitly disables or changes either mode.
- Handle normal coding questions with the repository and its tests.
- This GitHub repository is public-code-only. Never commit, push, upload, or
  copy company repositories, Feishu documents, real Wiki pages, credentials,
  or runtime logs into GitHub or another public web space.
- External models are allowed when the user authorizes the model and source
  scope. Keep `local_only` as the default boundary; only include those sources
  after explicit user authorization, such as `--allow-local-llm`.
- When answering from the Wiki, use MemoryForge's own query or agent command.
  Answer from returned evidence, state when it has no answer, and do not expose
  raw local page hashes as the user-facing source.
- Do not import private data, modify source material, commit, push, deploy, or
  run destructive commands unless the user explicitly asks.
- Zero-spend is a hard project constraint. Never enable or use billable APIs,
  models, cloud services, storage, GitHub-hosted runners, subscriptions, or
  metered features. Do not add payment methods, raise spending limits, or
  re-enable repository GitHub Actions. Use local free tools only. If a task
  cannot be completed at zero cost, stop and report the blocker.

## Trae CLI

- Use `/Users/bytedance/.local/bin/trae-cli`; it may not be on `PATH`.
- For non-interactive coding from the repository root:
  `/Users/bytedance/.local/bin/trae-cli exec -C . --ephemeral -m gpt-5.6-sol__max -c 'model_reasoning_effort="xhigh"' --permission-mode auto "<task>"`.
- For conversation recompilation, use `memoryforge recompile conversations
  --workspace <wiki> --trae --allow-local-llm` only after the user authorizes
  sending `local_only` conversations to Trae.
- Never use the dangerous permission-bypass flags.
