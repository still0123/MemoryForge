#!/usr/bin/env python3
"""Build a deterministic public benchmark summary from the strict registry."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "demo/evaluation/registry.json"
_VALIDATOR_PATH = REPO_ROOT / "demo/validate_benchmark_registry.py"
_SPEC = importlib.util.spec_from_file_location("validate_benchmark_registry", _VALIDATOR_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("could not load benchmark registry validator")
validator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validator)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    output = args.output.resolve()
    if output.is_relative_to(REPO_ROOT):
        raise SystemExit("--output must remain outside the repository")
    commit = _git("rev-parse", "HEAD")
    if _git("status", "--porcelain"):
        raise SystemExit("MemoryForge worktree must be clean")
    summary = build_summary(memoryforge_commit=commit)
    if _git("rev-parse", "HEAD") != commit or _git("status", "--porcelain"):
        raise SystemExit("benchmark summary source changed during generation")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote benchmark summary to {output}")


def build_summary(*, memoryforge_commit: str | None = None) -> dict[str, Any]:
    commit = memoryforge_commit or _git("rev-parse", "HEAD")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry_summary = validator.validate_registry_payload(registry)
    project = tomllib.loads(_git("show", f"{commit}:pyproject.toml"))
    return validator.build_benchmark_summary(
        registry,
        registry_summary,
        package_version=project["project"]["version"],
        memoryforge_commit=commit,
    )


def _git(*args: str) -> str:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
