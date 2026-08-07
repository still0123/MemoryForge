from __future__ import annotations

import importlib.util
from pathlib import Path

from memoryforge import query as query_module
from memoryforge.wiki_facts import CitationPayload


def test_code_support_rejects_topic_only_evidence(tmp_path: Path) -> None:
    citation = _citation("`agent_loop.run_bash` (function): `def run_bash(command: str) -> str:`")

    support = query_module._support_score(
        tmp_path,
        "Which function stores agent embeddings in a vector database?",
        query_module._terms("Which function stores agent embeddings in a vector database?"),
        [("wiki/pages/code/repository/agent-loop.md", citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_citations=1,
        code_page_paths={"wiki/pages/code/repository/agent-loop.md"},
    )

    assert support["score"] < support["threshold"]
    assert not support["sufficient"]
    assert support["components"]["core_term_coverage"] < 0.2
    assert support["failed_hard_gates"] == ["score_below_threshold"]


def test_code_support_accepts_an_exact_signature(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/permission.md"
    citation = _citation(
        "`s03_permission.code.check_permission` (function): `def check_permission(block) -> bool:`"
    )

    support = query_module._support_score(
        tmp_path,
        "What does s03_permission.code.check_permission accept and return?",
        query_module._terms("What does s03_permission.code.check_permission accept and return?"),
        [(page_path, citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_citations=1,
        code_page_paths={page_path},
    )

    assert support["score"] >= support["threshold"]
    assert support["sufficient"]
    assert support["failed_hard_gates"] == []


def test_support_requires_aligned_negation(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/cache.md"
    citation = _citation("Cache entries expire after sixty seconds.")

    support = query_module._support_score(
        tmp_path,
        "When does the cache not expire?",
        query_module._terms("When does the cache not expire?"),
        [(page_path, citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_citations=1,
        code_page_paths={page_path},
    )

    assert not support["sufficient"]
    assert "negation_not_aligned" in support["failed_hard_gates"]


def test_multi_source_support_requires_distinct_sources(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/cache.md"
    first = _citation("Cache entries expire after sixty seconds.")
    second = {
        **_citation("Sessions are revoked when their cache entry expires."),
        "locator": "chars:11-20",
    }

    support = query_module._support_score(
        tmp_path,
        "How do cache expiry and session revocation work?",
        query_module._terms("How do cache expiry and session revocation work?"),
        [(page_path, first), (page_path, second)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_citations=2,
        code_page_paths={page_path},
    )

    assert support["components"]["multi_source_coverage"] == 0.5
    assert "multi_source_incomplete" in support["failed_hard_gates"]


def test_conditional_support_requires_one_fact_to_cover_both_clauses(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/cache.md"

    support = query_module._support_score(
        tmp_path,
        "When cache expires, are sessions revoked?",
        query_module._terms("When cache expires, are sessions revoked?"),
        [
            (page_path, _citation("Cache expires after sixty seconds.")),
            (
                page_path,
                {
                    **_citation("Sessions are revoked by an administrator."),
                    "locator": "chars:11-20",
                },
            ),
        ],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_citations=1,
        code_page_paths={page_path},
    )

    assert support["components"]["fact_co_location"] == 0.0
    assert "condition_not_co_located" in support["failed_hard_gates"]


def test_explicit_identifier_requires_symbol_or_page_fact_coverage(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/cache.md"

    support = query_module._support_score(
        tmp_path,
        "Which MissingCacheManager manages cache entries?",
        query_module._terms("Which MissingCacheManager manages cache entries?"),
        [(page_path, _citation("CacheManager manages cache entries."))],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_citations=1,
        code_page_paths={page_path},
        code_page_fact_terms={page_path: query_module._terms("real.CacheManager")},
    )

    assert support["components"]["exact_identifier_coverage"] == 0.0
    assert "exact_identifier_not_covered" in support["failed_hard_gates"]


def test_support_benchmark_validates_complete_and_optional_unknown_payloads() -> None:
    script = Path(__file__).resolve().parent.parent / "demo/run_support_score_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_support_score_benchmark", script)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    support = query_module._support_score(
        script.parent,
        "What does cache return?",
        query_module._terms("What does cache return?"),
        [("wiki/pages/cache.md", _citation("Cache returns a stored value."))],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_citations=1,
        code_page_paths=set(),
    )

    assert runner._valid_support_payload(support, 75.0)
    assert not runner._valid_support_payload({}, 75.0)
    assert runner._valid_case_support(
        {"memoryforge": {"answer_status": "unknown", "support": None}},
        75.0,
    )
    assert not runner._valid_case_support(
        {"memoryforge": {"answer_status": "answered", "support": None}},
        75.0,
    )


def _citation(quote: str) -> CitationPayload:
    return {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-10",
        "quote": quote,
    }
