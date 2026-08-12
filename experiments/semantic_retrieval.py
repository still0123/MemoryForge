#!/usr/bin/env python3
"""Compare current Wiki page recall with a dependency-free n-gram proxy.

This is an offline experiment, not a production search backend. It reads an already-built
workspace and an evaluation suite, ranks complete Wiki pages with character n-gram cosine
similarity, and reports recall against the current INDEX/FTS5 candidate path. It does not
write to the workspace, call a model, or include answer text in the result.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import shutil
from collections import Counter
from pathlib import Path

from memoryforge.evaluation import EvaluationCase, EvaluationSuite
from memoryforge.manifests import SourceManifestStore
from memoryforge.query import (
    _candidate_pages,
    _has_many_index_routes,
    _page_citations,
    _safe_wiki_page,
    _terms,
)
from memoryforge.workspace import find_applied_page_paths, search_wiki_facts

_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_CJK = re.compile(r"^[\u4e00-\u9fff]+$")


def run_experiment(
    workspace_root: Path,
    config_path: Path,
    *,
    max_pages: int = 3,
) -> dict[str, object]:
    """Return page-level recall metrics for the current path and the proxy."""
    if not 1 <= max_pages <= 10:
        raise ValueError("max_pages must be between 1 and 10")

    suite = EvaluationSuite.model_validate_json(config_path.read_text(encoding="utf-8"))
    source_paths = {
        manifest.source_id: manifest.source_path
        for manifest in SourceManifestStore(
            workspace_root / ".memoryforge/manifests/sources"
        ).list_all()
    }
    result_cases: list[dict[str, object]] = []
    answered_cases = [case for case in suite.cases if case.expected_status == "answered"]
    for case in suite.cases:
        baseline_pages = _current_pages(workspace_root, case, max_pages)
        bm25_pages = _rank_bm25_pages(
            workspace_root,
            case.question,
            max_pages=max_pages,
            repository_id=case.repository_id,
        )
        proxy_pages = _rank_proxy_pages(workspace_root, case.question, max_pages=max_pages)
        df_pages = _rank_df_pages(workspace_root, case.question, max_pages=max_pages)
        rrf_pages = _reciprocal_rank_fusion(
            (
                _current_pages(workspace_root, case, min(10, max_pages * 3)),
                _rank_bm25_pages(
                    workspace_root,
                    case.question,
                    max_pages=min(10, max_pages * 3),
                    repository_id=case.repository_id,
                ),
                _rank_df_pages(
                    workspace_root,
                    case.question,
                    max_pages=min(10, max_pages * 3),
                ),
            ),
            max_pages=max_pages,
        )
        if case.expected_status == "answered":
            expected = _normalise_expected_paths(case)
            baseline_sources = _page_source_paths(baseline_pages, source_paths)
            bm25_sources = _page_source_paths(bm25_pages, source_paths)
            proxy_sources = _page_source_paths(proxy_pages, source_paths)
            df_sources = _page_source_paths(df_pages, source_paths)
            rrf_sources = _page_source_paths(rrf_pages, source_paths)
            baseline_recalled = _recall(expected, baseline_sources, case)
            bm25_recalled = _recall(expected, bm25_sources, case)
            proxy_recalled = _recall(expected, proxy_sources, case)
            df_recalled = _recall(expected, df_sources, case)
            rrf_recalled = _recall(expected, rrf_sources, case)
        else:
            baseline_recalled = None
            bm25_recalled = None
            proxy_recalled = None
            df_recalled = None
            rrf_recalled = None
        result_cases.append(
            {
                "id": case.id,
                "category": case.category,
                "baseline_recalled": baseline_recalled,
                "bm25_recalled": bm25_recalled,
                "proxy_recalled": proxy_recalled,
                "df_recalled": df_recalled,
                "rrf_recalled": rrf_recalled,
                "baseline_page_count": len(baseline_pages),
                "proxy_page_count": len(proxy_pages),
            }
        )

    baseline_values = [
        bool(case["baseline_recalled"])
        for case in result_cases
        if case["baseline_recalled"] is not None
    ]
    proxy_values = [
        bool(case["proxy_recalled"]) for case in result_cases if case["proxy_recalled"] is not None
    ]
    bm25_values = [
        bool(case["bm25_recalled"]) for case in result_cases if case["bm25_recalled"] is not None
    ]
    df_values = [
        bool(case["df_recalled"]) for case in result_cases if case["df_recalled"] is not None
    ]
    rrf_values = [
        bool(case["rrf_recalled"]) for case in result_cases if case["rrf_recalled"] is not None
    ]
    baseline_paraphrase = _category_values(result_cases, "paraphrase", "baseline_recalled")
    proxy_paraphrase = _category_values(result_cases, "paraphrase", "proxy_recalled")
    baseline_average_pages = _average_pages(result_cases, "baseline_page_count")
    proxy_average_pages = _average_pages(result_cases, "proxy_page_count")
    baseline_recall = _percentage(baseline_values)
    bm25_recall = _percentage(bm25_values)
    proxy_recall = _percentage(proxy_values)
    df_recall = _percentage(df_values)
    rrf_recall = _percentage(rrf_values)
    rrf_gain = round(rrf_recall - baseline_recall, 1)
    rrf_regressions = sum(
        case["baseline_recalled"] is True and case["rrf_recalled"] is False for case in result_cases
    )
    eligible_for_integration = rrf_gain > 0 and rrf_regressions == 0

    return {
        "schema_version": 1,
        "experiment": "page-level-character-ngram-proxy",
        "suite": suite.name,
        "case_count": len(suite.cases),
        "answered_case_count": len(answered_cases),
        "max_pages": max_pages,
        "runtime": {
            "qmd_command": shutil.which("qmd") is not None,
            "sentence_transformers_module": importlib.util.find_spec("sentence_transformers")
            is not None,
            "true_embedding_backend_used": False,
        },
        "metric_definition": (
            "For answered cases, recall is true when the expected source path is represented "
            "by the top page candidates; multi_source cases require every expected source. "
            "Unanswerable cases are excluded from recall."
        ),
        "baseline": {
            "name": "current INDEX + FTS5 candidate path",
            "source_recall_at_3": baseline_recall,
            "paraphrase_source_recall_at_3": _percentage(baseline_paraphrase),
            "average_pages_ranked": baseline_average_pages,
        },
        "bm25": {
            "name": "SQLite FTS5 fact/source BM25",
            "source_recall_at_3": bm25_recall,
        },
        "proxy": {
            "name": "page-level character n-gram cosine proxy",
            "source_recall_at_3": proxy_recall,
            "paraphrase_source_recall_at_3": _percentage(proxy_paraphrase),
            "average_pages_ranked": proxy_average_pages,
        },
        "df_proxy": {
            "name": "binary query-term inverse document frequency",
            "source_recall_at_3": df_recall,
            "average_pages_ranked": proxy_average_pages,
        },
        "rrf": {
            "name": "local reciprocal-rank fusion over current, BM25, and DF rankings",
            "source_recall_at_3": rrf_recall,
            "regressions_against_current": rrf_regressions,
        },
        "decision": {
            "status": "candidate_for_integration" if eligible_for_integration else "keep_current",
            "recall_gain_points": rrf_gain,
            "reason": (
                "RRF improves recall with no per-case regression"
                if eligible_for_integration
                else "RRF has no net gain or regresses a current case; keep production unchanged"
            ),
        },
        "cases": result_cases,
    }


def _current_pages(workspace_root: Path, case: EvaluationCase, max_pages: int) -> list[Path]:
    question_terms = _terms(case.question)
    if not question_terms:
        return []
    trace: list[dict[str, str]] = []
    return _candidate_pages(
        workspace_root,
        case.question,
        question_terms,
        max_pages=max_pages,
        trace=trace,
        repository_id=case.repository_id,
        prefer_index_routes=_has_many_index_routes(workspace_root),
    )


def _rank_proxy_pages(workspace_root: Path, question: str, *, max_pages: int) -> list[Path]:
    """Rank complete Wiki pages with weighted token and CJK n-gram cosine similarity."""
    query_features = _features(question)
    if not query_features:
        return []
    pages = _wiki_pages(workspace_root)
    documents = [(page, _features(page.read_text(encoding="utf-8"))) for page in pages]
    document_frequency = Counter(feature for _, features in documents for feature in set(features))
    scored: list[tuple[float, str, Path]] = []
    for page, features in documents:
        score = _weighted_cosine(query_features, features, document_frequency, len(documents))
        if score > 0:
            scored.append((score, page.relative_to(workspace_root).as_posix(), page))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [page for _, _, page in scored[:max_pages]]


def _rank_bm25_pages(
    workspace_root: Path,
    question: str,
    *,
    max_pages: int,
    repository_id: str | None,
) -> list[Path]:
    paths = [
        result.page_path
        for result in search_wiki_facts(
            workspace_root,
            question,
            limit=max_pages,
            repository_id=repository_id,
        )
    ]
    paths.extend(
        find_applied_page_paths(
            workspace_root,
            question,
            limit=max_pages,
            repository_id=repository_id,
            require_all_terms=False,
        )
    )
    return [
        page
        for path in dict.fromkeys(paths)
        if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None
    ][:max_pages]


def _reciprocal_rank_fusion(
    rankings: tuple[list[Path], ...],
    *,
    max_pages: int,
    rank_constant: int = 60,
) -> list[Path]:
    scores: dict[Path, float] = {}
    for ranking in rankings:
        for rank, page in enumerate(dict.fromkeys(ranking), start=1):
            scores[page] = scores.get(page, 0.0) + 1.0 / (rank_constant + rank)
    return sorted(scores, key=lambda page: (-scores[page], str(page)))[:max_pages]


def _rank_df_pages(workspace_root: Path, question: str, *, max_pages: int) -> list[Path]:
    query_terms = _terms(question)
    pages = _wiki_pages(workspace_root)
    page_terms = [(page, _terms(page.read_text(encoding="utf-8"))) for page in pages]
    document_frequency = Counter(term for _, terms in page_terms for term in terms)
    scored = [
        (
            sum(
                math.log((len(pages) + 1) / (document_frequency[term] + 1)) + 1
                for term in query_terms & terms
            ),
            page.relative_to(workspace_root).as_posix(),
            page,
        )
        for page, terms in page_terms
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [page for score, _, page in scored[:max_pages] if score > 0]


def _wiki_pages(workspace_root: Path) -> list[Path]:
    pages_root = workspace_root / "wiki" / "pages"
    if not pages_root.is_dir() or pages_root.is_symlink():
        return []
    pages: list[Path] = []
    for page in sorted(pages_root.rglob("*.md")):
        safe_page = _safe_wiki_page(workspace_root, page)
        if safe_page is not None:
            pages.append(safe_page)
    return pages


def _features(text: str) -> Counter[str]:
    features: Counter[str] = Counter()
    for match in _TOKEN.finditer(text.casefold()):
        token = match.group()
        if _CJK.fullmatch(token):
            for size in (2, 3):
                ngrams = (token[index : index + size] for index in range(len(token) - size + 1))
                features.update(ngrams)
            if len(token) == 1:
                features[token] += 1
        else:
            features[token] += 1
    return features


def _weighted_cosine(
    left: Counter[str],
    right: Counter[str],
    document_frequency: Counter[str],
    document_count: int,
) -> float:
    shared = left.keys() & right.keys()
    if not shared:
        return 0.0
    left_norm = 0.0
    right_norm = 0.0
    dot = 0.0
    for feature in left.keys() | right.keys():
        inverse_frequency = math.log((document_count + 1) / (document_frequency[feature] + 1)) + 1
        left_weight = left[feature] * inverse_frequency
        right_weight = right[feature] * inverse_frequency
        left_norm += left_weight * left_weight
        right_norm += right_weight * right_weight
        dot += left_weight * right_weight
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def _page_source_paths(
    pages: list[Path],
    source_paths: dict[str, str],
) -> set[str]:
    actual: set[str] = set()
    for page in pages:
        for citation in _page_citations(page.read_text(encoding="utf-8")):
            source_path = source_paths.get(citation["source_id"])
            if source_path is not None:
                actual.add(source_path.replace("\\", "/").lstrip("/"))
    return actual


def _normalise_expected_paths(case: EvaluationCase) -> set[str]:
    return {path.replace("\\", "/").lstrip("/") for path in case.expected_source_paths}


def _recall(expected: set[str], actual: set[str], case: EvaluationCase) -> bool:
    if case.category == "multi_source":
        return expected <= actual
    return bool(expected & actual)


def _category_values(cases: list[dict[str, object]], category: str, key: str) -> list[bool]:
    return [bool(case[key]) for case in cases if case["category"] == category]


def _average_pages(cases: list[dict[str, object]], key: str) -> float:
    values = [int(case[key]) for case in cases if case["baseline_recalled"] is not None]
    return round(sum(values) / len(values), 2) if values else 0.0


def _percentage(values: list[bool]) -> float:
    return round(100 * sum(values) / len(values), 1) if values else 0.0


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    result = run_experiment(
        args.workspace.resolve(),
        args.eval_config.resolve(),
        max_pages=args.max_pages,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
        return
    args.output.resolve().write_text(rendered, encoding="utf-8")
    print(f"Wrote retrieval experiment to {args.output.resolve()}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--eval-config", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
