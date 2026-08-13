#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
  python_bin="$PYTHON"
elif [[ -x "$repo_root/.venv/bin/python" ]]; then
  python_bin="$repo_root/.venv/bin/python"
else
  python_bin="python3"
fi
cd "$repo_root"

"$python_bin" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name MemoryForge \
  --osx-bundle-identifier com.memoryforge.desktop \
  --paths src \
  --specpath build \
  --exclude-module mypy \
  --exclude-module pytest \
  --exclude-module ruff \
  --collect-all webview \
  --collect-all tree_sitter \
  --collect-all tree_sitter_go \
  --collect-all tree_sitter_python \
  --collect-all tree_sitter_typescript \
  scripts/macos_desktop_app.py

printf 'Built: %s\n' "$repo_root/dist/MemoryForge.app"
