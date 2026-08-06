"""Small, reproducible checks for an applied MemoryForge workspace."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memoryforge.manifests import SourceManifestStore
from memoryforge.query import answer_question
from memoryforge.workspace import read_source_excerpt, search_sources


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    category: Literal["single_hop", "multi_source", "unanswerable", "paraphrase"]
    question: str = Field(min_length=1)
    expected_status: Literal["answered", "unknown"]
    expected_source_paths: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    repository_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_case(self) -> EvaluationCase:
        if self.expected_status == "answered" and not self.expected_source_paths:
            raise ValueError("answered evaluation cases require expected_source_paths")
        if self.expected_status == "answered" and not self.required_terms:
            raise ValueError("answered evaluation cases require required_terms")
        if self.category == "multi_source" and len(self.expected_source_paths) < 2:
            raise ValueError("multi_source evaluation cases require at least two sources")
        if self.category == "unanswerable" and self.expected_status != "unknown":
            raise ValueError("unanswerable evaluation cases must expect unknown")
        return self


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
            "answer_accuracy": _percentage(case["memoryforge"]["answer_correct"] for case in cases),
            "source_recall_at_3": _percentage(
                case["memoryforge"]["expected_sources_recalled"]
                for case in cases
                if case["category"] != "unanswerable"
            ),
            "citation_grounding_accuracy": _percentage(
                case["memoryforge"]["citation_grounded"]
                for case in cases
                if case["category"] != "unanswerable"
            ),
            "multi_source_coverage": _percentage(
                case["memoryforge"]["all_expected_sources_cited"]
                for case in cases
                if case["category"] == "multi_source"
            ),
            "abstention_accuracy": _percentage(
                case["memoryforge"]["abstention_correct"]
                for case in cases
                if case["category"] == "unanswerable"
            ),
            "average_wiki_pages_read": round(
                sum(case["memoryforge"]["wiki_pages_read"] for case in cases) / total,
                2,
            ),
            "average_raw_sources_read": round(
                sum(case["memoryforge"]["raw_sources_read"] for case in cases) / total,
                2,
            ),
            "average_evidence_characters": round(
                sum(case["memoryforge"]["evidence_characters"] for case in cases) / total,
                2,
            ),
        },
        "raw_fts_baseline": {
            "expected_source_recall_at_3": _percentage(
                case["raw_fts_baseline"]["expected_sources_recalled"]
                for case in cases
                if case["category"] != "unanswerable"
            ),
            "multi_source_coverage": _percentage(
                case["raw_fts_baseline"]["expected_sources_in_top_3"]
                for case in cases
                if case["category"] == "multi_source"
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
    citation_limit = len(case.expected_source_paths) if case.category == "multi_source" else 1
    answer = answer_question(
        workspace_root,
        case.question,
        debug=True,
        max_citations=citation_limit,
        repository_id=case.repository_id,
    )
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
    actual_source_paths = {source_paths.get(citation["source_id"]) for citation in citations}
    actual_source_paths.discard(None)
    expected_source_paths = {
        path.replace("\\", "/").lstrip("/") for path in case.expected_source_paths
    }
    expected_sources_recalled = not expected_source_paths or (
        expected_source_paths <= actual_source_paths
        if case.category == "multi_source"
        else bool(expected_source_paths & actual_source_paths)
    )
    citation_grounded = bool(citations) and all(
        _normalise(citation["quote"]) in _normalise(excerpt)
        for citation, excerpt in zip(citations, evidence, strict=True)
    )
    if case.expected_status == "unknown":
        citation_grounded = not citations and answer["status"] == "unknown"
    answer_correct = (
        answer["status"] == case.expected_status
        and expected_sources_recalled
        and all(term.casefold() in answer_text.casefold() for term in case.required_terms)
        and all(term.casefold() not in answer_text.casefold() for term in case.forbidden_terms)
    )
    raw_results = search_sources(
        workspace_root,
        case.question,
        limit=3,
        repository_id=case.repository_id,
        require_all_terms=False,
    )
    raw_source_paths = {result.source_path for result in raw_results}
    raw_expected_source_in_top_3 = (
        any(
            _same_source_path(result.source_path, expected_path)
            for result in raw_results
            for expected_path in case.expected_source_paths
        )
        if case.expected_source_paths
        else True
    )
    raw_expected_sources_in_top_3 = (
        all(
            any(_same_source_path(result.source_path, expected_path) for result in raw_results)
            for expected_path in case.expected_source_paths
        )
        if case.expected_source_paths
        else True
    )
    return {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "memoryforge": {
            "answer_correct": answer_correct,
            "citation_grounded": citation_grounded,
            "expected_sources_recalled": expected_sources_recalled,
            "all_expected_sources_cited": (
                expected_source_paths <= actual_source_paths if expected_source_paths else True
            ),
            "abstention_correct": answer["status"] == case.expected_status,
            "wiki_pages_read": sum(step["level"] == "L1" for step in answer.get("trace", [])),
            "raw_sources_read": sum(step["level"] == "L3" for step in answer.get("trace", [])),
            "evidence_characters": sum(len(excerpt) for excerpt in evidence),
            "wiki_pages": answer["wiki_pages"],
            "cited_source_paths": sorted(actual_source_paths),
        },
        "raw_fts_baseline": {
            "expected_source_in_top_3": raw_expected_source_in_top_3,
            "expected_sources_in_top_3": raw_expected_sources_in_top_3,
            "expected_sources_recalled": (
                raw_expected_sources_in_top_3
                if case.category == "multi_source"
                else raw_expected_source_in_top_3
            ),
            "raw_source_paths": sorted(raw_source_paths),
            "expected_source_paths": sorted(expected_source_paths),
            "result_count": len(raw_results),
            "exposed_characters": sum(len(result.snippet) for result in raw_results),
        },
    }


def _percentage(values: Iterable[bool]) -> float:
    checked = list(values)
    return round(100 * sum(bool(value) for value in checked) / len(checked), 1) if checked else 0.0


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _same_source_path(actual: str | None, expected: str) -> bool:
    if actual is None:
        return False
    actual_path = actual.replace("\\", "/")
    expected_path = expected.replace("\\", "/").lstrip("/")
    return actual_path == expected_path or actual_path.endswith(f"/{expected_path}")
