# v0.3.0 Release Delivery Research

## Scope

This note records fixed-Commit references for the MemoryForge v0.3.0
release-candidate and local artifact-delivery phase. It covers package metadata,
isolated local builds, artifact validation, and provenance. It does not justify
running frozen confirmation or holdout inputs before the release candidate is
approved.

## PyPA SampleProject

- Repository: `pypa/sampleproject`
- Commit: `621e4974ca25ce531773def586ba3ed8e736b3fc`
- License: MIT
- Read:
  - `pyproject.toml`
  - `.github/workflows/release.yml`
  - `.github/workflows/test.yml`
  - `README.md`
  - `LICENSE.txt`

Borrowed design:

- one PEP 440 package version in project metadata;
- explicit package name, Python requirement, license, entry point, and project
  URLs;
- build Wheel and sdist through the standard `python -m build` frontend;
- treat README as package-facing documentation and keep release wording aligned
  with package metadata.

Not adopted:

- GitHub-hosted test or release jobs;
- PyPI trusted publishing and OIDC permissions;
- `skip-existing`, because a release must fail on an existing or mismatched
  asset instead of silently accepting it;
- its broad Python/platform matrix, which would require hosted runners not
  available under this Goal.

## PyPA Build

- Repository: `pypa/build`
- Commit: `1b8f3fa736badce75895bc19f4a8650b679bbffa`
- License: MIT
- Read:
  - `pyproject.toml`
  - `src/build/__main__.py`
  - `src/build/_builder.py`
  - `tests/test_main.py`
  - `LICENSE`

Borrowed design:

- resolve build requirements through the declared PEP 517 backend;
- use isolated build environments and explicit output directories;
- validate source archives before using them to build a Wheel;
- keep subprocess failure and dependency failure explicit;
- test CLI argument routing and the sdist-to-Wheel path separately.

Not adopted:

- vendoring or wrapping the `build` frontend;
- adding `build` as a runtime dependency;
- supporting every installer/backend combination;
- network publication inside the release checker.

## MemoryForge Decision

MemoryForge keeps its existing Hatchling backend and pinned development
constraints. The release phase adds only repository-local orchestration:

1. freeze confirmation, holdout, and release manifests before changing package
   metadata;
2. set `pyproject.toml` and `memoryforge.__version__` to `0.3.0`;
3. build twice in isolated local directories from one clean Commit;
4. require byte-identical Wheel and sdist artifacts;
5. verify package metadata, archive members, clean-room imports, CLI version,
   dependency health, and public zero-key workflows;
6. write SHA256SUMS, provenance, and benchmark summary without private paths;
7. upload only after native Windows confirmation and the one allowed holdout
   run pass.

No reference code is copied.

## Candidate 10 Hardening

Candidate 10 keeps the same two fixed references and narrows the release
contract:

- Summary schema 3 retains each experiment's repository, development split,
  expected metrics, and complete Evidence identities.
- `accepted_development` now requires an accepted final-review sidecar;
  local-gate success and review failure remain distinct states.
- Build subprocesses use a clean Python/coverage environment and the official
  PyPI simple index instead of host index configuration.
- Retained Workspace, Code Wiki, package metadata, privacy, and review-scope
  Evidence is replayed from raw structured data rather than trusted pass
  strings.

Not adopted:

- a vendored package index or committed wheelhouse;
- a new release framework;
- changing any frozen development, confirmation, or holdout case.

## Expected Improvement

- package/CLI/release version consistency: 100%;
- clean build reproducibility: identical Wheel and sdist SHA256 across two
  local builds;
- public artifact traceability: every uploaded asset appears in SHA256SUMS and
  provenance;
- hosted runner use: 0;
- confirmation and holdout executions before release-candidate approval: 0.
