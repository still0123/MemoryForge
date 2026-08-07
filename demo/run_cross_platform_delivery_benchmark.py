#!/usr/bin/env python3
"""Run the frozen cross-platform delivery development cases twice."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
DEVELOPMENT = REPO_ROOT / "demo/evaluation/cross_platform_delivery_development.json"
CONFIRMATION = REPO_ROOT / "demo/evaluation/cross_platform_delivery_confirmation.json"
TEST_FILE = REPO_ROOT / "tests/test_cross_platform_delivery.py"
DEVELOPMENT_SHA256 = "0082594121022f6f97b6eae3d4106819794c0b988f531a02e51b9ec2194fb6ff"
CONFIRMATION_SHA256 = "200074acfb79979bf4663b97ae25e9dd62fa0f94359b8aa0d39c4250256660fc"
TEST_SHA256 = "08601b7effba8271ac318a58a5b0906b934bdeaa4564edcd0ec5738e61618ac3"
CASE_TIMEOUT_SECONDS = 30
_PYTEST_FAILURE_CLASSIFICATIONS = {
    1: "pytest_failure",
    2: "pytest_interrupted",
    3: "pytest_internal_error",
    4: "pytest_usage_error",
    5: "pytest_no_tests",
}


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    output = args.output.resolve()
    if output.is_relative_to(REPO_ROOT):
        raise SystemExit("--output must be outside the MemoryForge repository")
    memoryforge_commit = _git("rev-parse", "HEAD")
    if _git("status", "--porcelain"):
        raise SystemExit("MemoryForge worktree must be clean")
    runtime = _require_runtime()
    _validate_artifact(DEVELOPMENT, DEVELOPMENT_SHA256)
    _validate_artifact(CONFIRMATION, CONFIRMATION_SHA256)
    _validate_artifact(TEST_FILE, TEST_SHA256)
    development = cast(dict[str, Any], json.loads(DEVELOPMENT.read_text(encoding="utf-8")))
    confirmation = cast(dict[str, Any], json.loads(CONFIRMATION.read_text(encoding="utf-8")))
    _validate_development(development)
    _validate_confirmation(confirmation)

    first = _run_suite(development)
    second = _run_suite(development)
    runs = (
        {"name": "first", "evaluation_sha256": _payload_sha256(first)},
        {"name": "second", "evaluation_sha256": _payload_sha256(second)},
    )
    metrics = cast(dict[str, object], first["metrics"])
    gates = {
        "pass_rate": metrics["pass_rate"] == 100.0,
        "failed_cases": metrics["failed_cases"] == 0,
        "direct_platform_imports": metrics["direct_platform_imports"] == 0,
        "windows_lock_offset": metrics["windows_lock_offset"] == 0,
        "windows_lock_bytes": metrics["windows_lock_bytes"] == 1,
        "local_smoke": metrics["local_smoke"] == "passed",
        "deterministic_replay": runs[0]["evaluation_sha256"] == runs[1]["evaluation_sha256"],
        "stable_memoryforge_commit": _git("rev-parse", "HEAD") == memoryforge_commit,
        "clean_worktree_after_run": not bool(_git("status", "--porcelain")),
        "confirmation_not_run": _confirmation_is_isolated(confirmation),
    }
    evidence = {
        "schema_version": 1,
        "suite_id": development["suite_id"],
        "suite_revision": development["suite_revision"],
        "memoryforge_commit": memoryforge_commit,
        "memoryforge_worktree_dirty": False,
        "runtime": runtime,
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
        "runs": list(runs),
        "gates": gates,
        "passed": all(gates.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote cross-platform delivery evidence to {output}")
    if not evidence["passed"]:
        raise SystemExit("cross-platform delivery benchmark failed")


def _run_suite(suite: dict[str, Any]) -> dict[str, Any]:
    cases = [_run_case(case) for case in suite["cases"]]
    passed = sum(case["status"] == "passed" for case in cases)
    status_by_id = {case["id"]: case["status"] for case in cases}
    windows_contract = status_by_id["windows-byte-zero-contract"] == "passed"
    boundary = status_by_id["single-platform-boundary"] == "passed"
    smoke = status_by_id["portable-empty-demo"] == "passed"
    direct_platform_imports = _direct_platform_import_count()
    return {
        "case_count": len(cases),
        "metrics": {
            "pass_rate": round(100 * passed / len(cases), 1),
            "failed_cases": len(cases) - passed,
            "direct_platform_imports": direct_platform_imports
            if boundary
            else max(1, direct_platform_imports),
            "windows_lock_offset": 0 if windows_contract else -1,
            "windows_lock_bytes": 1 if windows_contract else 0,
            "local_smoke": "passed" if smoke else "failed",
        },
        "cases": cases,
    }


def _run_case(case: dict[str, str]) -> dict[str, object]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    node = f"tests/test_cross_platform_delivery.py::{case['test']}"
    with tempfile.TemporaryDirectory(prefix="memoryforge-frozen-pytest-") as isolated:
        pytest_arguments = [
            "-q",
            "--noconftest",
            "-c",
            os.devnull,
            "--rootdir",
            isolated,
            f"{TEST_FILE}::{case['test']}",
        ]
        bootstrap = (
            "import os,sys,pytest;"
            f"os.environ['PYTHONPATH']={str(REPO_ROOT / 'src')!r};"
            f"sys.path.insert(0,{str(REPO_ROOT / 'src')!r});"
            f"raise SystemExit(pytest.main({pytest_arguments!r}))"
        )
        return_code, output, timed_out = _run_isolated_pytest(
            [sys.executable, "-I", "-c", bootstrap],
            cwd=Path(isolated),
            environment=environment,
        )
    status = "passed" if return_code == 0 and "1 passed" in output else "failed"
    classification = _classify_pytest_result(
        return_code,
        output,
        status,
        timed_out=timed_out,
    )
    diagnostic_sha256 = hashlib.sha256(
        f"{return_code}:{classification}:{timed_out}".encode("ascii")
    ).hexdigest()
    return {
        "id": case["id"],
        "pytest_node": node,
        "status": status,
        "return_code": return_code,
        "timed_out": timed_out,
        "error_classification": classification,
        "diagnostic_sha256": diagnostic_sha256,
    }


def _run_isolated_pytest(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> tuple[int, str, bool]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=os.name != "nt",
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        ),
    )
    try:
        output, _ = process.communicate(timeout=CASE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        process.communicate()
        return -1, "", True
    return process.returncode, output, False


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            check=False,
            capture_output=True,
        )
    else:
        os.killpg(process.pid, signal.SIGKILL)


def _classify_pytest_result(
    return_code: int,
    output: str,
    status: str,
    *,
    timed_out: bool,
) -> str:
    if status == "passed":
        return "none"
    if timed_out:
        return "pytest_timeout"
    if "ModuleNotFoundError" in output:
        return "pytest_collection_module_not_found"
    if "ImportError while importing test module" in output:
        return "pytest_collection_import_error"
    if "SyntaxError" in output and "ERROR collecting" in output:
        return "pytest_collection_syntax_error"
    if "ERROR collecting" in output:
        return "pytest_collection_error"
    if "AssertionError" in output or " failed" in output.lower():
        return "pytest_assertion_failure"
    return _PYTEST_FAILURE_CLASSIFICATIONS.get(return_code, "pytest_process_error")


def _direct_platform_import_count() -> int:
    count = 0
    for path in sorted((REPO_ROOT / "src/memoryforge").glob("*.py")):
        if path.name == "platform_lock.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                count += sum(alias.name in {"fcntl", "msvcrt"} for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module in {"fcntl", "msvcrt"}:
                count += 1
    return count


def _require_runtime() -> dict[str, str]:
    if platform.python_implementation() != "CPython" or sys.version_info[:2] != (3, 11):
        raise SystemExit("cross-platform delivery development requires CPython 3.11")
    return {
        "implementation": platform.python_implementation(),
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
    }


def _confirmation_is_isolated(confirmation: dict[str, Any]) -> bool:
    result = REPO_ROOT / "demo/results/cross_platform_delivery_confirmation.json"
    workflows = REPO_ROOT / ".github/workflows"
    hosted_workflows = (
        [*workflows.glob("*.yml"), *workflows.glob("*.yaml")] if workflows.is_dir() else []
    )
    return confirmation.get("status") == "not_run" and not result.exists() and not hosted_workflows


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
        or suite.get("suite_id") != "cross-platform-delivery"
        or suite.get("suite_revision") != 1
        or suite.get("test_file")
        != {"path": "tests/test_cross_platform_delivery.py", "sha256": TEST_SHA256}
        or suite.get("expected_metrics")
        != {
            "pass_rate": 100.0,
            "failed_cases": 0,
            "direct_platform_imports": 0,
            "windows_lock_offset": 0,
            "windows_lock_bytes": 1,
            "local_smoke": "passed",
            "confirmation_not_run": True,
        }
    ):
        raise ValueError("invalid cross-platform delivery development suite")
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 7:
        raise ValueError("cross-platform delivery development suite requires seven cases")
    _validate_case_ids(cases)
    for case in cases:
        if (
            set(case) != {"id", "test", "expected"}
            or not isinstance(case["test"], str)
            or not case["test"].startswith("test_")
            or not isinstance(case["expected"], str)
            or not case["expected"]
        ):
            raise ValueError("invalid cross-platform delivery development case")


def _validate_confirmation(suite: dict[str, Any]) -> None:
    if (
        set(suite)
        != {
            "schema_version",
            "suite_id",
            "suite_revision",
            "status",
            "required_runtime",
            "cases",
            "expected_metrics",
        }
        or suite.get("schema_version") != 1
        or suite.get("suite_id") != "cross-platform-delivery"
        or suite.get("suite_revision") != 1
        or suite.get("status") != "not_run"
        or suite.get("required_runtime")
        != {"os_name": "nt", "implementation": "CPython", "python": "3.11"}
        or suite.get("expected_metrics")
        != {
            "pass_rate": 100.0,
            "failed_cases": 0,
            "native_windows_smoke": "passed",
            "wheel_import_from_fresh_venv": True,
            "github_actions_enabled": False,
        }
    ):
        raise ValueError("invalid cross-platform delivery confirmation suite")
    cases = suite.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ValueError("cross-platform delivery confirmation suite requires three cases")
    _validate_case_ids(cases)
    for case in cases:
        if set(case) != {"id", "expected"} or not isinstance(case["expected"], str):
            raise ValueError("invalid cross-platform delivery confirmation case")


def _validate_case_ids(cases: list[dict[str, Any]]) -> None:
    identifiers = [case.get("id") for case in cases]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers) or len(
        identifiers
    ) != len(set(identifiers)):
        raise ValueError("cross-platform delivery case IDs must be unique")


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
