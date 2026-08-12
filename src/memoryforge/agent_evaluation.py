"""Read-only real-provider evaluation for the bounded Wiki Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memoryforge.agent import run_agent
from memoryforge.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    _expected_sources,
    _percentage,
    _sources_recalled,
)
from memoryforge.provider import OpenAICompatibleProvider
from memoryforge.wiki_facts import CitationPayload
from memoryforge.workspace import Workspace, _connect_readonly


def run_agent_evaluation(
    workspace_root: Path,
    config_path: Path,
    provider: OpenAICompatibleProvider,
    *,
    max_steps: int = 4,
    max_pages: int = 3,
) -> dict[str, object]:
    """Run one frozen suite through the real Agent and aggregate its returned metrics."""
    suite = EvaluationSuite.model_validate_json(config_path.read_text(encoding="utf-8"))
    source_paths, source_repositories = _load_source_maps(workspace_root)
    cases = [
        _evaluate_agent_case(
            workspace_root,
            case,
            provider,
            source_paths,
            source_repositories,
            max_steps=max_steps,
            max_pages=max_pages,
        )
        for case in suite.cases
    ]
    answerable = [
        case for case, evaluation_case in zip(cases, suite.cases, strict=True)
        if evaluation_case.expected_status == "answered"
    ]
    unanswerable = [
        case for case, evaluation_case in zip(cases, suite.cases, strict=True)
        if evaluation_case.expected_status == "unknown"
    ]
    reason_counts: dict[str, int] = {}
    for case in cases:
        for reason, count in case["metrics"]["final_retry_reasons"].items():
            reason_counts[reason] = reason_counts.get(reason, 0) + count
    return {
        "suite": suite.name,
        "case_count": len(cases),
        "agent": {
            "answer_accuracy": _percentage(case["correct"] for case in cases),
            "source_recall": _percentage(case["source_recall"] for case in answerable),
            "abstention_accuracy": _percentage(case["correct"] for case in unanswerable),
            "max_steps_rate": _percentage(case["status"] == "max_steps" for case in cases),
            "provider_error_rate": _percentage(
                case["status"] == "provider_error" for case in cases
            ),
            "average_provider_calls": _average_metric(cases, "provider_calls"),
            "average_provider_latency_ms": _average_metric(cases, "provider_latency_ms"),
            "average_evidence_characters": _average(cases, "evidence_characters"),
            "average_tool_result_characters": _average(cases, "tool_result_characters"),
            "evidence_reuse_count": _sum_metric(cases, "evidence_reuse_count"),
            "tool_result_truncations": _sum_metric(cases, "tool_result_truncations"),
            "final_retry_reason_counts": reason_counts,
        },
        "cases": cases,
    }


def _evaluate_agent_case(
    workspace_root: Path,
    case: EvaluationCase,
    provider: OpenAICompatibleProvider,
    source_paths: dict[str, str],
    source_repositories: dict[str, str],
    *,
    max_steps: int,
    max_pages: int,
) -> dict[str, Any]:
    repository_ids = case.repository_ids or (
        (case.repository_id,) if case.repository_id is not None else ()
    )
    result = run_agent(
        workspace_root,
        case.question,
        provider=provider,
        max_steps=max_steps,
        max_pages=max_pages,
        repository_id=repository_ids[0] if len(repository_ids) == 1 else None,
    )
    actual_sources = _cited_sources(result["citations"], source_paths, source_repositories)
    expected_sources = _expected_sources(case.expected_source_paths, repository_ids)
    expected_source_recalled = _sources_recalled(
        actual_sources,
        expected_sources,
        require_all=True,
    )
    answer = result["answer"]
    terms_correct = all(
        term.casefold() in answer.casefold() for term in case.required_terms
    ) and all(
        term.casefold() not in answer.casefold() for term in case.forbidden_terms
    )
    if case.expected_status == "answered":
        correct = result["status"] == "answered" and terms_correct and expected_source_recalled
    else:
        correct = result["status"] == "unknown"
    return {
        "id": case.id,
        "category": case.category,
        "status": result["status"],
        "correct": correct,
        "source_recall": result["status"] == "answered" and expected_source_recalled,
        "cited_source_paths": sorted(path for _, path in actual_sources),
        "wiki_pages_read": result["wiki_pages_read"],
        "evidence_characters": result["evidence_characters"],
        "tool_result_characters": result["tool_result_characters"],
        "metrics": result["metrics"],
    }


def _cited_sources(
    citations: list[CitationPayload],
    source_paths: dict[str, str],
    source_repositories: dict[str, str],
) -> set[tuple[str | None, str]]:
    return {
        (source_repositories.get(citation["source_id"]), source_paths[citation["source_id"]])
        for citation in citations
        if citation["source_id"] in source_paths
    }


def _load_source_maps(workspace_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    opened = Workspace.open_readonly(workspace_root)
    with _connect_readonly(opened.index_path) as connection:
        source_rows = connection.execute(
            "SELECT source_id, source_path FROM sources"
        ).fetchall()
        repository_rows = connection.execute(
            """
            SELECT sources.source_id, revisions.repository_id
            FROM git_source_revisions AS revisions
            JOIN source_versions AS versions
              ON versions.id = revisions.source_version_id
            JOIN sources ON sources.id = versions.source_id
            WHERE versions.is_current = 1
            """
        ).fetchall()
    return (
        {str(row["source_id"]): str(row["source_path"]) for row in source_rows},
        {
            str(row["source_id"]): str(row["repository_id"])
            for row in repository_rows
        },
    )


def _average(cases: list[dict[str, Any]], field: str) -> float:
    if not cases:
        return 0.0
    return round(sum(float(case[field]) for case in cases) / len(cases), 2)


def _average_metric(cases: list[dict[str, Any]], field: str) -> float:
    if not cases:
        return 0.0
    return round(sum(float(case["metrics"][field]) for case in cases) / len(cases), 2)


def _sum_metric(cases: list[dict[str, Any]], field: str) -> int:
    return sum(case["metrics"][field] for case in cases)
