# v0.3.0 Release Candidate Specification

<!-- memoryforge-release-claim: version=0.3.0; status=release_candidate; active_candidate=12; platform_gate_candidate=11; platform_gate_status=accepted; review_status=pending; macos_passed=609; linux_passed=606; linux_skipped=3; windows_confirmation=not_run; confirmation=not_run; holdout=not_run -->

## Status

`DEVELOPMENT_PASSED_GATE_PENDING`

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

Candidate 3 binds a structured release claim across public documents, rejects
non-executed clean-room checks, scans secret-bearing provenance keys, and
rechecks summary source identity. Its development Evidence did not register
both isolated build byte sets, so that historical reproducibility claim is not
independently verifiable. Candidate 6 final-review Evidence retains this
negative audit finding.

## Accepted Candidate 3 Local Gates

- Evidence: `demo/results/release_candidate_candidate_3_local_gates.json`
- Evidence SHA256:
  `54e6bbd11d952c7f18afb7f4d637c5285daf6f4ac7596f649eeb5710af08bfcc`
- Gate Commit:
  `40cabe1dc5c3869ce67da60d3ce8bdbf883bc1a6`
- macOS: 586 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 583 passed, 3 skipped, coverage 88%
- Wheel SHA256:
  `466f8ebab1253a26bb2609596e54fb6539ebddff7df22598c95999ef525a7843`
- sdist SHA256:
  `7c2de1bc1fff12a69e9b222f1291a624d9780ce18004a49c177fb8446e3434f7`

Both platforms passed the complete local gate. Final static review remains
required before confirmation authorization.

## Rejected Development Candidate 4

- Evidence:
  `demo/results/release_candidate_development_candidate_4_rejected.json`
- Evidence SHA256:
  `2b55864983c0b5cc7fa9b3819b9a94b68cdd3192bd55b156fbcc5f564df48fbc`
- MemoryForge Commit:
  `7e998d509ee7a4aba31f269e16699d18343ec978`
- Result: `REJECTED`
- Development pass rate: 83.3%
- Failed cases: 1
- Error classification: `release_document_mismatch`
- Confirmation status: `not_run`
- Holdout status: `not_run`

Candidate 4 proved both isolated builds, the Workspace drill, package
metadata, Registry summary, and frozen split closure. Its document gate
incorrectly treated retained historical platform counts as contradictory
current release claims. The failed Evidence and both build byte sets remain
registered; the shared document validator must distinguish the canonical
release marker from historical Evidence before another candidate runs.

## Accepted Development Candidate 5

- Evidence: `demo/results/release_candidate_development_candidate_5.json`
- Evidence SHA256:
  `a64d26d8103c5bc7c0e2f61627fe978f57920a31d4be5252866cbbe354e6d861`
- MemoryForge Commit:
  `b42d6a887053464f138f87dd45922d22dc58baa0`
- Result: `ACCEPTED_DEVELOPMENT`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`

Candidate 5 limits current-claim validation to the canonical release marker
and required current claims while retaining all historical platform Evidence.
Both isolated build byte sets are registered.

## Accepted Candidate 5 Local Gates

- Evidence: `demo/results/release_candidate_candidate_5_local_gates.json`
- Evidence SHA256:
  `0eb8fe924537f6fc8ecdf6657e117abda3771705678364835b7e327fea43a808`
- Gate Commit:
  `88a0b101aad708190331d42ac1557e1cd44be114`
- macOS: 586 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 583 passed, 3 skipped, coverage 88%
- Wheel SHA256:
  `f96013bf056ddfd5f0bc0da3a2df60b6ca819433b20095745bf9a84d89de6360`
- sdist SHA256:
  `8d682330b75a93163dc13fdd4b1ae5b2e901d506b65c3edd18586463ccf78b88`

Both platforms passed Ruff, formatting, strict Mypy, Registry validation,
dependency checks, the full pytest suite, Wheel clean-room, sdist clean-room,
`pip check`, and CLI version smoke.

## Rejected Candidate 5 Static Review

- Evidence:
  `demo/results/release_candidate_candidate_5_static_review_rejected.json`
- Evidence SHA256:
  `7973225fac1123040b93674bbcb2d5df38872229772e9711015af064cbd39913`
- Reviewed Commit:
  `26767333bc20a6367bc87f239cdc956cd40e7f4e`
- Result: `REJECTED`
- P0: 0
- P1: 10
- P2: 2
- Confirmation status: `not_run`
- Holdout status: `not_run`

The review found remaining bypasses in source snapshot identity, package and
provenance schema, SHA256SUMS closure, retained artifact ownership, Registry
Commit binding, document claims, and Showcase privacy measurement. Raw
findings and the review Top 5 report remain under
`demo/results/artifacts/release_candidate_review_candidate_5/`.
Candidate 5 no longer authorizes confirmation.

## Accepted Development Candidate 6

- Evidence: `demo/results/release_candidate_development_candidate_6.json`
- Evidence SHA256:
  `8d1f1a07f218581048e3860556e12dea0a900f2189876f28265afeffbf8093a3`
- MemoryForge Commit:
  `9a6c145a3f052c78b47c4d8f882d4a3191c4a2f4`
- Result: `ACCEPTED_DEVELOPMENT`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`

