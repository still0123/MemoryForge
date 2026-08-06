#!/usr/bin/env python3
"""Summarize routed-page, selected-fact, and failure-classification evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_RESULTS = {
    "agent-skill-eval": ("doc-wiki-qa.agent-skill-eval", "development"),
    "click-development": ("doc-wiki-qa.click", "development"),
    "click-holdout": ("doc-wiki-qa.click", "holdout"),
    "uvicorn": ("doc-wiki-qa.document-frequency", "development"),
    "typer": ("doc-wiki-qa.document-frequency", "confirmation"),
    "watchfiles": ("doc-wiki-qa.local-fact-morphology", "development"),
    "structlog": ("doc-wiki-qa.local-fact-morphology", "confirmation"),
    "griffe": ("doc-wiki-qa.page-aware-griffe", "confirmation"),
    "learn-development": ("code-wiki-qa.learn-claude-code", "development"),
}
MACRO_METRICS = (
    "answer_accuracy",
    "page_route_recall_at_3",
    "fact_source_recall",
    "fact_selection_accuracy",
    "citation_grounding_accuracy",
    "repository_path_isolation_accuracy",
)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    results = _parse_results(args.result)
    evidence = build_summary(results)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote benchmark taxonomy evidence to {output}")


def build_summary(results: dict[str, Path]) -> dict[str, Any]:
    if set(results) != set(EXPECTED_RESULTS):
        raise ValueError("taxonomy summary requires every expected result exactly once")
    per_suite = []
    failures = []
    classification_counts: Counter[str] = Counter()
    total_cases = 0
    for alias in EXPECTED_RESULTS:
        path = results[alias]
        payload = json.loads(path.read_text(encoding="utf-8"))
        suite_id, split = EXPECTED_RESULTS[alias]
        metrics = payload["memoryforge"]
        cases = payload["cases"]
        total_cases += len(cases)
        for case in cases:
            classification = str(case["memoryforge"]["error_classification"])
            classification_counts[classification] += 1
            if case["memoryforge"]["answer_correct"]:
                continue
            failures.append(
                {
                    "suite_id": suite_id,
                    "split": split,
                    "id": case["id"],
                    "category": case["category"],
                    "error_classification": classification,
                    "page_route_recalled": case["memoryforge"][
                        "page_route_expected_sources_recalled"
                    ],
                    "fact_selection_correct": case["memoryforge"]["fact_selection_correct"],
                }
            )
        per_suite.append(
            {
                "suite_id": suite_id,
                "split": split,
                "suite_name": payload["suite"],
                "case_count": len(cases),
                "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "metrics": {name: metrics[name] for name in MACRO_METRICS},
                "error_classification_counts": metrics["error_classification_counts"],
            }
        )
    macro = {
        name: round(
            sum(float(suite["metrics"][name]) for suite in per_suite) / len(per_suite),
            1,
        )
        for name in MACRO_METRICS
    }
    confirmation = REPO_ROOT / "demo/evaluation/learn_claude_code_qa_confirm_v031.json"
    return {
        "schema_version": 1,
        "evaluation_commit": _git("rev-parse", "HEAD"),
        "evaluation_worktree_dirty": bool(_git("status", "--porcelain")),
        "evaluated_case_count": total_cases,
        "failed_case_count": len(failures),
        "macro_metrics": macro,
        "error_classification_counts": dict(sorted(classification_counts.items())),
        "per_suite": per_suite,
        "failures": failures,
        "unrun_frozen_split": {
            "suite_id": "code-wiki-qa.learn-claude-code",
            "split": "confirmation",
            "path": str(confirmation.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(confirmation.read_bytes()).hexdigest(),
            "status": "not_run",
        },
    }


def _parse_results(values: list[str]) -> dict[str, Path]:
    results: dict[str, Path] = {}
    for value in values:
        alias, separator, path = value.partition("=")
        if not separator or alias not in EXPECTED_RESULTS or alias in results:
            raise ValueError("--result must use one unique registered ALIAS=PATH")
        result_path = Path(path).resolve()
        if not result_path.is_file():
            raise ValueError(f"result does not exist: {result_path}")
        results[alias] = result_path
    return results


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
    parser.add_argument("--result", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
