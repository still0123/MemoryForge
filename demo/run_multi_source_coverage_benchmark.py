#!/usr/bin/env python3
"""Run the frozen multi-source selector development benchmark twice."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

from memoryforge.compiler.wiki_facts import CitationPayload
from memoryforge.query.query import _top_matches

REPO_ROOT = Path(__file__).resolve().parent.parent
DEVELOPMENT = REPO_ROOT / "demo/evaluation/multi_source_coverage_development.json"
CONFIRMATION = REPO_ROOT / "demo/evaluation/multi_source_coverage_confirmation.json"
DEVELOPMENT_SHA256 = "0815f3a2230fbc2b094310f03ce23dd7bf1bf51f362707dd32cd0ae0cd04fb73"
CONFIRMATION_SHA256 = "2759e7f3cc34e97b575e59a128a21e7c56e07d5d3afe08d8c80d02f4dc5a5a1a"
SOURCE_ID = re.compile(r"^[a-f0-9]{64}$")


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
    development = cast(dict[str, Any], json.loads(DEVELOPMENT.read_text(encoding="utf-8")))
    confirmation = cast(dict[str, Any], json.loads(CONFIRMATION.read_text(encoding="utf-8")))
    _validate_suite(development, expected_split="development")
    _validate_suite(confirmation, expected_split="confirmation")

    first = _run_suite(development)
    second = _run_suite(development)
    runs = [
        {"name": "first", "evaluation_sha256": _payload_sha256(first)},
        {"name": "second", "evaluation_sha256": _payload_sha256(second)},
    ]
    metrics = cast(dict[str, float], first["metrics"])
    selector_supports_required_sources = (
        "required_sources" in inspect.signature(_top_matches).parameters
    )
    gates = {
        "selection_accuracy": metrics["selection_accuracy"] == 100.0,
        "source_coverage_accuracy": metrics["source_coverage_accuracy"] == 100.0,
        "term_coverage_accuracy": metrics["term_coverage_accuracy"] == 100.0,
        "single_source_rank_preservation": (metrics["single_source_rank_preservation"] == 100.0),
        "duplicate_source_rate": metrics["duplicate_source_rate"] == 0.0,
        "deterministic_replay": runs[0]["evaluation_sha256"] == runs[1]["evaluation_sha256"],
        "selector_supports_required_sources": selector_supports_required_sources,
        "stable_memoryforge_commit": _git("rev-parse", "HEAD") == memoryforge_commit,
        "clean_worktree_after_run": not bool(_git("status", "--porcelain")),
        "confirmation_not_run": True,
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
    print(f"Wrote multi-source coverage evidence to {output}")
    if not evidence["passed"]:
        raise SystemExit("multi-source coverage benchmark failed")


def _run_suite(suite: dict[str, Any]) -> dict[str, Any]:
    results = [_run_case(case) for case in suite["cases"]]
    selected_count = sum(len(case["selected_source_versions"]) for case in results)
    distinct_count = sum(case["distinct_source_count"] for case in results)
    single_source = [case for case in results if case["required_source_count"] == 1]
    return {
        "case_count": len(results),
        "metrics": {
            "selection_accuracy": _percentage(case["selection_correct"] for case in results),
            "source_coverage_accuracy": _percentage(
                case["source_coverage_correct"] for case in results
            ),
            "term_coverage_accuracy": _percentage(
                case["term_coverage_correct"] for case in results
            ),
            "single_source_rank_preservation": _percentage(
                case["selection_correct"] for case in single_source
            ),
            "duplicate_source_rate": (
                round(100 * (selected_count - distinct_count) / selected_count, 1)
                if selected_count
                else 0.0
            ),
        },
        "cases": results,
    }


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    matches = [
        (
            tuple(candidate["rank"]),
            candidate["page_path"],
            cast(
                CitationPayload,
                {
                    "source_id": candidate["source_id"],
                    "source_version": candidate["source_version"],
                    "locator": candidate["locator"],
                    "quote": candidate["quote"],
                },
            ),
        )
        for candidate in case["candidates"]
    ]
    kwargs: dict[str, Any] = {"question_terms": set(case["question_terms"])}
    if "required_sources" in inspect.signature(_top_matches).parameters:
        kwargs["required_sources"] = case["required_source_count"]
    selected = _top_matches(matches, case["max_citations"], **kwargs)
    selected_sources = [
        [citation["source_id"], citation["source_version"]] for _, citation in selected
    ]
    selected_source_set = {(source_id, version) for source_id, version in selected_sources}
    expected_sources = {
        (source_id, version) for source_id, version in case["expected_source_versions"]
    }
    selected_text = " ".join(citation["quote"] for _, citation in selected).casefold()
    source_coverage_correct = expected_sources <= selected_source_set
    term_coverage_correct = all(term.casefold() in selected_text for term in case["expected_terms"])
    return {
        "id": case["id"],
        "required_source_count": case["required_source_count"],
        "selected_source_versions": selected_sources,
        "selected_quotes": [citation["quote"] for _, citation in selected],
        "distinct_source_count": len(selected_source_set),
        "source_coverage_correct": source_coverage_correct,
        "term_coverage_correct": term_coverage_correct,
        "selection_correct": source_coverage_correct and term_coverage_correct,
    }


def _validate_suite(suite: dict[str, Any], *, expected_split: str) -> None:
    expected_keys = {"schema_version", "suite_id", "suite_revision", "split", "cases"}
    if expected_split == "confirmation":
        expected_keys.add("status")
    if (
        set(suite) != expected_keys
        or suite.get("schema_version") != 1
        or suite.get("suite_id") != "multi-source-coverage-selection"
        or suite.get("suite_revision") != 1
        or suite.get("split") != expected_split
        or (expected_split == "confirmation" and suite.get("status") != "not_run")
        or not isinstance(suite.get("cases"), list)
        or not suite["cases"]
    ):
        raise ValueError("invalid multi-source coverage suite")
    ids = [case.get("id") for case in suite["cases"]]
    if any(not isinstance(case_id, str) or not case_id for case_id in ids) or len(ids) != len(
        set(ids)
    ):
        raise ValueError("multi-source coverage case IDs must be unique")
    for case in suite["cases"]:
        _validate_case(case)


def _validate_case(case: dict[str, Any]) -> None:
    if set(case) != {
        "id",
        "question_terms",
        "max_citations",
        "required_source_count",
        "candidates",
        "expected_source_versions",
        "expected_terms",
    }:
        raise ValueError("invalid multi-source coverage case schema")
    max_citations = case["max_citations"]
    required_sources = case["required_source_count"]
    candidates = case["candidates"]
    expected_sources = case["expected_source_versions"]
    if (
        isinstance(max_citations, bool)
        or not isinstance(max_citations, int)
        or isinstance(required_sources, bool)
        or not isinstance(required_sources, int)
        or not 1 <= required_sources <= max_citations <= 10
        or not isinstance(candidates, list)
        or len(candidates) < max_citations
        or not isinstance(expected_sources, list)
        or len(expected_sources) != required_sources
    ):
        raise ValueError("invalid multi-source coverage case budget")
    if any(
        not isinstance(term, str) or not term
        for field in ("question_terms", "expected_terms")
        for term in case[field]
    ):
        raise ValueError("multi-source coverage terms must be non-empty strings")
    if len({tuple(source) for source in expected_sources}) != len(expected_sources):
        raise ValueError("expected source versions must be unique")
    for source_id, source_version in expected_sources:
        if (
            SOURCE_ID.fullmatch(str(source_id)) is None
            or isinstance(source_version, bool)
            or not isinstance(source_version, int)
            or source_version < 1
        ):
            raise ValueError("invalid expected source version")
    for candidate in candidates:
        if (
            set(candidate)
            != {
                "rank",
                "page_path",
                "source_id",
                "source_version",
                "locator",
                "quote",
            }
            or not isinstance(candidate["rank"], list)
            or len(candidate["rank"]) < 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) for value in candidate["rank"]
            )
            or not isinstance(candidate["page_path"], str)
            or not candidate["page_path"].startswith("wiki/pages/")
            or SOURCE_ID.fullmatch(str(candidate["source_id"])) is None
            or isinstance(candidate["source_version"], bool)
            or not isinstance(candidate["source_version"], int)
            or candidate["source_version"] < 1
            or not isinstance(candidate["locator"], str)
            or not candidate["locator"].startswith("chars:")
            or not isinstance(candidate["quote"], str)
            or not candidate["quote"]
        ):
            raise ValueError("invalid multi-source coverage candidate")


def _validate_artifact(path: Path, expected_sha256: str) -> None:
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError(f"frozen artifact SHA256 mismatch: {path.relative_to(REPO_ROOT)}")


def _percentage(values: Any) -> float:
    checked = list(values)
    return round(100 * sum(bool(value) for value in checked) / len(checked), 1) if checked else 0.0


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