Candidate 6 builds from a detached Commit snapshot and strictly validates
package identity, provenance schema, exact Benchmark summary, SHA256SUMS,
retained artifact ownership, supporting JSON Evidence, Registry Commit
identity, document completion claims, and Showcase privacy. Both isolated
build byte sets and all supporting JSON artifacts are registered.

## Accepted Candidate 6 Local Gates

- Evidence: `demo/results/release_candidate_candidate_6_local_gates.json`
- Evidence SHA256:
  `37b1df73117f5e9260861a76abc79af6f614fba4399cab4ba6a3d1c567ce394d`
- Gate Commit:
  `4440b05e4200ceb939d5668f7a8dd73a77a69287`
- macOS: 594 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 591 passed, 3 skipped, coverage 88%
- Wheel SHA256:
  `a0ddda5469074b9aa4222f42aea95a411316be5878d54833101d0dece3ccc811`
- sdist SHA256:
  `dfb59503b821e8eaf641cfede9111753cae52faa74b7920182a64f1e4c6a2eab`

Both platforms passed Ruff, formatting, strict Mypy, Registry validation,
dependency checks, the full pytest suite, Wheel clean-room, sdist clean-room,
`pip check`, and CLI version smoke.

## Rejected Candidate 6 Static Review

- Evidence:
  `demo/results/release_candidate_candidate_6_static_review_rejected.json`
- Evidence SHA256:
  `cc3ae3f9a5f99f5d420e2b4cfce1f12cc260b46d99b03b34f93632f5c47dcacc`
- Reviewed Commit:
  `9588c2fb6a41225515165f0114ce61f23f51d921`
- Result: `REJECTED`
- P0: 0
- P1: 9
- P2: 3
- Confirmation status: `not_run`
- Holdout status: `not_run`

The review proved that Candidate 6 development and local gates used different
Wheel/sdist bytes after package inputs changed. It also found weaker Registry
consumers for retained support JSON, local-gate benchmark provenance, path
ownership, privacy, and strict JSON types. Raw findings and the Top 5 report
remain under `demo/results/artifacts/release_candidate_review_candidate_6/`.
Candidate 6 no longer authorizes confirmation.

## Accepted Development Candidate 7

Candidate 7 uses a stable package README, an explicit minimal sdist include
set, and a fixed `SOURCE_DATE_EPOCH`. Evidence, Registry, tests, and release
status documents can therefore be appended after development without changing
the Wheel/sdist bytes. Its acceptance contract requires macOS and Linux
local-gate artifact hashes to equal the development artifact hashes.

- Evidence: `demo/results/release_candidate_development_candidate_7.json`
- Evidence SHA256:
  `337393de3ea54605055fd08f29fa92679ca3db52470879080cc0c92c5dd5ff10`
- MemoryForge Commit:
  `80b111bbd472cacd16ceb773a4c141e70ee97a4a`
