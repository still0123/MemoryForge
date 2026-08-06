# MemoryForge v0.2.1 External Document Wiki Benchmark

## Question

The main branch already contains a Click external-document benchmark. This
independent replication freezes a different 20-question Click suite to test
whether the negative external-validity result is stable without changing
questions after seeing results.

## Frozen Data

- Repository: `pallets/click`
- Commit: `00e592cea702e0b2caa0dee42489fdb1c22cd845`
- License: BSD-3-Clause
- License SHA256:
  `9a8ad106a394e853bfe21f42f4e72d592819a22805d991b5f3275029292b658d`
- Development split: 10 questions
- Holdout split: 10 questions
- Each split: 5 single-source, 2 multi-source, 1 unanswerable, 2 paraphrase

The source manifest and both suites were committed at `e140690` before the
first evaluation. The frozen manifest counted 43 tracked Markdown or text
candidates. MemoryForge imported 38 after excluding three `.github` templates,
`CHANGES.md`, and `LICENSE.txt`.

## Results

| Metric | Development | Holdout | Combined |
| --- | ---: | ---: | ---: |
| Answer accuracy | 10.0% | 0.0% | 5.0% |
| Expected source recall | 22.2% | 11.1% | 16.7% |
| Citation grounding | 100.0% | 88.9% | 94.4% |
| Multi-source coverage | 0.0% | 0.0% | 0.0% |
| Abstention accuracy | 0.0% | 0.0% | 0.0% |
| Raw FTS source recall | 88.9% | 88.9% | 88.9% |
| Raw FTS multi-source coverage | 100.0% | 50.0% | 75.0% |

Both splits expanded exactly three Wiki pages per question. Combined average
selected evidence was 263.6 characters; Raw FTS exposed 726.8 candidate
characters.

This is a negative external-validity result. The Click suite does not pass the
Phase 4 requirement that Citation integrity remain at 100%.

## Diagnosis

Three independent failure groups recur:

1. Page routing overweights generic INDEX summary terms such as the repository
   name, while the relevant fact often appears deeper in the page.
2. Fact selection often prefers the first summary fact after the correct page
   is found, but the answer requires a later or adjacent fact.
3. English unanswerable questions share enough generic terms with unrelated
   pages to produce false answers.

The existing dependency-free character n-gram experiment improved development
page-level source recall from 33.3% to 88.9%. It was not integrated: page recall
alone does not fix fact selection or abstention, and the frozen holdout is now
consumed. A new retrieval iteration requires a new DatasetVersion and a new
holdout.

No production query logic, question, expected source, or required term was
changed after execution.

## Regression

The frozen AgentSkill-Eval 30-question suite was rerun unchanged:

- answer accuracy: 100.0%
- expected source recall: 96.2%
- Citation grounding: 100.0%
- multi-source coverage: 100.0%
- abstention accuracy: 100.0%

The existing alternative-source mismatch for the frontend framework question
remains recorded.

## Reproduction

```bash
python demo/run_public_demo.py \
  --source-repo /absolute/path/to/click \
  --workspace /private/tmp/memoryforge-click-dev \
  --output /private/tmp/click-dev.json \
  --eval-config demo/evaluation/external_document_click_dev_v021.json \
  --question "What command installs Click from PyPI?"

python demo/run_public_demo.py \
  --source-repo /absolute/path/to/click \
  --workspace /private/tmp/memoryforge-click-holdout \
  --output /private/tmp/click-holdout.json \
  --eval-config demo/evaluation/external_document_click_holdout_v021.json \
  --question "Which context setting can make Click option tokens case-insensitive?"
```

The source checkout must use the frozen commit above. Two runs from separate
empty workspaces produced byte-identical evidence for each split.

## Evidence

- [`external_document_click_dev_v021.json`](../demo/results/external_document_click_dev_v021.json)
  SHA256 `95d31ac8445e497165f49f018da84e6fef32a7ac215b78ce33ee08b8c30030fb`
- [`external_document_click_holdout_v021.json`](../demo/results/external_document_click_holdout_v021.json)
  SHA256 `dee599644307b2bcae087a7fad0279bd7ac8cb34d3b24882fabb934d1c98c871`
- [`external_document_click_semantic_dev_v021.json`](../demo/results/external_document_click_semantic_dev_v021.json)
  SHA256 `b632e2bf36cdf0f6783ea251cbf016433a6c1f58ebe31bf0f2923a6ddaa96181`
