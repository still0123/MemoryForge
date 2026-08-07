#!/usr/bin/env python3
"""Build a deterministic public benchmark summary from the strict registry."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tomllib
from collections import defaultdict
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
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry_summary = validator.validate_registry()
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    suites = [_suite_summary(suite) for suite in registry["suites"]]
    experiments = [_experiment_summary(experiment) for experiment in registry["experiments"]]
    return {
        "schema_version": 1,
        "package_version": project["project"]["version"],
        "memoryforge_commit": memoryforge_commit or _git("rev-parse", "HEAD"),
        "registry": registry_summary,
        "macro": _macro_metrics(suites),
        "suites": suites,
        "experiments": experiments,
        "negative_results": _negative_results(registry),
    }


def _suite_summary(suite: dict[str, Any]) -> dict[str, Any]:
    return {
        "suite_id": suite["suite_id"],
        "suite_revision": suite["suite_revision"],
        "suite_type": suite["suite_type"],
        "repositories": [
            {
                "repository": repository["repository"],
                "commit": repository["commit"],
                "license": repository["license"],
            }
            for repository in suite["repositories"]
        ],
        "splits": suite["splits"],
        "metrics": suite["expected_metrics"],
        "evidence": [
            {
                "split": evidence["split"],
                "evidence_revision": evidence["evidence_revision"],
                "path": evidence["path"],
                "sha256": evidence["sha256"],
                "memoryforge_commit": evidence["memoryforge_commit"],
            }
            for evidence in suite["evidence"]
        ],
    }


def _experiment_summary(experiment: dict[str, Any]) -> dict[str, Any]:
    accepted = [
        evidence
        for evidence in experiment["evidence"]
        if evidence["status"] == "accepted_development"
    ]
    return {
        "suite_id": experiment["suite_id"],
        "suite_revision": experiment["suite_revision"],
        "confirmation": experiment["splits"]["confirmation"],
        "holdout": experiment["splits"]["holdout"],
        "accepted_evidence": [
            {
                "path": evidence["path"],
                "sha256": evidence["sha256"],
                "memoryforge_commit": evidence["memoryforge_commit"],
            }
            for evidence in accepted
        ],
    }


def _macro_metrics(suites: list[dict[str, Any]]) -> dict[str, float]:
    values: defaultdict[str, list[float]] = defaultdict(list)
    for suite in suites:
        if suite["suite_type"] not in {"document_wiki_qa", "code_wiki_qa"}:
            continue
        for metrics in suite["metrics"].values():
            if not isinstance(metrics, dict):
                continue
            for name, value in metrics.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[name].append(float(value))
    return {
        name: round(sum(metric_values) / len(metric_values), 1)
        for name, metric_values in sorted(values.items())
        if metric_values
    }


def _negative_results(registry: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for experiment in registry["experiments"]:
        for evidence in experiment["evidence"]:
            if evidence["status"] in {"rejected", "development_passed_regression_failed"}:
                results.append(
                    {
                        "suite_id": experiment["suite_id"],
                        "status": evidence["status"],
                        "path": evidence["path"],
                        "sha256": evidence["sha256"],
                    }
                )
            regression = evidence.get("regression_evidence")
            if isinstance(regression, dict):
                results.append(
                    {
                        "suite_id": experiment["suite_id"],
                        "status": "regression_rejected",
                        "path": regression["path"],
                        "sha256": regression["sha256"],
                    }
                )
    for suite in registry["suites"]:
        for split, metrics in suite["expected_metrics"].items():
            if (
                isinstance(metrics, dict)
                and isinstance(metrics.get("answer_accuracy"), (int, float))
                and metrics["answer_accuracy"] < 100
            ):
                results.append(
                    {
                        "suite_id": suite["suite_id"],
                        "status": "retained_metric_gap",
                        "split": split,
                        "answer_accuracy": metrics["answer_accuracy"],
                    }
                )
    return sorted(
        results,
        key=lambda item: (item["suite_id"], item["status"], item.get("split", "")),
    )


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
