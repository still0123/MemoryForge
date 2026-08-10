# Local Free Tooling

MemoryForge does not use GitHub Actions. Repository Actions permissions and
the former CI/Release workflows are disabled. Quality checks and release
artifacts are produced locally with open-source Python tools.

## Bootstrap

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c constraints/dev.txt -e '.[dev]'
```

## Full Local Gate

POSIX:

```bash
./scripts/check_local.sh
```

Windows PowerShell:

```powershell
.\scripts\check_local.ps1
```

The script runs:

```text
Ruff -> format check -> strict Mypy -> registry -> dependency check
-> platform smoke -> pytest + coverage
-> Wheel/sdist build -> Wheel clean-room -> sdist clean-room
-> SHA256SUMS
```

Evidence is written under the ignored `local-evidence/<UTC timestamp>/`
directory. Pass an explicit output path when preparing a named release:

```bash
./scripts/check_local.sh local-evidence/v0.3.0

python scripts/build_release.py \
  --output local-evidence/v0.3.0-release
```

The directory contains:

- Wheel and sdist;
- `platform-smoke.json` when using the PowerShell gate;
- `release-provenance.json`;
- `SHA256SUMS`.

The PowerShell entry point is not evidence of native Windows confirmation by
itself. Native Windows confirmation remains a separate frozen release-candidate
gate and must record its exact runtime and Wheel SHA256.

## Manual Release

After the local gate passes on a clean exact Commit:

```bash
git tag -a v0.3.0 -m "MemoryForge v0.3.0"
git push origin v0.3.0

gh release create v0.3.0 --verify-tag --generate-notes
gh release upload v0.3.0 \
  local-evidence/v0.3.0-release/memoryforge-*.whl \
  local-evidence/v0.3.0-release/memoryforge-*.tar.gz \
  local-evidence/v0.3.0-release/release-provenance.json \
  local-evidence/v0.3.0-release/benchmark-summary.json \
  local-evidence/v0.3.0-release/workspace-drill.json \
  local-evidence/v0.3.0-release/reproducibility-* \
  local-evidence/v0.3.0-release/SHA256SUMS
```

GitHub CLI and GitHub Releases are used manually; no hosted runner or paid
automation is required. Downloaded assets must be checked against
`SHA256SUMS` before the release is considered complete.

## Pull Requests

Because hosted checks are disabled, each PR must include:

- exact tested Commit;
- local test count;
- Ruff, format, Mypy, and dependency-check results;
- local Evidence path and `SHA256SUMS` digest when artifacts are relevant.

Never claim a remote CI result for changes validated only locally.
