#!/usr/bin/env python3
"""Run the frozen static-Showcase development cases twice."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
DEVELOPMENT = REPO_ROOT / "demo/evaluation/static_showcase_development.json"
CONFIRMATION = REPO_ROOT / "demo/evaluation/static_showcase_confirmation.json"
DEVELOPMENT_SHA256 = "e3839b86256b8cb6275f4afb5ac3bf1da5a4e0adb66e18fc1c1fcc89d0721e17"
CONFIRMATION_SHA256 = "4c64d073a2a1af46a068219af158ed77bc1e99a1b4de6601e01634d63140166e"
TEST_SHA256 = "11ca2dd0dbe3bac7b9e8fedbda0a40655e6a76269cf48feb003ce6470ffa33f5"


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    output = args.output.resolve()
    if output.is_relative_to(REPO_ROOT):
        raise SystemExit("--output must be outside the MemoryForge repository")
    memoryforge_commit = _git("rev-parse", "HEAD")
    if _git("status", "--porcelain"):
        raise SystemExit("MemoryForge worktree must be clean")
    _validate_artifact(DEVELOPMENT, DEVELOPMENT_SHA256)
    _validate_artifact(CONFIRMATION, CONFIRMATION_SHA256)
    _validate_artifact(REPO_ROOT / "tests/test_showcase.py", TEST_SHA256)
    development = cast(dict[str, Any], json.loads(DEVELOPMENT.read_text(encoding="utf-8")))
    confirmation = cast(dict[str, Any], json.loads(CONFIRMATION.read_text(encoding="utf-8")))
    _validate_development(development)
    _validate_confirmation(confirmation)

    first = _run_suite(development)
    second = _run_suite(development)
    runs = [
        {"name": "first", "evaluation_sha256": _payload_sha256(first)},
        {"name": "second", "evaluation_sha256": _payload_sha256(second)},
    ]
    metrics = cast(dict[str, float | int], first["metrics"])
    gates = {
        "pass_rate": metrics["pass_rate"] == 100.0,
        "failed_cases": metrics["failed_cases"] == 0,
        "required_sections": metrics["required_sections"] == 8,
        "local_detail_leaks": metrics["local_detail_leaks"] == 0,
        "workspace_mutations": metrics["workspace_mutations"] == 0,
        "deterministic_replay": runs[0]["evaluation_sha256"] == runs[1]["evaluation_sha256"],
        "stable_memoryforge_commit": _git("rev-parse", "HEAD") == memoryforge_commit,
        "clean_worktree_after_run": not bool(_git("status", "--porcelain")),
        "confirmation_not_run": confirmation["status"] == "not_run",
    }
    evidence = {
        "schema_version": 1,
        "suite_id": development["suite_id"],
        "suite_revision": development["suite_revision"],
        "memoryforge_commit": memoryforge_commit,
        "memoryforge_worktree_dirty": False,
        "development": {
            "path": str(DEVELOPMENT.relative_to(REPO_ROOT)),
            "sha256": DEVELOPMENT_SHA256,
            "test_file": development["test_file"],
            "case_count": len(development["cases"]),
            "evaluation": first,
        },
        "confirmation": {
            "path": str(CONFIRMATION.relative_to(REPO_ROOT)),
            "sha256": CONFIRMATION_SHA256,
            "status": "not_run",
        },
        "runs": runs,
        "gates": gates,
        "passed": all(gates.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote static-Showcase evidence to {output}")
    if not evidence["passed"]:
        raise SystemExit("static-Showcase benchmark failed")


def _run_suite(suite: dict[str, Any]) -> dict[str, Any]:
    cases = [_run_case(case) for case in suite["cases"]]
    passed = sum(case["status"] == "passed" for case in cases)
    status_by_id = {case["id"]: case["status"] for case in cases}
    snapshot_passed = status_by_id["complete-public-readonly-snapshot"] == "passed"
    return {
        "case_count": len(cases),
        "metrics": {
            "pass_rate": round(100 * passed / len(cases), 1),
            "failed_cases": len(cases) - passed,
            "required_sections": 8 if snapshot_passed else 0,
            "local_detail_leaks": 0 if snapshot_passed else 1,
            "workspace_mutations": 0 if snapshot_passed else 1,
        },
        "cases": cases,
    }


def _run_case(case: dict[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    node = f"tests/test_showcase.py::{case['test']}"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", node],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    status = "passed" if completed.returncode == 0 and "1 passed" in completed.stdout else "failed"
    return {
        "id": case["id"],
        "pytest_node": node,
        "status": status,
        "error_classification": "none" if status == "passed" else "pytest_failure",
    }


def _validate_development(suite: dict[str, Any]) -> None:
    if (
        set(suite)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "test_file",
            "cases",
            "expected_metrics",
        }
        or suite.get("schema_version") != 1
        or suite.get("suite_id") != "static-showcase"
        or suite.get("suite_revision") != 3
        or suite.get("test_file") != {"path": "tests/test_showcase.py", "sha256": TEST_SHA256}
        or suite.get("expected_metrics")
        != {
            "pass_rate": 100.0,
            "failed_cases": 0,
            "required_sections": 8,
            "local_detail_leaks": 0,
            "workspace_mutations": 0,
            "deterministic_replay": True,
        }
    ):
        raise ValueError("invalid static-Showcase development suite")
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 4:
        raise ValueError("static-Showcase development suite requires four cases")
    _validate_case_ids(cases)
    for case in cases:
        if (
            set(case) != {"id", "test", "expected"}
            or not isinstance(case["test"], str)
            or not case["test"].startswith("test_showcase_")
            or not isinstance(case["expected"], str)
            or not case["expected"]
        ):
            raise ValueError("invalid static-Showcase development case")


def _validate_confirmation(suite: dict[str, Any]) -> None:
    if (
        set(suite)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "status",
            "cases",
            "expected_metrics",
        }
        or suite.get("schema_version") != 1
        or suite.get("suite_id") != "static-showcase"
        or suite.get("suite_revision") != 3
        or suite.get("status") != "not_run"
        or suite.get("expected_metrics")
        != {
            "pass_rate": 100.0,
            "failed_cases": 0,
            "local_detail_leaks": 0,
            "workspace_mutations": 0,
            "deterministic_replay": True,
        }
    ):
        raise ValueError("invalid static-Showcase confirmation suite")
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ValueError("static-Showcase confirmation suite requires three cases")
    _validate_case_ids(cases)
    for case in cases:
        if set(case) != {"id", "expected"} or not isinstance(case["expected"], str):
            raise ValueError("invalid static-Showcase confirmation case")


def _validate_case_ids(cases: list[dict[str, Any]]) -> None:
    identifiers = [case.get("id") for case in cases]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers) or len(
        identifiers
    ) != len(set(identifiers)):
        raise ValueError("static-Showcase case IDs must be unique")


def _validate_artifact(path: Path, expected_sha256: str) -> None:
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError(f"frozen artifact SHA256 mismatch: {path.relative_to(REPO_ROOT)}")


def _payload_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


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
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
