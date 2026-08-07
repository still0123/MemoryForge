#!/usr/bin/env python3
"""Run the frozen recursive folder-import development cases twice."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
DEVELOPMENT = REPO_ROOT / "demo/evaluation/folder_import_development.json"
CONFIRMATION = REPO_ROOT / "demo/evaluation/folder_import_confirmation.json"
DEVELOPMENT_SHA256 = "d818b64079f9fb4136aa53180f7f0f6f49a433b7f658ee5f083f5da6362abbec"
CONFIRMATION_SHA256 = "099d8a49892e9f0b2e6203891bbeeb6ccac53042be2e86fea110180c426cf45d"
TEST_SHA256 = "b66dc3c314ff7933fc3916b24b8b276acba5b4f143f67fba12ab5534e9dd2fd0"


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
    _validate_artifact(REPO_ROOT / "tests/test_folder_import.py", TEST_SHA256)
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
            "test_sha256": TEST_SHA256,
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
    print(f"Wrote folder-import evidence to {output}")
    if not evidence["passed"]:
        raise SystemExit("folder-import benchmark failed")


def _run_suite(suite: dict[str, Any]) -> dict[str, Any]:
    cases = [_run_case(case) for case in suite["cases"]]
    passed = sum(case["status"] == "passed" for case in cases)
    return {
        "case_count": len(cases),
        "metrics": {
            "pass_rate": round(100 * passed / len(cases), 1),
            "failed_cases": len(cases) - passed,
        },
        "cases": cases,
    }


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


def _validate_development(suite: dict[str, Any]) -> None:
    if (
        set(suite)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "split",
            "test_file",
            "test_sha256",
            "cases",
            "expected_metrics",
        }
        or suite.get("schema_version") != 1
        or suite.get("suite_id") != "folder-import-lifecycle"
        or suite.get("suite_revision") != 1
        or suite.get("split") != "development"
        or suite.get("test_file") != "tests/test_folder_import.py"
        or suite.get("test_sha256") != TEST_SHA256
        or suite.get("expected_metrics")
        != {
            "pass_rate": 100.0,
            "failed_cases": 0,
            "deterministic_replay": True,
        }
    ):
        raise ValueError("invalid folder-import development suite")
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 5:
        raise ValueError("folder-import development suite requires five cases")
    _validate_case_ids(cases)
    for case in cases:
        if (
            set(case) != {"id", "category", "pytest_node", "expected_status"}
            or not isinstance(case["category"], str)
            or not case["category"]
            or not isinstance(case["pytest_node"], str)
            or not case["pytest_node"].startswith("tests/test_folder_import.py::test_")
            or case["expected_status"] != "passed"
        ):
            raise ValueError("invalid folder-import development case")


def _validate_confirmation(suite: dict[str, Any]) -> None:
    if (
        set(suite)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "split",
            "status",
            "cases",
        }
        or suite.get("schema_version") != 1
        or suite.get("suite_id") != "folder-import-lifecycle"
        or suite.get("suite_revision") != 1
        or suite.get("split") != "confirmation"
        or suite.get("status") != "not_run"
    ):
        raise ValueError("invalid folder-import confirmation suite")
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ValueError("folder-import confirmation suite requires three cases")
    _validate_case_ids(cases)
    for case in cases:
        if set(case) != {"id", "category", "scenario", "expected"} or any(
            not isinstance(case[field], str) or not case[field]
            for field in ("category", "scenario", "expected")
        ):
            raise ValueError("invalid folder-import confirmation case")


def _validate_case_ids(cases: list[dict[str, Any]]) -> None:
    ids = [case.get("id") for case in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in ids) or len(ids) != len(
        set(ids)
    ):
        raise ValueError("folder-import case IDs must be unique")


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
