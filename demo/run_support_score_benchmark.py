#!/usr/bin/env python3
"""Run frozen support-score development and structural gates twice."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from memoryforge.evaluation import run_evaluation
from memoryforge.query import (
    _has_support_condition,
    _has_support_negation,
    _support_identifiers,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "demo/evaluation/support_score_sources.json"
_EXTERNAL_SCRIPT = REPO_ROOT / "demo/run_external_code_wiki_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("run_external_code_wiki_benchmark", _EXTERNAL_SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("could not load external Code Wiki benchmark")
external_benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(external_benchmark)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    memoryforge_commit = _git("rev-parse", "HEAD")
    if _git("status", "--porcelain"):
        raise SystemExit("MemoryForge worktree must be clean")
    manifest = cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))
    for key in ("development", "baseline_evidence", "confirmation"):
        _validate_artifact(cast(dict[str, Any], manifest[key]))
    repository = cast(dict[str, Any], manifest["repositories"][0])
    source = args.source_repo.resolve()
    source_commit = _git_at(source, "rev-parse", "HEAD")
    if _git_at(source, "status", "--porcelain"):
        raise SystemExit("source worktree must be clean")
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    _require_external_output(output, REPO_ROOT, source)
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")

    sources = {str(repository["name"]): source}
    development_path = REPO_ROOT / str(manifest["development"]["path"])
    runs = []
    evaluations = []
    for name in ("first", "second"):
        run_root = workdir / name
        structural = external_benchmark.build_evidence(run_root, sources, MANIFEST)
        evaluation = run_evaluation(
            run_root / str(repository["name"]) / "workspace",
            development_path,
        )
        evaluations.append(evaluation)
        runs.append(
            {
                "name": name,
                "structural_passed": structural["summary"]["passed"],
                "structural_sha256": _payload_sha256(structural),
                "evaluation_sha256": _payload_sha256(evaluation),
                "metrics": evaluation["memoryforge"],
            }
        )

    first = cast(dict[str, Any], evaluations[0])
    metrics = cast(dict[str, Any], first["memoryforge"])
    cases = cast(list[dict[str, Any]], first["cases"])
    failures = [
        {
            "id": case["id"],
            "category": case["category"],
            "error_classification": case["memoryforge"]["error_classification"],
            "support": case["memoryforge"]["support"],
        }
        for case in cases
        if case["memoryforge"]["error_classification"] != "none"
    ]
    unsupported = next(case for case in cases if case["id"] == "dev-unknown-vector-database")
    threshold = float(manifest["support_threshold"])
    gates = {
        "structural_benchmark": all(run["structural_passed"] for run in runs),
        "answer_accuracy": metrics["answer_accuracy"] == 100.0,
        "selective_accuracy": metrics["selective_accuracy"] == 100.0,
        "coverage": metrics["coverage"] == 90.0,
        "risk": metrics["risk"] == 0.0,
        "abstention_accuracy": metrics["abstention_accuracy"] == 100.0,
        "page_route_recall_at_3": metrics["page_route_recall_at_3"] == 100.0,
        "source_recall_at_3": metrics["source_recall_at_3"] == 100.0,
        "fact_selection_accuracy": metrics["fact_selection_accuracy"] == 100.0,
        "citation_grounding_accuracy": metrics["citation_grounding_accuracy"] == 100.0,
        "multi_source_coverage": metrics["multi_source_coverage"] == 100.0,
        "repository_path_isolation_accuracy": (
            metrics["repository_path_isolation_accuracy"] == 100.0
        ),
        "per_case_support": all(_valid_case_support(case, threshold) for case in cases),
        "unsupported_question_abstains": (
            unsupported["memoryforge"]["answer_status"] == "unknown"
            and isinstance(unsupported["memoryforge"]["support"], dict)
            and unsupported["memoryforge"]["support"]["score"] < threshold
        ),
        "no_failed_cases": not failures,
        "deterministic_replay": _deterministic_replay(runs),
        "stable_memoryforge_commit": _git("rev-parse", "HEAD") == memoryforge_commit,
        "clean_worktree_after_run": not bool(_git("status", "--porcelain")),
        "stable_source_commit": _git_at(source, "rev-parse", "HEAD") == source_commit,
        "clean_source_worktree_after_run": not bool(_git_at(source, "status", "--porcelain")),
        "confirmation_not_run": manifest["confirmation"]["status"] == "not_run",
    }
    evidence = {
        "schema_version": 1,
        "suite_id": manifest["suite_id"],
        "suite_revision": manifest["suite_revision"],
        "memoryforge_commit": memoryforge_commit,
        "memoryforge_worktree_dirty": False,
        "source_manifest": {
            "path": str(MANIFEST.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        },
        "source_repository": {
            "repository": repository["repository"],
            "remote_url": _git_at(source, "remote", "get-url", "origin"),
            "commit": source_commit,
            "license": repository["license"],
            "license_sha256": repository["license_sha256"],
            "source_paths": repository["code_paths"],
        },
        "development": {
            "path": manifest["development"]["path"],
            "sha256": manifest["development"]["sha256"],
            "case_count": len(cases),
            "support_threshold": threshold,
            "metrics": metrics,
            "failures": failures,
            "evaluation": first,
        },
        "baseline_evidence": manifest["baseline_evidence"],
        "confirmation": manifest["confirmation"],
        "runs": runs,
        "gates": gates,
        "passed": all(gates.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote support-score evidence to {output}")
    if not evidence["passed"]:
        raise SystemExit("support-score benchmark failed")


def _payload_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _require_external_output(output: Path, *repositories: Path) -> None:
    if any(output.is_relative_to(repository) for repository in repositories):
        raise SystemExit("--output must be outside MemoryForge and source repositories")


def _deterministic_replay(runs: list[dict[str, Any]]) -> bool:
    return all(runs[0][key] == runs[1][key] for key in ("structural_sha256", "evaluation_sha256"))


def _validate_artifact(artifact: dict[str, Any]) -> None:
    path = REPO_ROOT / str(artifact["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
        raise ValueError(f"frozen artifact SHA256 mismatch: {artifact['path']}")


def _valid_support_payload(payload: object, threshold: float) -> bool:
    if not isinstance(payload, dict) or set(payload) != {
        "score",
        "threshold",
        "sufficient",
        "enforced",
        "components",
        "failed_hard_gates",
    }:
        return False
    score = payload["score"]
    components = payload["components"]
    failed_hard_gates = payload["failed_hard_gates"]
    expected_score = (
        round(
            100
            * (
                0.20 * components["exact_identifier_coverage"]
                + 0.35 * components["core_term_coverage"]
                + 0.15 * components["fact_co_location"]
                + 0.10 * components["negation_alignment"]
                + 0.10 * components["multi_source_coverage"]
                + 0.10 * components["current_source_versions"]
            ),
            1,
        )
        if isinstance(components, dict)
        and set(components)
        == {
            "exact_identifier_coverage",
            "core_term_coverage",
            "fact_co_location",
            "negation_alignment",
            "multi_source_coverage",
            "current_source_versions",
        }
        and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
            for value in components.values()
        )
        else None
    )
    return (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and 0 <= score <= 100
        and score == expected_score
        and payload["threshold"] == threshold
        and isinstance(payload["sufficient"], bool)
        and isinstance(payload["enforced"], bool)
        and isinstance(failed_hard_gates, list)
        and all(isinstance(gate, str) and gate for gate in failed_hard_gates)
        and len(failed_hard_gates) == len(set(failed_hard_gates))
        and payload["sufficient"] is (not failed_hard_gates)
        and (payload["enforced"] or (payload["sufficient"] is True and not failed_hard_gates))
    )


def _valid_case_support(case: dict[str, Any], threshold: float) -> bool:
    memoryforge = case.get("memoryforge")
    if not isinstance(memoryforge, dict):
        return False
    support = memoryforge.get("support")
    if support is None:
        return memoryforge.get("answer_status") == "unknown"
    if not _valid_support_payload(support, threshold) or support["enforced"] is not True:
        return False
    components = support["components"]
    failed_hard_gates = []
    question = str(case.get("question", ""))
    if _support_identifiers(question) and components["exact_identifier_coverage"] < 1:
        failed_hard_gates.append("exact_identifier_not_covered")
    if support["score"] < threshold:
        failed_hard_gates.append("score_below_threshold")
    if _has_support_condition(question) and components["fact_co_location"] < 1:
        failed_hard_gates.append("condition_not_co_located")
    if _has_support_negation(question) and components["negation_alignment"] < 1:
        failed_hard_gates.append("negation_not_aligned")
    if (
        case.get("category") in {"multi_source", "cross_repository"}
        and components["multi_source_coverage"] < 1
    ):
        failed_hard_gates.append("multi_source_incomplete")
    if components["current_source_versions"] < 1:
        failed_hard_gates.append("citation_not_current")
    return support["failed_hard_gates"] == failed_hard_gates and support["sufficient"] is (
        memoryforge.get("answer_status") == "answered"
    )


def _git(*args: str) -> str:
    return _git_at(REPO_ROOT, *args)


def _git_at(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
