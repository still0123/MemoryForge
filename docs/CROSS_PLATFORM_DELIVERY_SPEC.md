# Cross-Platform Delivery Specification

## Status

`FROZEN_BASELINE_PENDING`

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

- `Workspace.exclusive_lock` delegates to `exclusive_file_lock`;
- `ChangeSetStore` serializes mutations through
  `.memoryforge/staging/.changesets.lock`;
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
