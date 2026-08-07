# Cross-Platform Delivery Specification

## Status

`CANDIDATE_6_LOCAL_GATES_PASS_REVIEW_PENDING`

## Base

- MemoryForge Commit:
  `85b52fd7b4730f9556d460b6bb66e8e1022e897f`
- Package target: `0.3.0`
- Runtime: Python 3.11
- New runtime dependencies: none
- GitHub Actions: disabled

## Frozen Inputs

- Development suite:
  `demo/evaluation/cross_platform_delivery_development.json`
- Development SHA256:
  `0082594121022f6f97b6eae3d4106819794c0b988f531a02e51b9ec2194fb6ff`
- Development test:
  `tests/test_cross_platform_delivery.py`
- Development test SHA256:
  `08601b7effba8271ac318a58a5b0906b934bdeaa4564edcd0ec5738e61618ac3`
- Development cases: 7
- Confirmation suite:
  `demo/evaluation/cross_platform_delivery_confirmation.json`
- Confirmation SHA256:
  `200074acfb79979bf4663b97ae25e9dd62fa0f94359b8aa0d39c4250256660fc`
- Confirmation cases: 3
- Confirmation status: `not_run`

The development test and both suite files are frozen before production code
changes. Confirmation must not run during development.

## Baseline Result

- Evidence:
  `demo/results/cross_platform_delivery_baseline_rejected.json`
- MemoryForge Commit:
  `bef6c7e35e9d8e282d2b3b0e0c4b3874a12f9e8a`
- Evidence SHA256:
  `f169486f1fc757abaaf3728187703834510726c2cae11736c2e82ba220e369ac`
- Result: `REJECTED`
- Development pass rate: 0.0%
- Failed cases: 7
- Deterministic replay: passed
- Confirmation status: `not_run`

The retained Evidence proves seven deterministic generic `pytest_failure`
results before `memoryforge.platform_lock` existed. The interactive command
log showed `ModuleNotFoundError`, but that diagnostic was not included in the
frozen JSON; therefore the exact exception is not a public Evidence claim.

## Candidate 1 Result

- Evidence:
  `demo/results/cross_platform_delivery_candidate_1.json`
- MemoryForge Commit:
  `96c720cf49ed0bfc97fd765e9af025ab6f4ae9ea`
- Evidence SHA256:
  `f2bd8afa6759c3d1ddbc796444cfb58d968fbab82818c27f1c0f24590013801e`
- Result: `DEVELOPMENT_PASS_REGRESSION_PENDING`
- Development pass rate: 100.0%
- Failed cases: 0
- Deterministic evaluation SHA256:
  `04e69c51fd4ca959da432fc501db91c06bfcc3d1ef04002042dee5fa147b3f54`
- Focused regression: 63 passed
- Full pytest: 526 passed
- Ruff, format, strict Mypy, registry, and portable smoke: passed
- Confirmation status: `not_run`

The native Windows type audit leaves 27 errors in the documented POSIX
directory-descriptor and `fchmod` paths. It reports no error in
`platform_lock.py`. This is retained as a negative portability boundary and
is not represented as native Windows confirmation.

## Candidate 2 Full Local Gate

- Development Evidence:
  `demo/results/cross_platform_delivery_candidate_2.json`
- Development Evidence SHA256:
  `22a309f133008268e857e4331f70967d58f0c06adc45ab1988f8a99ee3c34775`
- Gate Commit:
  `7d0a296ffbbb73863b63ec732608a6e3c0bab35b`
- Acceptance Evidence:
  `demo/results/cross_platform_delivery_candidate_2_local_gate.json`
- Acceptance Evidence SHA256:
  `6318d9bf999163917441c65e8085bce3548424b6a7183b4c284e7f9c43b9b2d7`
- Development pass rate: 100.0%
- Deterministic evaluation SHA256:
  `04e69c51fd4ca959da432fc501db91c06bfcc3d1ef04002042dee5fa147b3f54`
- Ruff check and format: passed
- Strict Mypy: passed
- Registry validation: 12 suites / 7 experiments / 73 Evidence / 121 QA
- Dependency check: passed
- Pytest: 529 passed
- Coverage: 88%
- Wheel clean-room: passed
- sdist clean-room: passed
- `pip check`: passed
- CLI version smoke: passed
- Confirmation status: `not_run`

Candidate 2 passed development and the local gate, then became superseded when
static review reproduced these P1 defects:

