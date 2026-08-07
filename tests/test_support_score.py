from __future__ import annotations

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


def _citation(quote: str) -> CitationPayload:
    return {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-10",
        "quote": quote,
    }
