#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python="$PYTHON_BIN"
elif [[ -x "$root/.venv/bin/python" ]]; then
  python="$root/.venv/bin/python"
else
  python="python3"
fi

output="${1:-$root/local-evidence/$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ -e "$output" ]]; then
  echo "output already exists: $output" >&2
  exit 1
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/memoryforge-local-check.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT
mkdir -p "$output/dist"

"$python" -m ruff check --no-cache .
"$python" -m ruff format --check .
"$python" -m mypy
"$python" demo/validate_benchmark_registry.py
if "$python" -m pip --version >/dev/null 2>&1; then
  "$python" -m pip check
elif command -v uv >/dev/null 2>&1; then
  uv pip check --python "$python"
else
  echo "dependency check requires pip or uv" >&2
  exit 1
fi
"$python" -m pytest -W error::ResourceWarning \
  -W error::pytest.PytestUnraisableExceptionWarning \
  --cov=memoryforge --cov-report=term-missing

"$python" -m venv "$workdir/build"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$workdir/build/bin/python" \
    -c "$root/constraints/dev.txt" build hatchling
else
  "$workdir/build/bin/python" -m pip install \
    -c "$root/constraints/dev.txt" build hatchling
fi
"$workdir/build/bin/python" -m build \
  --wheel --sdist --no-isolation --outdir "$output/dist"
"$workdir/build/bin/python" - "$output"/dist/memoryforge-*.tar.gz <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1]) as archive:
    forbidden = [
        name
        for name in archive.getnames()
        if "/demo/results/artifacts/" in f"/{name}"
        or name.endswith((".whl", ".tar.gz"))
    ]
if forbidden:
    raise SystemExit(f"sdist contains retained or nested artifacts: {forbidden[:3]}")
PY

PIP_CONSTRAINT="$root/constraints/dev.txt" "$python" demo/run_release_check.py \
  --wheel "$output"/dist/memoryforge-*.whl \
  --workdir "$workdir/wheel" \
  --output "$output/release-provenance.json"

"$python" -m venv "$workdir/sdist"
"$workdir/sdist/bin/python" -m pip install \
  -c "$root/constraints/dev.txt" hatchling
"$workdir/sdist/bin/python" -m pip install \
  -c "$root/constraints/dev.txt" \
  --no-build-isolation "$output"/dist/memoryforge-*.tar.gz
(
  cd "$workdir"
  env -u PYTHONPATH PYTHONNOUSERSITE=1 \
    "$workdir/sdist/bin/python" -I -m pip check
  env -u PYTHONPATH PYTHONNOUSERSITE=1 \
    "$workdir/sdist/bin/python" -I - "$workdir/sdist" <<'PY'
import importlib.metadata
import memoryforge
import sys
from pathlib import Path

environment = Path(sys.argv[1]).resolve()
import_path = Path(memoryforge.__file__).resolve()
if not import_path.is_relative_to(environment):
    raise SystemExit(f"sdist import escaped clean environment: {import_path}")
print(importlib.metadata.version("memoryforge"))
PY
  env -u PYTHONPATH PYTHONNOUSERSITE=1 \
    "$workdir/sdist/bin/python" -I -m memoryforge --version
)

"$python" - "$output" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
artifacts = sorted((root / "dist").iterdir()) + [root / "release-provenance.json"]
lines = [
    f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}"
    for path in artifacts
]
(root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
for line in lines:
    digest, relative = line.split("  ", 1)
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest
PY

echo "Local checks passed. Evidence: $output"
