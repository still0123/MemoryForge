#!/usr/bin/env python3
"""Run model-free Code Wiki checks against pinned public repositories."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from memoryforge.code.code_evaluation import CodeEvaluationSuite, run_code_evaluation

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "demo/evaluation/external_code_wiki_sources_v021.json"


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")
    sources = _parse_sources(args.source_repo)
    evidence = build_evidence(workdir, sources, args.manifest.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote external Code Wiki evidence to {output}")
    if not evidence["summary"]["passed"]:
        raise SystemExit("external Code Wiki benchmark did not pass all frozen labels")


def build_evidence(
    workdir: Path,
    sources: dict[str, Path],
    manifest_path: Path = MANIFEST,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repositories = manifest["repositories"]
    expected_names = {str(repository["name"]) for repository in repositories}
    if set(sources) != expected_names:
        raise ValueError(f"--source-repo names must be: {', '.join(sorted(expected_names))}")

    results = [
        _evaluate_repository(
            workdir / str(repository["name"]),
            sources[str(repository["name"])].resolve(),
            repository,
            REPO_ROOT / str(repository["suite"]),
        )
        for repository in repositories
    ]
    source_cases = [case for result in results for case in result["evaluation"]["cases"]["sources"]]
    symbol_cases = [case for result in results for case in result["evaluation"]["cases"]["symbols"]]
    relation_cases = [
        case for result in results for case in result["evaluation"]["cases"]["relations"]
    ]
    module_cases = [case for result in results for case in result["evaluation"]["cases"]["modules"]]
    passed = all(bool(result["passed"]) for result in results)
    return {
        "schema_version": 1,
        "memoryforge_commit": _git_output(REPO_ROOT, "rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": bool(_git_output(REPO_ROOT, "status", "--porcelain")),
        "source_manifest": {
            "path": str(manifest_path.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
        "label_scope": "non_exhaustive_manually_verified_positive_sample",
        "repositories": results,
        "summary": {
            "repository_count": len(results),
            "expected_source_count": len(source_cases),
            "manual_symbol_labels": len(symbol_cases),
            "manual_relation_labels": len(relation_cases),
            "manual_module_labels": len(module_cases),
            "actual_counts": {
                "symbols": sum(
                    int(result["evaluation"]["counts"]["symbols"]) for result in results
                ),
                "relations": sum(
                    int(result["evaluation"]["counts"]["relations"]) for result in results
                ),
                "modules": sum(
                    int(result["evaluation"]["counts"]["modules"]) for result in results
                ),
                "architecture_edges": sum(
                    int(result["evaluation"]["counts"]["architecture_edges"]) for result in results
                ),
                "code_wiki_files": sum(
                    int(result["workflow"]["code_wiki_file_count"]) for result in results
                ),
            },
            "metrics": {
                "expected_source_coverage": _case_percentage(source_cases),
                "symbol_label_match": _case_percentage(symbol_cases),
                "relation_label_match": _case_percentage(relation_cases),
                "module_label_match": _case_percentage(module_cases),
            },
            "passed": passed,
        },
    }


def _evaluate_repository(
    workdir: Path,
    source_repo: Path,
    repository: dict[str, Any],
    suite_path: Path,
) -> dict[str, Any]:
    name = str(repository["name"])
    _verify_source(source_repo, repository)
    suite = CodeEvaluationSuite.model_validate_json(suite_path.read_text(encoding="utf-8"))
    if suite.incremental is not None:
        raise ValueError(f"{name} external suite must not claim unmeasured incremental results")
    if len(suite.symbols) != 20 or len(suite.relations) != 15 or len(suite.modules) != 5:
        raise ValueError(f"{name} suite must freeze 20 symbols, 15 relations, and 5 modules")
    if len({symbol.qualified_name for symbol in suite.symbols}) != len(suite.symbols):
        raise ValueError(f"{name} suite contains duplicate symbol labels")
    relation_keys = {
        (relation.type, relation.source, relation.target) for relation in suite.relations
    }
    if len(relation_keys) != len(suite.relations):
        raise ValueError(f"{name} suite contains duplicate relation labels")
    if len(suite.expected_source_paths) != int(repository["expected_source_count"]):
        raise ValueError(f"{name} source count disagrees with its frozen suite")

    workspace = workdir / "workspace"
    _cli("init", str(workspace))
    registration = _cli_json("git-add", str(source_repo), "--public", *_ws(workspace))
    repository_id = str(registration["repository_id"])
    for code_path in repository["code_paths"]:
        _cli_json("code-add", repository_id, str(code_path), *_ws(workspace))
    synced = _cli_json("git-sync", repository_id, *_ws(workspace))
    ingest = _cli_json("ingest", "--code-wiki", repository_id, *_ws(workspace))
    _cli("review", str(ingest["changeset_id"]), *_ws(workspace))
    _cli_json("approve", str(ingest["changeset_id"]), *_ws(workspace))
    applied = _cli_json("apply", str(ingest["changeset_id"]), *_ws(workspace))
    lint = _cli_json("lint", *_ws(workspace))
    evaluation = run_code_evaluation(workspace, repository_id, suite_path)
    metrics = evaluation["metrics"]
    gates = evaluation["gates"]
    passed = (
        metrics["expected_source_coverage"] == 100.0
        and metrics["symbol_recall"] == 100.0
        and metrics["core_relation_recall"] == 100.0
        and metrics["module_assignment_accuracy"] == 100.0
        and metrics["citation_grounding_accuracy"] == 100.0
        and metrics["architecture_edge_grounding"] == 100.0
        and metrics["deterministic_replay"] == 100.0
        and all(bool(value) for value in gates.values())
        and lint["status"] == "clean"
    )
    not_applicable = ["known_gap_relation_recall"]
    if evaluation["counts"]["architecture_edges"] == 0:
        not_applicable.extend(
            [
                "mermaid_edge_coverage",
                "architecture_citation_coverage",
                "architecture_mermaid_determinism",
            ]
        )
    return {
        "name": name,
        "remote_url": repository["remote_url"],
        "commit": repository["commit"],
        "license": repository["license"],
        "license_sha256": repository["license_sha256"],
        "checkout_worktree_dirty": bool(_git_output(source_repo, "status", "--porcelain")),
        "code_paths": repository["code_paths"],
        "suite": {
            "path": str(suite_path.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
            "manual_symbol_labels": len(suite.symbols),
            "manual_relation_labels": len(suite.relations),
            "manual_module_labels": len(suite.modules),
        },
        "workflow": {
            "synced_document_count": int(synced["created"]) + int(synced["updated"]),
            "indexed_source_count": len(suite.expected_source_paths),
            "code_wiki_file_count": sum(
                path.startswith("wiki/pages/code/") for path in applied["files"]
            ),
            "lint": lint,
        },
        "not_applicable_metrics": not_applicable,
        "evaluation": evaluation,
        "passed": passed,
    }


def _verify_source(source_repo: Path, repository: dict[str, Any]) -> None:
    if _git_output(source_repo, "rev-parse", "HEAD") != repository["commit"]:
        raise ValueError(f"{repository['name']} checkout is not at the pinned commit")
    if _git_output(source_repo, "remote", "get-url", "origin") != repository["remote_url"]:
        raise ValueError(f"{repository['name']} checkout has the wrong origin")
    license_bytes = _git_bytes(
        source_repo,
        "show",
        f"HEAD:{repository['license_path']}",
    )
    if hashlib.sha256(license_bytes).hexdigest() != repository["license_sha256"]:
        raise ValueError(f"{repository['name']} license hash does not match the manifest")


def _case_percentage(cases: list[dict[str, Any]]) -> float:
    return round(100 * sum(bool(case["found"]) for case in cases) / len(cases), 1)


def _parse_sources(values: list[str]) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise ValueError("--source-repo must use NAME=/absolute/path")
        if name in sources:
            raise ValueError(f"duplicate --source-repo name: {name}")
        sources[name] = Path(path)
    return sources


def _ws(workspace: Path) -> tuple[str, str]:
    return "--workspace", str(workspace)


def _cli(*args: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "memoryforge", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"memoryforge {' '.join(args)} failed:\n{completed.stderr.strip()}")
    return completed.stdout


def _cli_json(*args: str) -> Any:
    return json.loads(_cli(*args))


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-repo",
        action="append",
        required=True,
        help="Pinned checkout in NAME=/absolute/path form; repeat for click, cobra, zod.",
    )
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
