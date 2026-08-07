#!/usr/bin/env python3
"""Run frozen exact-Symbol development and structural gates twice."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from memoryforge.evaluation import run_evaluation

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "demo/evaluation/exact_symbol_routing_sources.json"
_EXTERNAL_SCRIPT = REPO_ROOT / "demo/run_external_code_wiki_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("run_external_code_wiki_benchmark", _EXTERNAL_SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("could not load external Code Wiki benchmark")
external_benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(external_benchmark)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))
    for key in ("development", "case_type_overlay", "baseline_evidence", "confirmation"):
        _validate_artifact(cast(dict[str, Any], manifest[key]))
    repository = cast(dict[str, Any], manifest["repositories"][0])
    source = args.source_repo.resolve()
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")

    sources = {str(repository["name"]): source}
    development_path = REPO_ROOT / str(manifest["development"]["path"])
    exact_case_ids = _exact_case_ids(manifest)
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
                "evaluation_sha256": _payload_sha256(evaluation),
                "metrics": evaluation["memoryforge"],
            }
        )

    first = cast(dict[str, Any], evaluations[0])
    metrics = cast(dict[str, Any], first["memoryforge"])
    cases = cast(list[dict[str, Any]], first["cases"])
    exact_cases = [case for case in cases if case["id"] in exact_case_ids]
    failures = [
        {
            "id": case["id"],
            "category": case["category"],
            "error_classification": case["memoryforge"]["error_classification"],
            "page_route_recalled": case["memoryforge"]["page_route_expected_sources_recalled"],
            "fact_selection_correct": case["memoryforge"]["fact_selection_correct"],
        }
        for case in cases
        if case["memoryforge"]["error_classification"] != "none"
    ]
    gates = {
        "structural_benchmark": all(run["structural_passed"] for run in runs),
        "exact_symbol_answer_accuracy": (
            len(exact_cases) == len(exact_case_ids)
            and all(case["memoryforge"]["answer_correct"] for case in exact_cases)
        ),
        "answer_accuracy_at_least_90": metrics["answer_accuracy"] >= 90.0,
        "page_route_recall_at_3": metrics["page_route_recall_at_3"] == 100.0,
        "source_recall_at_3": metrics["source_recall_at_3"] == 100.0,
        "fact_selection_accuracy": metrics["fact_selection_accuracy"] == 100.0,
        "citation_grounding_accuracy": metrics["citation_grounding_accuracy"] == 100.0,
        "multi_source_coverage": metrics["multi_source_coverage"] == 100.0,
        "repository_path_isolation_accuracy": (
            metrics["repository_path_isolation_accuracy"] == 100.0
        ),
        "deferred_abstention_gap_visible": (
            metrics["abstention_accuracy"] == 0.0
            and any(
                failure["id"] == "dev-unknown-vector-database"
                and failure["error_classification"] == "wrong_abstention"
                for failure in failures
            )
        ),
        "deterministic_replay": runs[0]["evaluation_sha256"] == runs[1]["evaluation_sha256"],
        "confirmation_not_run": manifest["confirmation"]["status"] == "not_run",
    }
    evidence = {
        "schema_version": 1,
        "suite_id": manifest["suite_id"],
        "suite_revision": manifest["suite_revision"],
        "memoryforge_commit": _git("rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": bool(_git("status", "--porcelain")),
        "source_manifest": {
            "path": str(MANIFEST.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        },
        "source_repository": {
            "repository": repository["repository"],
            "remote_url": _git_at(source, "remote", "get-url", "origin"),
            "commit": _git_at(source, "rev-parse", "HEAD"),
            "license": repository["license"],
            "license_sha256": repository["license_sha256"],
            "source_paths": repository["code_paths"],
        },
        "development": {
            "path": manifest["development"]["path"],
            "sha256": manifest["development"]["sha256"],
            "case_count": len(cases),
            "exact_symbol_case_ids": sorted(exact_case_ids),
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
    print(f"Wrote exact Symbol routing evidence to {output}")
    if not evidence["passed"]:
        raise SystemExit("exact Symbol routing benchmark failed")


def _exact_case_ids(manifest: dict[str, Any]) -> set[str]:
    overlay_config = cast(dict[str, Any], manifest["case_type_overlay"])
    overlay_path = REPO_ROOT / str(overlay_config["path"])
    overlay = cast(dict[str, Any], json.loads(overlay_path.read_text(encoding="utf-8")))
    return {
        str(mapping["case_id"])
        for mapping in overlay["mappings"]
        if mapping["suite_id"] == overlay_config["suite_id"]
        and mapping["split"] == overlay_config["split"]
        and mapping["case_type"] == "exact_symbol"
    }


def _payload_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _validate_artifact(artifact: dict[str, Any]) -> None:
    path = REPO_ROOT / str(artifact["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
        raise ValueError(f"frozen artifact SHA256 mismatch: {artifact['path']}")


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