- Result: `ACCEPTED_DEVELOPMENT`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`

## Rejected Candidate 7 First Local Gate

- Evidence:
  `demo/results/release_candidate_candidate_7_local_gate_contract_rejected.json`
- Evidence SHA256:
  `921d5595531bc3b8427b4080264f366f02b40909e01312fc340ce417c298aa57`
- Gate Commit:
  `0da3092733e0cf549d3e55ed50ed2413374a5cfb`
- macOS: 598 passed, 1 failed
- Linux: `not_run`
- Classification: `outdated_sdist_manifest_contract`
- Confirmation status: `not_run`
- Holdout status: `not_run`

The old local-tooling test still required `sdist.exclude` after Candidate 7
replaced it with an explicit stable `sdist.include` set. The failure is
retained; the package bytes themselves remained identical to development.

## Accepted Candidate 7 Local Gates

- Evidence: `demo/results/release_candidate_candidate_7_local_gates.json`
- Evidence SHA256:
  `ce550b6ff8cccb17f4fb3bf2dff758d8755c4d05221ccd275b1b030c34e961f3`
- Gate Commit:
  `249a89b36518452d64a56d902c41c81027976c1b`
- macOS: 599 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 596 passed, 3 skipped, coverage 88%
- Registry at gate time: 12 suites, 8 experiments, 113 Evidence, 121 QA
- Wheel SHA256:
  `fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096`
- sdist SHA256:
  `2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05`

Both platforms passed Ruff, formatting, strict Mypy, Registry validation,
dependency checks, full pytest, Wheel clean-room, sdist clean-room, `pip
check`, and CLI version smoke. Both platform builds are byte-identical to
Candidate 7 development artifacts. Final static review remains required
before confirmation authorization.

## Rejected Candidate 7 Static Review

- Evidence:
  `demo/results/release_candidate_candidate_7_static_review_rejected.json`
- Evidence SHA256:
  `94b841b8148f40049e3b226b705294527767acf7567a5a456b8706edcde3b501`
- Reviewed range:
  `569685c2f0bf790819820b821b4768d180c4ee0d...a044337347b9c6884ea660c7568c4e3911c84521`
- Result: `REJECTED`
- P0: 0
- P1: 10
- P2: 3
- Confirmation status: `not_run`
- Holdout status: `not_run`

The review found cross-checkout EOL instability, incomplete semantic closure
for retained summaries and frozen manifests, replay gaps in Workspace drill,
symlink ownership gaps, non-replayable retained SHA256SUMS, and historical
review ranges that depended on movable refs. Raw 13 findings, Top 5, fixed
review scope, HTML, and Markdown reports remain under
`demo/results/artifacts/release_candidate_review_candidate_7/`. Candidate 7
does not authorize confirmation.

## Accepted Development Candidate 8

- Evidence: `demo/results/release_candidate_development_candidate_8.json`
- Evidence SHA256:
  `37b0270bba89da81815f2ac00fbeec10e766c8a16436e28ab1e7a2fd449afe83`
- MemoryForge Commit:
  `2451f2dae8845b490db1cb46727c7828f0d227f7`
- Result: `DEVELOPMENT_PASSED_GATE_PENDING`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`
- Wheel SHA256:
  `fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096`
- sdist SHA256:
  `2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05`

Candidate 8 fixes all 13 Candidate 7 review findings: checkout EOL is
frozen, retained summaries are rebuilt from fixed Registry snapshots,
confirmation counts come from hashed cases, Summary schema 2 binds acceptance
and negative Commit identities, release symlinks are rejected, Workspace
answer/Citation/unknown/replay checks are real, and historical review scopes
are fixed by immutable Commit sidecars. Local gates and final review remain
pending; confirmation stays closed.

## Accepted Candidate 8 Local Gates

- Evidence: `demo/results/release_candidate_candidate_8_local_gates.json`
- Evidence SHA256:
  `65486449522da88a80a734f30af81cf8881cdd41b12766440f9a50aac7d0930e`
- Gate Commit:
  `4c6f8e64e4dcda725966d7982ad3f2630814432f`
- macOS: 604 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 601 passed, 3 skipped, coverage 88%
- Registry at gate time: 12 suites, 8 experiments, 116 Evidence, 121 QA
- Wheel SHA256:
  `fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096`
- sdist SHA256:
  `2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05`

Both platforms passed the complete local gate and produced package bytes
identical to Candidate 8 development. Each platform's retained directory
preserves the original `dist/`, `release-provenance.json`, and `SHA256SUMS`
layout; `shasum -a 256 -c SHA256SUMS` succeeds in place. Final static review
remains required before confirmation authorization.

## Rejected Candidate 8 Static Review

- Evidence:
  `demo/results/release_candidate_candidate_8_static_review_rejected.json`
