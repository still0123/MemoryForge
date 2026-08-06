# Benchmark Registry Research

## Scope

This research covers the v0.3.0 benchmark registry only. It does not justify a
model judge, remote execution service, plugin system, or general task framework.

## EleutherAI lm-evaluation-harness

- Repository: `EleutherAI/lm-evaluation-harness`
- Commit: `f4d4b3de3ee6741a7151a9fe74945ee515262f4c`
- License: MIT
- Files reviewed:
  - `lm_eval/tasks/hellaswag/hellaswag.yaml`
  - `tests/test_task_manager.py`
  - `docs/task_guide.md`

Relevant design:

- task identity, dataset, split, metrics, and task metadata are declarative;
- registry traversal is deterministic;
- duplicate task identities and include cycles are tested;
- task metadata has its own version independent of package releases.

Adopt:

- declarative suite records;
- stable `suite_id` plus integer `suite_revision`;
- deterministic ordering, duplicate rejection, and content SHA256 checks.

Reject:

- YAML function tags and arbitrary Python task loading;
- remote datasets and runtime task discovery;
- a general-purpose task inheritance system.

## OpenAI Evals

- Repository: `openai/evals`
- Commit: `8eac7a7de5215c907fbddc30efdaf316913eccdd`
- License: MIT for code; individual datasets may use other licenses
- Files reviewed:
  - `evals/registry.py`
  - `evals/registry/evals/arc.yaml`
  - `LICENSE.md`

Relevant design:

- registry IDs resolve to explicit evaluation specs;
- split and spec revision are encoded separately from package releases;
- duplicate entries fail during registry loading;
- the registry records metrics next to the evaluation definition.

Adopt:

- one auditable registry entry per stable suite identity;
- explicit development, confirmation, and holdout fields;
- evidence references next to expected metrics.

Reject:

- dynamic class construction;
- API model discovery;
- aliases that obscure the canonical suite identity;
- model-graded evaluation as a release default.

## MemoryForge Decision

MemoryForge uses one checked-in JSON registry and a standard-library validator.
Existing benchmark files remain immutable historical artifacts even when their
legacy filenames contain package-like versions. New suite identity comes only
from `suite_id` and `suite_revision`; new files must not encode an unreleased
package version.

Expected improvement:

- 100% of release metrics resolve to a fixed repository Commit, suite SHA256,
  evidence SHA256, and MemoryForge Commit;
- duplicate suite IDs: 0;
- missing or altered registered artifacts: hard failure;
- default model-judge calls: 0.
