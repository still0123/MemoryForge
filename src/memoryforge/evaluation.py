"""Small, reproducible checks for an applied MemoryForge workspace."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memoryforge.manifests import SourceManifestStore
from memoryforge.query import answer_question
from memoryforge.workspace import (
    Workspace,
    list_current_git_source_versions,
    list_git_checkouts,
    read_source_excerpt,
    search_sources,
)

RepositoryId = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
SourceKey = tuple[str | None, str]
CaseCategory = Literal[
    "single_hop",
    "multi_source",
    "unanswerable",
    "paraphrase",
    "exact_symbol",
    "code_behavior",
    "temporal_update",
    "cross_repository",
]
ErrorClassification = Literal[
    "none",
    "page_route_miss",
    "fact_selection_miss",
    "insufficient_support",
    "citation_stale",
    "multi_source_incomplete",
    "repository_isolation_failure",
    "wrong_answer",
    "wrong_abstention",
]
_CODE_WIKI_FACT = re.compile(r"^`[^`]+` \([^)]+\): `(?P<code>.*)`$")


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    category: CaseCategory
    question: str = Field(min_length=1)
    expected_status: Literal["answered", "unknown"]
    expected_source_paths: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    repository_id: RepositoryId | None = None
    repository_ids: tuple[RepositoryId, ...] = ()

    @model_validator(mode="after")
    def validate_case(self) -> EvaluationCase:
        if self.expected_status == "answered" and not self.expected_source_paths:
            raise ValueError("answered evaluation cases require expected_source_paths")
        if self.expected_status == "answered" and not self.required_terms:
            raise ValueError("answered evaluation cases require required_terms")
        if _is_multi_source(self.category) and len(self.expected_source_paths) < 2:
            raise ValueError("multi_source evaluation cases require at least two sources")
        if self.category == "unanswerable" and self.expected_status != "unknown":
            raise ValueError("unanswerable evaluation cases must expect unknown")
        if self.repository_id is not None and self.repository_ids:
            raise ValueError("use repository_id or repository_ids, not both")
        if len(self.repository_ids) != len(set(self.repository_ids)):
            raise ValueError("repository_ids must not contain duplicates")
        if len(self.repository_ids) > 1:
            if not _is_multi_source(self.category):
                raise ValueError("multiple repository_ids require a multi_source case")
            if len(self.repository_ids) != len(self.expected_source_paths):
                raise ValueError("multiple repository_ids must align with expected_source_paths")
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
    current_git_sources = [
        (repository.repository_id, source)
        for repository in list_git_checkouts(workspace_root)
        for source in list_current_git_source_versions(workspace_root, repository.repository_id)
    ]
    source_repositories = {
        source.source_id: repository_id for repository_id, source in current_git_sources
    }
    current_source_versions = {
        source.source_id: source.source_version for _, source in current_git_sources
    }
    cases = [
        _evaluate_case(
            workspace_root,
            case,
            source_paths,
            source_repositories,
            current_source_versions,
        )
        for case in suite.cases
    ]
    total = len(cases)
    answerable = [case for case in cases if case["category"] != "unanswerable"]
    answered = [case for case in cases if case["memoryforge"]["answer_status"] == "answered"]
    selective_accuracy = _percentage(case["memoryforge"]["answer_correct"] for case in answered)
    coverage = round(100 * len(answered) / total, 1)
    classifications = {
        classification: sum(
            case["memoryforge"]["error_classification"] == classification for case in cases
        )
        for classification in sorted(
            {case["memoryforge"]["error_classification"] for case in cases}
        )
    }
    return {
        "suite": suite.name,
        "case_count": total,
        "memoryforge": {
            "answer_accuracy": _percentage(case["memoryforge"]["answer_correct"] for case in cases),
            "page_route_recall_at_3": _percentage(
                case["memoryforge"]["page_route_expected_sources_recalled"] for case in answerable
            ),
            "source_recall_at_3": _percentage(
                case["memoryforge"]["expected_sources_recalled"] for case in answerable
            ),
            "fact_source_recall": _percentage(
                case["memoryforge"]["expected_sources_recalled"] for case in answerable
            ),
            "fact_selection_accuracy": _percentage(
                case["memoryforge"]["fact_selection_correct"] for case in answerable
            ),
            "citation_grounding_accuracy": _percentage(
                case["memoryforge"]["citation_grounded"] for case in answerable
            ),
            "multi_source_coverage": _percentage(
                case["memoryforge"]["all_expected_sources_cited"]
                for case in cases
                if _is_multi_source(case["category"])
            ),
            "abstention_accuracy": _percentage(
                case["memoryforge"]["abstention_correct"]
                for case in cases
                if case["category"] == "unanswerable"
            ),
            "selective_accuracy": selective_accuracy,
            "coverage": coverage,
            "risk": round(100.0 - selective_accuracy, 1),
            "risk_coverage": [
                {
                    "threshold": 75.0,
                    "coverage": coverage,
                    "selective_accuracy": selective_accuracy,
                    "risk": round(100.0 - selective_accuracy, 1),
                }
            ],
            "repository_path_isolation_accuracy": _percentage(
                case["memoryforge"]["repository_path_isolated"]
                for case in cases
                if case["category"] != "unanswerable"
                and case["memoryforge"]["expected_repository_ids"]
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
            "error_classification_counts": classifications,
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
                if _is_multi_source(case["category"])
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
    source_repositories: dict[str, str],
    current_source_versions: dict[str, int],
) -> dict[str, Any]:
    citation_limit = len(case.expected_source_paths) if _is_multi_source(case.category) else 1
    repository_ids = case.repository_ids or (
        (case.repository_id,) if case.repository_id is not None else ()
    )
    repository_scope = repository_ids[0] if len(repository_ids) == 1 else None
    answer = answer_question(
        workspace_root,
        case.question,
        debug=True,
        max_citations=citation_limit,
        min_source_count=citation_limit,
        repository_id=repository_scope,
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
    actual_sources = {
        (
            source_repositories.get(citation["source_id"]),
            source_paths[citation["source_id"]],
        )
        for citation in citations
        if citation["source_id"] in source_paths
    }
    expected_sources = _expected_sources(case.expected_source_paths, repository_ids)
    trace = answer.get("trace", [])
    routed_wiki_pages = [
        step["artifact"]
        for step in trace
        if step["level"] == "L1" and step["artifact"].startswith("wiki/pages/")
    ]
    workspace = Workspace.open(workspace_root)
    routed_source_ids = {
        source_id
        for page_path in routed_wiki_pages
        for source_id in workspace.source_ids_for_page(page_path)
    }
    routed_sources = {
        (source_repositories.get(source_id), source_paths[source_id])
        for source_id in routed_source_ids
        if source_id in source_paths
    }
    require_all_sources = _is_multi_source(case.category)
    page_route_expected_sources_recalled = _sources_recalled(
        routed_sources,
        expected_sources,
        require_all=require_all_sources,
    )
    expected_sources_recalled = _sources_recalled(
        actual_sources,
        expected_sources,
        require_all=require_all_sources,
    )
    citation_grounded = bool(citations) and all(
        _citation_quote_grounded(citation["quote"], excerpt)
        for citation, excerpt in zip(citations, evidence, strict=True)
    )
    if case.expected_status == "unknown":
        citation_grounded = not citations and answer["status"] == "unknown"
    selected_fact_text = "\n".join(str(citation["quote"]) for citation in citations)
    fact_terms_correct = all(
        term.casefold() in selected_fact_text.casefold() for term in case.required_terms
    ) and all(term.casefold() not in selected_fact_text.casefold() for term in case.forbidden_terms)
    fact_selection_correct = expected_sources_recalled and fact_terms_correct
    answer_terms_correct = all(
        term.casefold() in answer_text.casefold() for term in case.required_terms
    ) and all(term.casefold() not in answer_text.casefold() for term in case.forbidden_terms)
    answer_correct = (
        answer["status"] == case.expected_status
        and expected_sources_recalled
        and answer_terms_correct
    )
    all_expected_sources_cited = _sources_recalled(
        actual_sources,
        expected_sources,
        require_all=True,
    )
    repository_path_isolated = _repository_paths_isolated(actual_sources, expected_sources)
    citations_current = all(
        current_source_versions.get(citation["source_id"], citation["source_version"])
        == citation["source_version"]
        for citation in citations
    )
    error_classification = _classify_error(
        case=case,
        answer_status=answer["status"],
        answer_correct=answer_correct,
        page_route_recalled=page_route_expected_sources_recalled,
        fact_selection_correct=fact_selection_correct,
        citation_grounded=citation_grounded,
        citations_current=citations_current,
        all_expected_sources_cited=all_expected_sources_cited,
        repository_path_isolated=repository_path_isolated,
    )
    raw_results = search_sources(
        workspace_root,
        case.question,
        limit=3,
        repository_id=repository_scope,
        require_all_terms=False,
    )
    raw_sources = {
        (source_repositories.get(result.source_id), result.source_path) for result in raw_results
    }
    raw_source_paths = {result.source_path for result in raw_results}
    raw_expected_source_in_top_3 = _sources_recalled(
        raw_sources,
        expected_sources,
        require_all=False,
    )
    raw_expected_sources_in_top_3 = _sources_recalled(
        raw_sources,
        expected_sources,
        require_all=True,
    )
    return {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "memoryforge": {
            "answer_status": answer["status"],
            "answer_correct": answer_correct,
            "page_route_expected_sources_recalled": page_route_expected_sources_recalled,
            "fact_selection_correct": fact_selection_correct,
            "citation_grounded": citation_grounded,
            "citations_current": citations_current,
            "expected_sources_recalled": expected_sources_recalled,
            "all_expected_sources_cited": all_expected_sources_cited,
            "repository_path_isolated": repository_path_isolated,
            "expected_repository_ids": sorted(repository_id for repository_id in repository_ids),
            "abstention_correct": answer["status"] == case.expected_status,
            "error_classification": error_classification,
            "support": answer.get("support"),
            "wiki_pages_read": sum(step["level"] == "L1" for step in trace),
            "raw_sources_read": sum(step["level"] == "L3" for step in trace),
            "evidence_characters": sum(len(excerpt) for excerpt in evidence),
            "wiki_pages": answer["wiki_pages"],
            "routed_wiki_pages": routed_wiki_pages,
            "cited_source_paths": sorted(path for _, path in actual_sources),
            "cited_sources": _serialise_sources(actual_sources),
            "routed_source_paths": sorted(path for _, path in routed_sources),
            "routed_sources": _serialise_sources(routed_sources),
        },
        "raw_fts_baseline": {
            "expected_source_in_top_3": raw_expected_source_in_top_3,
            "expected_sources_in_top_3": raw_expected_sources_in_top_3,
            "expected_sources_recalled": (
                raw_expected_sources_in_top_3
                if _is_multi_source(case.category)
                else raw_expected_source_in_top_3
            ),
            "raw_source_paths": sorted(raw_source_paths),
            "raw_sources": _serialise_sources(raw_sources),
            "expected_source_paths": sorted(path for _, path in expected_sources),
            "expected_sources": _serialise_sources(expected_sources),
            "result_count": len(raw_results),
            "exposed_characters": sum(len(result.snippet) for result in raw_results),
        },
    }


def _classify_error(
    *,
    case: EvaluationCase,
    answer_status: str,
    answer_correct: bool,
    page_route_recalled: bool,
    fact_selection_correct: bool,
    citation_grounded: bool,
    citations_current: bool,
    all_expected_sources_cited: bool,
    repository_path_isolated: bool,
) -> ErrorClassification:
    if answer_correct:
        return "none"
    if case.expected_status == "unknown":
        return "wrong_abstention"
    if not page_route_recalled:
        return "page_route_miss"
    if answer_status == "unknown":
        return "insufficient_support"
    if not citations_current:
        return "citation_stale"
    if not repository_path_isolated:
        return "repository_isolation_failure"
    if _is_multi_source(case.category) and not all_expected_sources_cited:
        return "multi_source_incomplete"
    if not fact_selection_correct or not citation_grounded:
        return "fact_selection_miss"
    return "wrong_answer"


def _is_multi_source(category: str) -> bool:
    return category in {"multi_source", "cross_repository"}


def _percentage(values: Iterable[bool]) -> float:
    checked = list(values)
    return round(100 * sum(bool(value) for value in checked) / len(checked), 1) if checked else 0.0


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _citation_quote_grounded(quote: str, excerpt: str) -> bool:
    normalised_excerpt = _normalise(excerpt)
    normalised_quote = _normalise(quote)
    if normalised_quote in normalised_excerpt:
        return True
    code_fact = _CODE_WIKI_FACT.fullmatch(normalised_quote)
    return bool(
        code_fact
        and _normalise(code_fact.group("code").replace("\\`", "`")) in normalised_excerpt
    )


def _expected_sources(
    source_paths: tuple[str, ...],
    repository_ids: tuple[str, ...],
) -> set[SourceKey]:
    paths = tuple(path.replace("\\", "/").lstrip("/") for path in source_paths)
    if not repository_ids:
        return {(None, path) for path in paths}
    if len(repository_ids) == 1:
        return {(repository_ids[0], path) for path in paths}
    return set(zip(repository_ids, paths, strict=True))


def _sources_recalled(
    actual_sources: set[SourceKey],
    expected_sources: set[SourceKey],
    *,
    require_all: bool,
) -> bool:
    if not expected_sources:
        return True
    matches = (
        any(_same_source(actual, expected) for actual in actual_sources)
        for expected in expected_sources
    )
    return all(matches) if require_all else any(matches)


def _repository_paths_isolated(
    actual_sources: set[SourceKey],
    expected_sources: set[SourceKey],
) -> bool:
    for actual_repository, actual_path in actual_sources:
        expected_repositories = {
            expected_repository
            for expected_repository, expected_path in expected_sources
            if _same_source_path(actual_path, expected_path) and expected_repository is not None
        }
        if expected_repositories and actual_repository not in expected_repositories:
            return False
    return True


def _same_source(actual: SourceKey, expected: SourceKey) -> bool:
    actual_repository, actual_path = actual
    expected_repository, expected_path = expected
    return (
        expected_repository is None or actual_repository == expected_repository
    ) and _same_source_path(actual_path, expected_path)


def _serialise_sources(sources: set[SourceKey]) -> list[dict[str, str | None]]:
    return [
        {"repository_id": repository_id, "source_path": source_path}
        for repository_id, source_path in sorted(
            sources, key=lambda source: (source[0] or "", source[1])
        )
    ]


def _same_source_path(actual: str | None, expected: str) -> bool:
    if actual is None:
        return False
    actual_path = actual.replace("\\", "/")
    expected_path = expected.replace("\\", "/").lstrip("/")
    return actual_path == expected_path or actual_path.endswith(f"/{expected_path}")