- Evidence SHA256:
  `f2456842b969565a221d962fc48f95264cbe22ccce13fca960b21b1a155f043a`
- Reviewed range:
  `569685c2f0bf790819820b821b4768d180c4ee0d...f4dde0904e5bcaeb78be6d7a32e74a6beae5679a`
- Result: `REJECTED`
- P0: 0
- P1: 4
- P2: 2
- Confirmation status: `not_run`
- Holdout status: `not_run`

The review found a schema-2 downgrade path, a root JSON boolean alias,
incomplete review-scope recomputation, unclean version-probe environments,
Showcase metrics not derived from final replay cases, and a weak Code Wiki
provenance consumer. All six findings and the fixed review scope are retained.
Candidate 8 does not authorize confirmation.

## Accepted Development Candidate 9

- Evidence: `demo/results/release_candidate_development_candidate_9.json`
- Evidence SHA256:
  `cabe2c738be47c5e3d73b371c78a32b1dbaea899f1ce743234327316f67ded0b`
- MemoryForge Commit:
  `63326fb2f123c336c31bcebf68c76c90dfac86e6`
- Result: `DEVELOPMENT_PASSED_GATE_PENDING`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`
- Wheel SHA256:
  `fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096`
- sdist SHA256:
  `2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05`

Candidate 9 closes all six Candidate 8 review findings without changing any
frozen split. Summary schema 2 is mandatory by Commit ancestry, root Registry
types are strict, historical review scope is recomputed from fixed Commits,
version probes use a clean Python environment, Showcase metrics derive from
final replay cases, and complete Code Wiki Evidence is retained and validated.
Local gates and the rejected final review are recorded below; confirmation
stays closed.

## Accepted Candidate 9 Local Gates

- Evidence: `demo/results/release_candidate_candidate_9_local_gates.json`
- Evidence SHA256:
  `a624fd50375639da4b4727fdac656e3d73074a1c3d845f0204356da0bf1ea48c`
- Gate Commit:
  `53caa517ac4e11079767ee26633f8f3be9f55d0d`
- macOS: 607 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 604 passed, 3 skipped, coverage 88%
- Registry at gate time: 12 suites, 8 experiments, 119 Evidence, 121 QA
- Wheel SHA256:
  `fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096`
- sdist SHA256:
  `2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05`
- raw Code Wiki Evidence SHA256:
  `f5fc93493984c1259a39f8d35578f98defb5c8adb14be59039bac17489097b9e`

Both platforms passed the complete local gate. Wheel, sdist, and raw Code Wiki
Evidence bytes match across platforms; each retained SHA256SUMS replays in
place. The rejected final static review is recorded below.

## Rejected Candidate 9 Static Review

- Evidence:
  `demo/results/release_candidate_candidate_9_static_review_rejected.json`
- Evidence SHA256:
  `bda916939f33f5ca10ad0c78733f01197fd8fc5924712d2b9aa1f37e4bebda2d`
- Reviewed range:
  `569685c2f0bf790819820b821b4768d180c4ee0d...c23de5915e6237700c0aa8e03a14e44583ab1049`
- Result: `REJECTED`
- P0: 0
- P1: 10
- P2: 4
- Confirmation status: `not_run`
- Holdout status: `not_run`

The review found retained semantic gaps, an incomplete review state machine,
remaining build-environment dependencies, weak package/privacy replay, and
historical review scopes that were not all recomputed from fixed Commits. All
14 raw findings, Top 5, fixed scope, HTML, and Markdown reports are retained.
Candidate 9 does not authorize confirmation.

## Rejected Development Candidate 10

- Evidence:
  `demo/results/release_candidate_development_candidate_10_rejected.json`
- Evidence SHA256:
  `6a52f9ffda29b8c49dd2e428683294d4d108fee22bebebafec91552f126a8b14`
- MemoryForge Commit:
  `a4c74bdb8047bb6267955624c7d054d17bb5e722`
- Result: `REJECTED`
- Development pass rate: 83.3%
- Failed case: `workspace-release-drill`
- Error classification: `workspace_drill_failure`
- Confirmation status: `not_run`
- Holdout status: `not_run`

Candidate 10 generated Summary schema 3 and Workspace drill schema 2
successfully. The development consumer still required the old drill schema 1,
so the run was rejected at 5/6. The output and all generated artifacts are
retained; Candidate 10 is not rerun.

## Accepted Development Candidate 11

- Evidence: `demo/results/release_candidate_development_candidate_11.json`
- Evidence SHA256:
  `690b4d46e4191e3e0b267e9100fd3f61fa0cbdd8f9661784b2dd82405a2ac396`
- MemoryForge Commit:
  `f174d1de4e459c4b324f0ba5f58e8df62263fa00`
- Result: `DEVELOPMENT_PASSED_GATE_PENDING`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`
- Wheel SHA256:
  `fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096`
