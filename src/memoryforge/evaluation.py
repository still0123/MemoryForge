"""Small, reproducible checks for an applied MemoryForge workspace."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from memoryforge.manifests import SourceManifestStore
from memoryforge.query import answer_question
from memoryforge.workspace import read_source_excerpt, search_sources


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_source_path: str = Field(min_length=1)
    required_terms: tuple[str, ...] = Field(min_length=1)


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)


def run_evaluation(workspace_root: Path, config_path: Path) -> dict[str, object]:
    """Compare bounded Wiki answers with the existing raw-source FTS baseline."""
    suite = EvaluationSuite.model_validate_json(config_path.read_text(encoding="utf-8"))
    source_paths = {
        manifest.source_id: manifest.source_path
        for manifest in SourceManifestStore(
            workspace_root / ".memoryforge/manifests/sources"
        ).list_all()
    }
    cases = [_evaluate_case(workspace_root, case, source_paths) for case in suite.cases]
    total = len(cases)
    return {
        "suite": suite.name,
        "case_count": total,
        "memoryforge": {
            "answer_accuracy": _percentage(
                case["memoryforge"]["answer_correct"] for case in cases
            ),
            "citation_accuracy": _percentage(
                case["memoryforge"]["citation_correct"] for case in cases
            ),
            "average_wiki_pages_read": round(
                sum(case["memoryforge"]["wiki_pages_read"] for case in cases) / total,
                2,
            ),
            "average_raw_sources_read": round(
                sum(case["memoryforge"]["raw_sources_read"] for case in cases) / total,
                2,
            ),
            "average_citation_audit_characters": round(
                sum(case["memoryforge"]["citation_audit_characters"] for case in cases)
                / total,
                2,
            ),
        },
        "raw_fts_baseline": {
            "expected_source_recall_at_3": _percentage(
                case["raw_fts_baseline"]["expected_source_in_top_3"] for case in cases
            ),
            "average_exposed_characters": round(
                sum(case["raw_fts_baseline"]["exposed_characters"] for case in cases) / total,
                2,
            ),
        },
        "cases": cases,
    }


def _evaluate_case(
    workspace_root: Path,
    case: EvaluationCase,
    source_paths: dict[str, str],
) -> dict[str, Any]:
    answer = answer_question(workspace_root, case.question, debug=True)
    answer_text = answer["answer"]
    citations = answer["citations"]
    evidence = [
        read_source_excerpt(
            workspace_root,
            source_id=citation["source_id"],
            source_version=citation["source_version"],
            locator=citation["locator"],
        )
        for citation in citations
    ]
    raw_results = search_sources(workspace_root, case.question, limit=3)
    return {
        "id": case.id,
        "question": case.question,
        "memoryforge": {
            "answer_correct": answer["status"] == "answered"
            and all(term.casefold() in answer_text.casefold() for term in case.required_terms),
            "citation_correct": any(
                _same_source_path(
                    source_paths.get(citation["source_id"]), case.expected_source_path
                )
                and _normalise(citation["quote"]) in _normalise(excerpt)
                for citation, excerpt in zip(citations, evidence, strict=True)
            ),
            "wiki_pages_read": sum(step["level"] == "L1" for step in answer.get("trace", [])),
            "raw_sources_read": sum(step["level"] == "L3" for step in answer.get("trace", [])),
            "citation_audit_characters": sum(len(excerpt) for excerpt in evidence),
            "wiki_pages": answer["wiki_pages"],
        },
        "raw_fts_baseline": {
            "expected_source_in_top_3": any(
                _same_source_path(result.source_path, case.expected_source_path)
                for result in raw_results
            ),
            "result_count": len(raw_results),
            "exposed_characters": sum(len(result.snippet) for result in raw_results),
        },
    }


def _percentage(values: Iterable[bool]) -> float:
    checked = list(values)
    return round(100 * sum(bool(value) for value in checked) / len(checked), 1)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _same_source_path(actual: str | None, expected: str) -> bool:
    if actual is None:
        return False
    actual_path = actual.replace("\\", "/")
    expected_path = expected.replace("\\", "/").lstrip("/")
    return actual_path == expected_path or actual_path.endswith(f"/{expected_path}")
