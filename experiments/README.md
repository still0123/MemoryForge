# 检索实验

semantic_retrieval.py 是一个离线实验脚本，不是 MemoryForge 的生产检索后端。
它在完整 Wiki 页面上做标准库字符 n-gram 排序，与当前 INDEX/FTS5 页面候选路径比较
固定题集的 source_recall_at_3。实验不调用模型、不写 workspace，也不输出答案正文。

运行：

    .venv/bin/python experiments/semantic_retrieval.py \
      --workspace /absolute/path/to/memoryforge-workspace \
      --eval-config demo/evaluation/agent_skill_eval.json \
      --output /private/tmp/memoryforge-semantic-retrieval.json

这个 n-gram 方法只是“语义检索是否值得引入”的低成本代理，不是真正的 Embedding。
只有候选召回提升至少 10 个百分点且页面预算不增加，才考虑接入真正的本地语义后端；
否则继续使用当前轻量 Wiki 检索。

本次使用全新公开 Demo workspace 的固定 30 题实验结论：当前 INDEX/FTS5 页面候选召回率为
100.0%，paraphrase 召回率为 100.0%；n-gram 代理也是 100.0% 和 100.0%，提升为
0 个百分点。因此不并入主链路。完整问答链路的同期结果是答案准确率 93.3%、source recall
92.0%、引用可核验率 100.0%。