- replacing the separate ChangeSet lock-file inode split serialization;
- repository-controlled pytest loading could bypass frozen tests;
- a hanging lock test could prevent negative Evidence from being written;
- rejected Evidence skipped strict case and metric validation;
- artifact paths could escape the repository;
- the PowerShell sdist probe could import the source checkout.

The fix candidate returns ChangeSet serialization to the opened staging
directory descriptor, isolates pytest startup, adds process-tree timeouts and
runtime provenance, hardens artifact paths and rejected Evidence, and isolates
the PowerShell sdist probe. Native Windows confirmation remains open
release-delivery work.

## Candidate 3 Development And Linux Gate

- Development Evidence:
  `demo/results/cross_platform_delivery_candidate_3.json`
- Development Evidence SHA256:
  `1584be87a25356d6189c55a696c35d9b679c4c56c654da261c1caf6d185abb31`
- MemoryForge Commit:
  `79188650e953c6c183b631fd41432e795bde0eaa`
- Runtime: CPython 3.11.15 / Darwin arm64
- Development pass rate: 100.0%
- Deterministic evaluation SHA256:
  `9cafc35adc2f818d9aff8a8e5bbead013ac5e91e77471b25ba5f0d6882a5a27b`
- Full host pytest: 538 passed
- Confirmation status: `not_run`

Linux Evidence:

- Path:
  `demo/results/cross_platform_delivery_candidate_3_linux_gate.json`
- SHA256:
  `efd898c2a3c9eb4807b0610bf5c2979ccc5a86b48fd81735f4b72a6ce6360824`
- Runtime: local Lima 2.2.0 VM, Debian 12 aarch64, CPython 3.11.2
- Pytest: 536 passed, 2 macOS-only cases skipped, 0 failed
- Coverage: 88%
- Ruff, format, strict Mypy, registry, dependency check: passed
- Wheel and sdist clean-room: passed
- Hosted runner: false

Candidate 3 passed the final host artifact gate, then became superseded when
review recheck found that the POSIX Workspace compatibility lock file could
still be replaced, `PYTEST_PLUGINS` could explicitly inject hooks, timeout and
SIGHUP shared one sentinel, and failure diagnostics remained too broad.

Candidate 4 locks the Workspace root namespace plus the compatibility file,
removes explicit pytest plugins, records a distinct timeout flag, and binds a
stable diagnostic classification digest. Native Windows confirmation remains
`not_run`.

## Candidate 4 Development Result

- Development Evidence:
  `demo/results/cross_platform_delivery_candidate_4.json`
- Development Evidence SHA256:
  `198c3654291ba762b591891f894f87c3ebc41764faa8e7e24cfc5c484a4c39cb`
- MemoryForge Commit:
  `2bb3505ce2c5b32ec1ce6e2b4dcd1a12638cc93f`
- Runtime: CPython 3.11.15 / Darwin arm64
- Development pass rate: 100.0%
- Deterministic evaluation SHA256:
  `03879bd4165a21543fadd0ecd237d5976c6b19330bc0bab7d321a84ee1ec92f1`
- Full pytest: 544 passed
- Ruff, format, strict Mypy, registry: passed
- PowerShell 7.6 parser: passed
- Confirmation status: `not_run`

Candidate 4 became superseded after final review found collection
`SyntaxError` was still grouped with interrupts.

## Candidate 5 Development Result

- Development Evidence:
  `demo/results/cross_platform_delivery_candidate_5.json`
- Development Evidence SHA256:
  `4f1cb0fcad903e7e525670d9efde02148b2cdf2a9bef532210997f9ca8102106`
- MemoryForge Commit:
  `cba84d7a6b01d20abfb353e85ae2733210bde98b`
- Runtime: CPython 3.11.15 / Darwin arm64
- Development pass rate: 100.0%
- Deterministic evaluation SHA256:
  `03879bd4165a21543fadd0ecd237d5976c6b19330bc0bab7d321a84ee1ec92f1`
- Confirmation status: `not_run`

Candidate 5 adds exact collection syntax classification. Its final host and
Linux gates are recorded below.

## Candidate 5 Final Local Gates

macOS acceptance:

- Gate Commit:
  `c9af1ed22c5aef64a6b888b494fb27872c7d6ad9`
- Acceptance Evidence:
  `demo/results/cross_platform_delivery_candidate_5_local_gate.json`
