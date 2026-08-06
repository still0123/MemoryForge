# MemoryForge Agent

This repository contains MemoryForge and its own Feishu demonstration agent.

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
