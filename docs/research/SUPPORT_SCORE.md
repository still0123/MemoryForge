# Evidence Support Score Research

## Scope

This research covers deterministic evidence sufficiency, abstention, coverage
selection, and risk-coverage reporting. It does not cover learned rerankers,
model judges, embeddings, or answer generation.

## Haystack

- Repository: `deepset-ai/haystack`
- Commit: `b8cafb2bf6efb905ef097556567c373608f84f4f`
- License: Apache-2.0
- License SHA256:
  `3f0a3af7e9b0f3b6358aaa3c7501ab522a67dc5483b870e9a8b3db41ea42e249`
- Reviewed:
  - `haystack/components/joiners/document_joiner.py`
  - `haystack/components/routers/conditional_router.py`
  - `test/components/joiners/test_document_joiner.py`
  - `test/components/routers/test_conditional_router.py`

Relevant design:

- independent retrieval scores can be normalized and combined with explicit
  weights;
- duplicate evidence keeps the strongest score or receives a weighted merge;
- missing scores sort below scored evidence;
- empty and uninformative inputs have deterministic behavior;
- conditional routing selects the first satisfied route and supports an
  explicit fallback route.

Adopt:

- expose named support components and fixed weights;
- deduplicate Citation identity before coverage scoring;
- route insufficient support to `unknown` through an explicit deterministic
  branch;
- keep multi-source coverage separate from topical similarity.

Reject:

- embedding retrievers, score-distribution fitting, reciprocal-rank fusion,
  Jinja conditions, general pipeline components, and Haystack dependencies.

Reason: MemoryForge already has bounded Facts and needs an auditable score, not
a general retrieval framework.

## Transformers SQuAD 2

- Repository: `huggingface/transformers`
- Commit: `42f189ded85d18d00b51161d694cafd325e32b91`
- License: Apache-2.0
- License SHA256:
  `77fd4710def9ec3c0f6225800e0235f15a425abd4a8b03559127fcd782612049`
- Reviewed:
  - `src/transformers/data/metrics/squad_metrics.py`
  - `examples/pytorch/question-answering/utils_qa.py`
  - `examples/pytorch/question-answering/run_qa.py`
  - `examples/pytorch/question-answering/README.md`

Relevant design:

- no-answer is a first-class candidate;
- the best non-null answer is compared with the null candidate;
- a fixed `null_score_diff_threshold` controls abstention;
- score differences are exported for later audit;
- answerable and no-answer metrics are reported separately.

Adopt:

- treat `unknown` as an explicit competing outcome;
- freeze one development threshold before confirmation;
- export per-case score components and the threshold decision;
- report Answer accuracy, selective accuracy, coverage, abstention accuracy,
  and risk at the operating point separately.

Reject:

- model logits, softmax probabilities, training, SQuAD token spans, GPU
  execution, and Transformers dependencies.

Reason: MemoryForge support comes from deterministic Fact and Citation
properties, not a learned reader.

## MemoryForge Decision

The first candidate applies to Code Wiki answers and uses six normalized
components:

1. exact identifier coverage;
2. core question-term coverage;
3. conclusion and condition co-location;
4. aligned negation;
5. multi-source coverage;
6. current SourceVersion grounding.

Weights are fixed before implementation:

- exact identifier: 20%;
- core terms: 35%;
- co-location: 15%;
- negation: 10%;
- multi-source: 10%;
- current SourceVersion: 10%.

The development threshold is 75%. Current SourceVersion, negation alignment,
and required multi-source coverage are hard gates. Non-Code Wiki answers
receive the components for observability but are not rejected in this focused
phase.

## Expected Metrics

- learn-claude-code development Answer accuracy: 100%;
- selective accuracy: 100%;
- coverage: 90%;
- abstention accuracy: 100%;
- page route, Source recall, fact selection, Citation grounding, multi-source
  coverage, and repository isolation: 100%;
- the unsupported vector-database case changes from `wrong_abstention` to a
  correct `unknown`;
- all public structural and query regressions remain green.