- Acceptance Evidence SHA256:
  `20d2ea86f04120b4f27c8ba39ef8e613de2c11acedcb46961284ad56a72f9240`
- Runtime: Darwin arm64, CPython 3.11.15, hosted runner false
- Pytest: 544 passed
- Coverage: 88%
- Registry at gate: 12 suites / 7 experiments / 81 Evidence / 121 QA

Linux acceptance:

- Evidence:
  `demo/results/cross_platform_delivery_candidate_5_linux_gate.json`
- Evidence SHA256:
  `d58dacc1bd4a34f6230e3045db058e4f2542e671d1f017311c02147ee76e3a8f`
- Runtime: local Lima 2.2.0 VM, Debian 12 aarch64, CPython 3.11.2
- Pytest: 542 passed, 2 macOS-only cases skipped, 0 failed
- Coverage: 88%
- Registry at gate: 12 suites / 7 experiments / 81 Evidence / 121 QA

Both gates passed Ruff, format, strict Mypy, dependency checks, Wheel and
sdist clean-room installs, `pip check`, CLI smoke, and Code Wiki release
checks. Both produced byte-identical artifacts:

- Wheel SHA256:
  `71c8644213185531f58b2f215e70a8e3cbf471a50c5207916dd862657bc6b11d`
- sdist SHA256:
  `51046aa8a0c83a64d8e54f5aac8485fb7de1b3ff6ded855b2ec5cb3816fd4137`

PowerShell 7.6 parsed `scripts/check_local.ps1` without syntax errors.
Candidate 5 passed development and both local gates, then became superseded
when cross-group review showed Workspace root replacement could split the
POSIX lock and `scripts/check_local.sh` could import the source checkout during
its sdist probe. Candidate 6 locks the parent namespace and isolates the POSIX
sdist import. Native Windows confirmation remains frozen at `not_run` and is
not claimed.

## Candidate 6 Development Result

- Development Evidence:
  `demo/results/cross_platform_delivery_candidate_6.json`
- Development Evidence SHA256:
  `17bdad1bb9ce7b1cfeff779e4c096d5c981248326493088a2bbee43898fbb706`
- MemoryForge Commit:
  `b3dab407db3f3103456dcbe79d704e4a72c6b656`
- Runtime: CPython 3.11.15 / Darwin arm64
- Development pass rate: 100.0%
- Deterministic evaluation SHA256:
  `03879bd4165a21543fadd0ecd237d5976c6b19330bc0bab7d321a84ee1ec92f1`
- Full pytest: 547 passed
- Confirmation status: `not_run`

Candidate 6 closes the two cross-group findings while preserving the frozen
7-case development and 3-case confirmation inputs.

## Candidate 6 Final Local Gates

macOS acceptance:

- Gate Commit:
  `271a788b51ce1e5a6072362d7dea0a13e1c31fad`
- Acceptance Evidence:
  `demo/results/cross_platform_delivery_candidate_6_local_gate.json`
- Acceptance Evidence SHA256:
  `21e0c9f5752030fc7e3a94bedb45c2868d803c50b93d67866e2af2bd554dd593`
- Runtime: Darwin arm64, CPython 3.11.15, hosted runner false
- Pytest: 547 passed
- Coverage: 88%
- Registry at gate: 12 suites / 7 experiments / 84 Evidence / 121 QA

Linux acceptance:

- Evidence:
  `demo/results/cross_platform_delivery_candidate_6_linux_gate.json`
- Evidence SHA256:
  `ef31991c6efc21cbdeec6ab656937961e0df3bfbcecaf4b92de6f97c8b59575f`
- Runtime: local Lima 2.2.0 VM, Debian 12 aarch64, CPython 3.11.2
- Pytest: 545 passed, 2 macOS-only cases skipped, 0 failed
- Coverage: 88%
- Registry at gate: 12 suites / 7 experiments / 84 Evidence / 121 QA

Both gates passed Ruff, format, strict Mypy, dependency checks, Wheel and
sdist clean-room installs, `pip check`, CLI smoke, and Code Wiki release
checks. Both produced byte-identical artifacts:

- Wheel SHA256:
  `e794f6d0d0e43d4e06cfd9a382003c45a18e0e7b4795ff0cb5925df401c3c3c4`
- sdist SHA256:
  `eca169f278c89e5499e521e9864679ce796b931746e84ec9c7a09a6b1f68edc0`

