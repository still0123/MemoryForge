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

```bash
./scripts/check_local.sh
```

The script runs:

```text
Ruff -> format check -> strict Mypy -> pytest + coverage
-> Wheel/sdist build -> Wheel clean-room -> sdist clean-room
-> SHA256SUMS
```

Evidence is written under the ignored `local-evidence/<UTC timestamp>/`
directory. Pass an explicit output path when preparing a named release:

```bash
./scripts/check_local.sh local-evidence/v0.2.2
```

The directory contains:

- Wheel and sdist;
- `release-provenance.json`;
- `SHA256SUMS`.

## Manual Release

After the local gate passes on a clean exact Commit:

```bash
git tag -a v0.2.2 -m "MemoryForge v0.2.2"
git push origin v0.2.2

gh release create v0.2.2 --verify-tag --generate-notes
gh release upload v0.2.2 \
  local-evidence/v0.2.2/dist/memoryforge-*.whl \
  local-evidence/v0.2.2/dist/memoryforge-*.tar.gz \
  local-evidence/v0.2.2/release-provenance.json \
  local-evidence/v0.2.2/SHA256SUMS
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
