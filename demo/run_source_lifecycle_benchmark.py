#!/usr/bin/env python3
"""Run deterministic source lifecycle tests and write auditable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUITE = REPO_ROOT / "demo/evaluation/source_lifecycle_suite.json"


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    suite_path = args.suite.resolve()
    output = args.output.resolve()
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = _validate_suite(suite)
    results = [_run_case(case) for case in cases]
    passed = sum(result["status"] == "passed" for result in results)
    evidence = {
        "schema_version": 1,
        "suite_id": suite["suite_id"],
        "suite_revision": suite["suite_revision"],
        "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        "memoryforge_commit": _git("rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": bool(_git("status", "--porcelain")),
        "case_count": len(results),
        "metrics": {
            "passed_cases": passed,
            "pass_rate": round(100 * passed / len(results), 1),
        },
        "cases": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote source lifecycle evidence to {output}")
    if passed != len(results):
        raise SystemExit("source lifecycle benchmark failed")


def _validate_suite(suite: dict[str, Any]) -> list[dict[str, str]]:
    if suite.get("suite_id") != "source-lifecycle.local-git":
        raise ValueError("unexpected source lifecycle suite_id")
    if suite.get("suite_revision") != 1:
        raise ValueError("unexpected source lifecycle suite_revision")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("source lifecycle suite requires cases")
    if len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("source lifecycle case IDs must be unique")
    for case in cases:
        node = case.get("pytest_node")
        if not isinstance(node, str) or "::" not in node or not node.startswith("tests/test_"):
            raise ValueError("source lifecycle cases require local pytest nodes")
    return cases


def _run_case(case: dict[str, str]) -> dict[str, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", case["pytest_node"]],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    status = "passed" if completed.returncode == 0 else "failed"
    return {
        "id": case["id"],
        "category": case["category"],
        "pytest_node": case["pytest_node"],
        "status": status,
        "error_classification": "none" if status == "passed" else "pytest_failure",
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
