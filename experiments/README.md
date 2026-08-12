# 检索实验

semantic_retrieval.py 是一个离线实验脚本，不是 MemoryForge 的生产检索后端。
它在固定题集上比较当前候选路径、SQLite FTS5 BM25、字符 n-gram、文档频率和本地 RRF 的
source_recall_at_3。实验不调用模型、不写 workspace，也不输出答案正文。

运行：

    .venv/bin/python experiments/semantic_retrieval.py \
      --workspace /absolute/path/to/memoryforge-workspace \
      --eval-config demo/evaluation/agent_skill_eval.json \
      --output /private/tmp/memoryforge-semantic-retrieval.json

这个 n-gram 方法只是“语义检索是否值得引入”的低成本代理，不是真正的 Embedding。
RRF 也只在内存中合并已有排名，不依赖 Elasticsearch。只有 RRF 总体召回提高且没有逐题回退，
才允许进入生产候选；否则保持当前检索。

本次使用全新公开 Demo workspace 的固定 30 题实验结论：当前 INDEX/FTS5 页面候选召回率为
100.0%，paraphrase 召回率为 100.0%；n-gram 代理也是 100.0% 和 100.0%，提升为
0 个百分点。因此不并入主链路。完整问答链路的同期结果是答案准确率 93.3%、source recall
92.0%、引用可核验率 100.0%。

2026-08-12 使用当前脚本重建固定公开 Workspace 后，页面候选对照为：

- AgentSkill-Eval：current 46.2%，BM25 50.0%，RRF 57.7%；RRF 无逐题回退；
- Click development：current 62.5%，BM25 87.5%，RRF 75.0%；RRF 有 1 个逐题回退；
- Click holdout：current 50.0%，BM25 75.0%，RRF 50.0%；RRF 无净提升。

不同实验版本的候选口径不能横向混用。按“跨冻结集无回退且 holdout 有净提升”门禁，当前决策为
`keep_current`：不替换生产排序，不加入向量库或外部检索服务。
