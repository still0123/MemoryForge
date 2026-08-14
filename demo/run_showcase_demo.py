#!/usr/bin/env python3
"""Build the zero-key public Code Wiki Showcase and retain real negative evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from run_code_wiki_benchmark import build_evidence

from memoryforge.portal.showcase import build_showcase

REPO_ROOT = Path(__file__).resolve().parent.parent
CLICK_EVIDENCE = REPO_ROOT / "demo/results/click_docs_holdout_v021.json"
CLICK_EVIDENCE_SHA256 = "a9b9f47bc805987384ae5d006498e8780e69c8b1ae15d00fdba49abad792efe1"
DOCUMENT_EVIDENCE = REPO_ROOT / "demo/results/agent_skill_eval_public.json"
DOCUMENT_EVIDENCE_SHA256 = "d777b5b7547b3e53d66742b03cd5a376d523d17ab7caa108bfa5684ec24f95cf"


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")
    _validate_artifact(CLICK_EVIDENCE, CLICK_EVIDENCE_SHA256)
    _validate_artifact(DOCUMENT_EVIDENCE, DOCUMENT_EVIDENCE_SHA256)

    code_evidence = build_evidence(workdir, include_sample_query=True)
    public_evidence = _showcase_evidence(code_evidence)
    evidence_path = workdir / "showcase-evidence.json"
    evidence_path.write_text(
        json.dumps(public_evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = build_showcase(
        workdir / "workspace",
        output,
        evidence=evidence_path,
        include_local=True,
    )
    print(
        json.dumps(
            {
                **result,
                "output": str(output),
                "evidence": str(evidence_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _showcase_evidence(code_evidence: dict[str, Any]) -> dict[str, Any]:
    click = cast(dict[str, Any], json.loads(CLICK_EVIDENCE.read_text(encoding="utf-8")))
    document = cast(dict[str, Any], json.loads(DOCUMENT_EVIDENCE.read_text(encoding="utf-8")))
    failure = _find_case(click, "holdout-abort-exit")
    abstention = _find_case(document, "unknown-kubernetes-capacity")
    return {
        "schema_version": 1,
        "sample_query": code_evidence["sample_query"],
        "evaluation": {
            "suite": "Code Wiki metrics with retained external validity evidence",
            "case_count": 2,
            "memoryforge": code_evidence["evaluation"]["metrics"],
            "cases": [
                {
                    "id": failure["id"],
                    "category": failure["category"],
                    "error_classification": "EXPECTED_SOURCE_MISS",
                    "memoryforge": failure["memoryforge"],
                },
                {
                    "id": abstention["id"],
                    "category": abstention["category"],
                    "error_classification": None,
                    "memoryforge": abstention["memoryforge"],
                },
            ],
        },
    }


def _find_case(evidence: dict[str, Any], identifier: str) -> dict[str, Any]:
    evaluation = evidence.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("public evidence is missing its evaluation")
    cases = evaluation.get("cases")
    if not isinstance(cases, list):
        raise ValueError("public evidence is missing its cases")
    for case in cases:
        if isinstance(case, dict) and case.get("id") == identifier:
            return cast(dict[str, Any], case)
    raise ValueError(f"public evidence is missing case: {identifier}")


def _validate_artifact(path: Path, expected_sha256: str) -> None:
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError(f"public evidence SHA256 mismatch: {path.relative_to(REPO_ROOT)}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
