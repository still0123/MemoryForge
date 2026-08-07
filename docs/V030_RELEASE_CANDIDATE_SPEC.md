# v0.3.0 Release Candidate Specification

## Status

`BASELINE_REJECTED_IMPLEMENTATION_PENDING`

## Base

- MemoryForge main Commit:
  `569685c2f0bf790819820b821b4768d180c4ee0d`
- Package baseline: `0.2.1`
- Package target: `0.3.0`
- Runtime: CPython 3.11
- GitHub Actions: disabled
- New runtime dependencies: none

This phase changes release metadata, release-only orchestration, public
documentation, and Evidence. It must not tune query, ranking, support-score,
adapter, Showcase, or Workspace behavior against confirmation or holdout
results.

## Research

Fixed references, licenses, and design decisions are recorded in
`docs/research/V030_RELEASE_DELIVERY.md`.

## Frozen Inputs

Development:

- Path: `demo/evaluation/release_candidate_development.json`
- SHA256:
  `8d9fe33359b71ac0b86b6fa42b0bc5ee34080126af3ae0b9bf2fa2c0121cf2a6`
- Cases: 6

Confirmation:

- Path: `demo/evaluation/release_candidate_confirmation.json`
- SHA256:
  `3eed08e04592b614a709cd18c6a92951b5da145d7dab4b9997824cd4e002e805`
- Component evaluations: 7
- Evaluated cases: 26
- Status: `not_run`

Holdout:

- Path: `demo/evaluation/release_candidate_holdout.json`
- SHA256:
  `b466644bef2748a96d598540c54633fe49770af97fc71ce6e62e18eda687e8f8`
- Cases: 5
- Status: `not_run`

Contract test:

- Path: `tests/test_release_candidate_contract.py`
- SHA256:
  `7b18ce481ca5d0a475a85bb987d78bb6fa5a598cbb6306b7a1c1ef835006d5cc`

The contract test may run during development because it only hashes, parses,
and proves absence of result files. It does not execute confirmation or
holdout cases.

## Baseline Result

- Evidence: `demo/results/release_candidate_baseline_rejected.json`
- Evidence SHA256:
  `2e2674ce071489f92d4a480b4f0c0018e0b416764e9a2df1a7ff8a2fc7640740`
- MemoryForge Commit:
  `4d834679ec61355e285fb36a0cceef8f489a9083`
- Result: `REJECTED`
- Development pass rate: 16.7%
- Failed cases: 5
- Confirmation status: `not_run`
- Holdout status: `not_run`

The only passing case proved that both frozen result paths remained absent.
Failures are retained with deterministic classifications:

- `version_mismatch`;
- `benchmark_summary_mismatch`;
- `artifact_missing`;
- `workspace_drill_failure`;
- `release_document_mismatch`.

## Confirmation Components

The release confirmation manifest binds:

1. exact Symbol routing on the frozen learn-claude-code confirmation set;
2. support-score selection and abstention on the same five cases;
3. multi-source coverage selection;
4. recursive Folder lifecycle and privacy;
5. GitHub Thread lifecycle, pagination, and deduplication;
6. static Showcase privacy and atomic replacement;
7. native Windows process locking and clean-Wheel CLI/Workspace/Demo smoke.

Each component must write deterministic per-case status and failure
classification. Macro pass rate and each component result are reported
separately.

## Development Gate

Before confirmation authorization:

- package, module, CLI, Wheel, and sdist metadata report `0.3.0`;
- strict registry validation passes;
- Ruff, formatting, strict Mypy, dependency checks, full pytest, and coverage
  pass;
- two isolated builds from one clean Commit produce byte-identical Wheel and
  sdist bytes;
- Wheel and sdist clean-room installs pass;
- the public zero-key Showcase builds;
- an isolated Workspace completes refresh, review, approve, apply, lint,
  no-pending ingest, backup, restore, query, and Showcase drills;
- README, CHANGELOG, Evidence Claims, known limits, interview script,
  provenance, and benchmark summary agree;
- confirmation and holdout result files are absent;
- final static review has no P0-P2 finding.

## Confirmation Authorization

Confirmation is opened only after one release-candidate Commit and its
development artifacts are frozen. All seven components run against that same
Commit.

- Native Windows cases must run on native Windows with CPython 3.11.
- Simulated `msvcrt` tests are not confirmation.
- The runner refuses an existing result path.
- A confirmation component is never rerun after failure.
- Any failure remains public Evidence and blocks holdout.
- Production behavior is not tuned after reading confirmation.

The result path is:

`demo/results/release_candidate_confirmation.json`.

## Holdout Authorization

Holdout opens only when all confirmation components pass. It runs once against
the frozen release-candidate Commit and already-built artifacts.

The result path is:

`demo/results/release_candidate_holdout.json`.

The five cases cover clean rebuild reproducibility, installed-Wheel public
Showcase execution, Workspace backup/restore replay, release-asset round-trip,
and public-claims privacy. A failure is retained and blocks release.

## Release Artifacts

The verified release directory must contain:

- `memoryforge-0.3.0-py3-none-any.whl`;
- `memoryforge-0.3.0.tar.gz`;
- `SHA256SUMS`;
- `release-provenance.json`;
- `benchmark-summary.json`;
- native Windows confirmation Evidence;
- release confirmation and holdout Evidence.

All JSON uses repository-relative public paths and excludes credentials,
prompts, private Workspace content, and absolute private paths.

## Tag And Upload

After final artifact verification:

1. merge the focused release PR;
2. rebuild from the exact merged Commit if its tree differs from the frozen
   candidate;
3. verify all SHA256 values from a separate directory;
4. create annotated tag `v0.3.0` at the verified release Commit;
5. create the GitHub Release and upload the fixed artifact set manually;
6. download every asset and verify SHA256 again;
7. keep GitHub Actions disabled.

No asset is silently replaced and no existing release is accepted through a
`skip-existing` path.

## Known Boundaries

- Native Windows confirmation is pending and cannot be inferred from macOS,
  Linux, Mypy, or simulated tests.
- Existing secure directory-descriptor import and publication paths are not
  claimed as full Windows parity.
- No PyPI publication is required by this Goal.
- Historical Click and rejected Candidate results remain visible and are not
  release regressions rewritten as successes.

## Forbidden

- modifying frozen cases, required terms, source paths, or expected results;
- running confirmation before the authorization gate;
- running holdout before confirmation passes;
- rerunning a failed confirmation or holdout to tune production behavior;
- enabling GitHub-hosted Actions;
- adding a paid service or model judge;
- uploading private paths, content, credentials, or prompts;
- deleting or relabeling negative Evidence.