Candidate 6 is the accepted development result pending final static review.
Native Windows confirmation remains frozen at `not_run` and is not claimed.

## Goal

Remove the package-level Windows failure caused by unconditional `fcntl`
imports. Provide one small exclusive lock module, retain current POSIX
serialization, add the native Windows standard-library backend, and provide
local PowerShell and native smoke entry points.

## Lock Contract

1. `memoryforge.platform_lock` is the only production module importing
   `fcntl` or `msvcrt`;
2. platform imports are mutually exclusive;
3. POSIX uses `fcntl.flock`;
4. Windows uses `msvcrt.locking`;
5. both backends support one non-blocking exclusive attempt;
6. blocking mode polls at a finite positive interval;
7. Windows locks exactly one byte starting at byte zero;
8. Windows restores the descriptor position after lock and unlock calls;
9. contention returns `False` only from the non-blocking API;
10. permanent native failures propagate;
11. unlock is explicit and the caller retains descriptor ownership;
12. lock files remain in place after release;
13. path-level locking accepts only the same opened regular file identity;
14. lock acquisition never truncates or rewrites existing lock-file content.

## Integration Contract

- `Workspace.exclusive_lock` locks the parent namespace, Workspace root, and
  compatibility lock file on POSIX, and delegates to the lock file on Windows;
- `ChangeSetStore` serializes mutations by locking its opened staging
  directory through the platform descriptor API;
- `workspace.py` and `changesets.py` contain no direct platform lock imports;
- existing Workspace security errors stay fail-closed;
- no database, SourceVersion, Citation, ChangeSet, or public payload schema
  changes;
- no confirmation or holdout execution.

## PowerShell Gate

`scripts/check_local.ps1` mirrors the local quality and artifact contract:

- Ruff without cache;
- Ruff format check;
- strict Mypy;
- deterministic registry validation;
- dependency check;
- cross-platform smoke;
- full pytest and coverage;
- isolated Wheel and sdist build;
- Wheel release check;
- sdist clean-room install;
- `pip check`;
- CLI version smoke;
- `SHA256SUMS`.

It does not enable or invoke GitHub Actions.

## Native Smoke

`demo/run_cross_platform_smoke.py` uses the current interpreter and installed
package. It verifies:

- package import;
- CLI version;
- CLI help;
- Workspace initialization;
- Workspace reopen;
- Workspace lock;
- an empty-workspace query returns `unknown`.

Output contains runtime metadata and check states, but no absolute Workspace
path or private content.

## Frozen Development Cases

1. descriptor round trip and contention;
2. blocking wait and release;
3. simulated Windows byte-zero and position contract;
4. stable regular lock file and symbolic-link rejection;
5. single production platform-import boundary and Workspace integration;
6. PowerShell local gate contract;
7. portable CLI/Workspace/empty Demo smoke.

## Frozen Confirmation Cases

Confirmation remains `not_run` during development:

1. native Windows cross-process contention;
2. native Windows descriptor-close release;
3. clean Wheel native Windows CLI/Workspace/empty Demo smoke.

## Development Gates

- 7/7 frozen development cases pass;
- deterministic replay is byte-identical;
- existing Workspace, ChangeSet, CLI, public Demo, and security tests pass;
- Ruff, format, strict Mypy, registry, dependency, pytest, Wheel, sdist,
  `pip check`, and CLI gates pass;
- no direct `fcntl` or `msvcrt` import remains outside `platform_lock.py`;
- no runtime dependency is added;
- confirmation status remains `not_run`;
- GitHub Actions remains disabled.

## Research

Fixed references and design decisions are recorded in:

`docs/research/CROSS_PLATFORM_WORKSPACE_LOCK.md`.

## Known Boundary

This phase does not claim full native Windows support for secure local file
import, SourceManifest publication, ChangeSet directory-descriptor operations,
or static Showcase publication. Those paths use POSIX `dir_fd`,
`O_DIRECTORY`, and `O_NOFOLLOW` security semantics and require a separate,
evidence-backed portability design before such a claim is allowed.

## Forbidden

- adding `filelock`, `portalocker`, `pywin32`, or another runtime dependency;
- existence-only soft locks;
- deleting lock files after release;
- busy-loop acquisition;
- swallowing permanent lock errors;
- weakening current POSIX symlink checks;
- claiming native Windows confirmation from simulated tests;
- running confirmation before release-candidate authorization;
- enabling hosted GitHub Actions.
