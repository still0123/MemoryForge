#!/usr/bin/env python3
"""Run a zero-source CLI and Workspace smoke on the current native platform."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import memoryforge
from memoryforge.storage.workspace import Workspace


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    output = args.output.resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise SystemExit(f"--workspace must be absent or empty: {workspace}")
    if output.is_relative_to(workspace):
        raise SystemExit("--output must remain outside the smoke Workspace")

    checks: dict[str, str] = {"package_import": "passed"}
    if _run_cli("--version").strip() != memoryforge.__version__:
        raise SystemExit("CLI version does not match the imported package")
    checks["cli_version"] = "passed"
    if "memoryforge" not in _run_cli("--help").lower():
        raise SystemExit("CLI help is missing the command identity")
    checks["cli_help"] = "passed"

    _run_cli("init", str(workspace))
    checks["workspace_init"] = "passed"
    opened = Workspace.open(workspace)
    if len(opened.current_commit()) != 40:
        raise SystemExit("Workspace baseline Commit is invalid")
    checks["workspace_open"] = "passed"
    with opened.exclusive_lock():
        pass
    checks["workspace_lock"] = "passed"

    query = _run_cli_json(
        "ask",
        "What evidence exists in this empty Workspace?",
        "--workspace",
        str(workspace),
    )
    if query.get("status") != "unknown" or query.get("citations") != []:
        raise SystemExit("empty Workspace query must return unknown without Citations")
    checks["empty_query_unknown"] = "passed"

    evidence: dict[str, Any] = {
        "schema_version": 1,
        "status": "passed",
        "runtime": {
            "os_name": os.name,
            "system": platform.system(),
            "machine": platform.machine(),
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "memoryforge": memoryforge.__version__,
        },
        "checks": checks,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote cross-platform smoke evidence to {output}")


def _run_cli(*arguments: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "memoryforge", *arguments],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"memoryforge command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def _run_cli_json(*arguments: str) -> dict[str, Any]:
    payload = json.loads(_run_cli(*arguments))
    if not isinstance(payload, dict):
        raise SystemExit("memoryforge command returned a non-object payload")
    return payload


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