- sdist SHA256:
  `2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05`

Candidate 11 uses one schema-aware consumer for retained Workspace Evidence.
Summary schema 3 binds repository, development split, expected metrics, and all
Evidence identities; drill schema 2 retains exact query, Citation, unknown,
case metrics, and backup/restore replay. Local gates and final review remain
recorded below; final review remains pending and confirmation stays closed.

## Accepted Candidate 11 Local Gates

- Evidence: `demo/results/release_candidate_candidate_11_local_gates.json`
- Evidence SHA256:
  `c9b61fbc81951aa87944e2866a2693c97838b40b26fea3baed7ff92383f539a3`
- Gate Commit:
  `ee298f07a66c4889b999752fb07555993c16716c`
- macOS: 609 passed, 0 skipped, coverage 88%
- Debian 12 / Lima: 606 passed, 3 skipped, coverage 88%
- Registry at gate time: 12 suites, 8 experiments, 123 Evidence, 121 QA
- Wheel SHA256:
  `fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096`
- sdist SHA256:
  `2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05`
- raw Code Wiki Evidence SHA256:
  `5421009566d32b77cd0aee34e1f5bf23ddfb9ebd7cad0b6932d9c38d7a84a001`

Both platforms passed the complete local gate. Wheel, sdist, and raw Code Wiki
Evidence bytes match across platforms; each retained SHA256SUMS replays in
place. The rejected final static review is recorded below.

## Rejected Candidate 11 Static Review

- Evidence:
  `demo/results/release_candidate_candidate_11_static_review_rejected.json`
- Evidence SHA256:
  `45ab7e09a98b6e888332a16ed03be1944c3f14ea5851ac4d700fde986e02b1f6`
- Reviewed range:
  `569685c2f0bf790819820b821b4768d180c4ee0d...43d5c80852595c7b49e46c66f70ced82c53cf7d0`
- Result: `REJECTED`
- P0: 0
- P1: 8
- P2: 4
- Confirmation status: `not_run`
- Holdout status: `not_run`

The review found remaining exact package/query schemas, environment isolation,
privacy, artifact-root closure, Registry snapshot binding, and accepted-review
transition gaps. All 12 independent findings and fixed scope are retained.
Candidate 11 does not authorize confirmation.

## Accepted Development Candidate 12

- Evidence: `demo/results/release_candidate_development_candidate_12.json`
- Evidence SHA256:
  `d66093fdd468501ef5c43045cb2d72579bf97a83d0a29602d51447d3e92d816c`
- MemoryForge Commit:
  `bced2a660ef38dfc4c0c6a0f994897d6af895574`
- Result: `DEVELOPMENT_PASSED_GATE_PENDING`
- Development pass rate: 100.0%
- Failed cases: 0
- Reproducible Wheel/sdist: true
- Private detail leaks: 0
- Confirmation status: `not_run`
- Holdout status: `not_run`
- Wheel SHA256:
  `fd3a0ab7cd24e5148408250a220db44eb378ff705770593784c17ec687878096`
- sdist SHA256:
  `2cbe617826ce0b9b7e2bd3da66f22bb7b5c05cd894426d80ee1d47a140ac7a05`

Candidate 12 closes all twelve Candidate 11 findings with exact package/query
schemas, Registry-to-Commit binding, cross-platform path privacy, clean-room
import ownership, fixed Git fixture identity, PowerShell initialization
restoration, replayable artifact-root closure, and passed final-review
governance. The two development replays are byte-equivalent at the evaluation
level. Candidate 12 local macOS/Linux gates and final review remain pending;
the retained Candidate 11 platform counts do not authorize confirmation.

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
- `workspace-drill.json`;
- four `reproducibility-{first,second}-*` build artifacts;
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
