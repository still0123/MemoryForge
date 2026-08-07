# v0.3.0 Release Candidate Specification

<!-- memoryforge-release-claim: version=0.3.0; status=release_candidate; active_candidate=3; platform_gate_candidate=2; platform_gate_status=superseded; macos_passed=583; linux_passed=580; linux_skipped=3; windows_confirmation=not_run; confirmation=not_run; holdout=not_run -->

## Status

`DEVELOPMENT_ACCEPTED_LOCAL_GATES_PENDING`

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

## Superseded Development Candidate 1

- Evidence: `demo/results/release_candidate_development_candidate_1.json`
- Evidence SHA256:
  `9c9a40dcd491613ca55f54e7b25ba78993be0eef0ee7fff4dbccf6f65fca3695`
- MemoryForge Commit:
  `b51a90d9603c2558ae72817bfbc8f291c3933812`
- Result: `ACCEPTED_DEVELOPMENT_SUPERSEDED`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`

Candidate 1 passed the original development checks and dual-platform local
gates, but final static review found release Evidence checks weaker than their
claims. The result and artifacts remain retained; they no longer authorize a
release-candidate freeze.

## Superseded Candidate 1 Local Gates

- Evidence: `demo/results/release_candidate_candidate_1_local_gates.json`
- Evidence SHA256:
  `94924487672989ea216a1728caf77f94bfeb094cae496c78f832ec2838f65d9b`
- Gate Commit:
  `3980b47fec0a8abc001c4df740b6924d3f32223a`
- macOS: 574 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 571 passed, 3 skipped, coverage 88%
- Wheel SHA256:
  `05e3494a476bc46c1138ba45d9b732132c6f545c428d1a4e7ac47d405675cbe7`
- sdist SHA256:
  `856fa7dc13eb9cd9420504d02145781a6673670162d01de034db13438b680c0e`

Both platforms passed Ruff, formatting, strict Mypy, dependency checks,
strict Registry validation, Wheel clean-room, sdist clean-room, `pip check`,
and CLI version smoke. Actual artifacts, per-platform provenance, and
SHA256SUMS are retained under
`demo/results/artifacts/release_candidate_delivery_candidate_1/`.
GitHub Actions remained disabled. Native Windows confirmation and holdout
remain `not_run`.

## Rejected Candidate 2 Preflight

- Evidence:
  `demo/results/release_candidate_sdist_probe_regression_rejected.json`
- Evidence SHA256:
  `b0c18c7e2d23d47e3cb8cb1200c3511dc9a4bb560ac81e531e4492c5f1353d5b`
- MemoryForge Commit:
  `94b136e0ddda947c14e4ab0297b6505e00b9c63f`
- Result: `REJECTED`
- Classification: `sdist_clean_room_path_alias`
- Release output created: false
- Confirmation status: `not_run`
- Holdout status: `not_run`

The strict sdist import-ownership probe exposed the macOS `/var` to
`/private/var` alias. The fix canonicalizes the environment path and keeps the
ownership check; it does not weaken the gate.

## Superseded Development Candidate 2

- Evidence: `demo/results/release_candidate_development_candidate_2.json`
- Evidence SHA256:
  `84a5a2e3eefb6894d512a0aea6ccc4626844ceaaab55a28b3a96f733f84b0792`
- MemoryForge Commit:
  `4972b3c2223c5e6fe7248090a9d8ee006c1c271b`
- Result: `ACCEPTED_DEVELOPMENT_SUPERSEDED`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`

Candidate 2 validates actual Wheel and sdist metadata and bytes, exact source
Commit binding, independent sdist clean-room installation, deterministic
malformed-artifact classification, structured document boundaries, and atomic
release publication.

## Superseded Candidate 2 Local Gates

- Evidence: `demo/results/release_candidate_candidate_2_local_gates.json`
- Evidence SHA256:
  `fd6c6f2475848b79cf7c89a212d0f5ba2e0c52846cec7e572cc406dd3d8de092`
- Gate Commit:
  `926832e28503b69d83c2ca760d3ad0065615b5a8`
- macOS: 583 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 580 passed, 3 skipped, coverage 88%
- Wheel SHA256:
  `b6f6cc6f869c9bcfbb83a162d5b1bb622a44f0b86855b6db9a183c21845fb803`
- sdist SHA256:
  `f908bc5431d2486cc380c5abb30d255004a14ee9faf9c4040835c5f4539fc4d2`

Both platforms passed the complete local gate. Actual artifacts and
per-platform provenance are retained under
`demo/results/artifacts/release_candidate_delivery_candidate_2/`.
Final static review must pass before confirmation authorization.

## Rejected Candidate 2 Static Review

- Evidence:
  `demo/results/release_candidate_candidate_2_static_review_rejected.json`
- Evidence SHA256:
  `475b6e5981bc43438107c67a7fd3ab05fc95888bfb30c391f2e7ae2275c23d45`
- Reviewed Commit:
  `433f33c001c963cd69dd507346ac836895b7c36b`
- Result: `REJECTED`
- P0: 0
- P1: 5
- P2: 1
- Confirmation status: `not_run`
- Holdout status: `not_run`

The review found remaining gaps between release claims and independently
retained Evidence. Candidate 2 remains visible but no longer authorizes
confirmation.

## Accepted Development Candidate 3

- Evidence: `demo/results/release_candidate_development_candidate_3.json`
- Evidence SHA256:
  `c61e5817c9a55e2bda780a7381512087c0a37943d8d34bb0e0a54a880a074349`
- MemoryForge Commit:
  `5005f1511301797d7d1a9ce25c3a885ab6ba85ba`
- Result: `ACCEPTED_DEVELOPMENT`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`

Candidate 3 retains both isolated build outputs, binds a structured release
claim across public documents, rejects non-executed clean-room checks, scans
secret-bearing provenance keys, and rechecks summary source identity.
Candidate 3 macOS/Linux gates and final static review remain pending.

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
