#!/usr/bin/env python3
"""Offline bake-off comparing lexical-only vs hybrid RRF recall on fixture data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from memoryforge.retrieval_models import RetrievalCandidate
from memoryforge.retrieval_v2 import retrieve_candidates


def _build_fixtures() -> tuple[list[dict], list[dict], list[dict]]:
    repo_id = "a" * 64
    source_id = "f" * 64

    wiki_facts = [
        {
            "page_path": "wiki/pages/auth/login.md",
            "source_id": source_id,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 验证流程",
            "quote": "LoginService.validate checks password hash with bcrypt",
            "routing_text": "LoginService validate password hash bcrypt authentication",
            "symbol": "auth.login.LoginService.validate",
            "relation_type": None,
            "repository_id": repo_id,
        },
        {
            "page_path": "wiki/pages/auth/login.md",
            "source_id": source_id,
            "source_version": 1,
            "locator": "chars:100-200",
            "section_path": "## 会话管理",
            "quote": "SessionManager.issue produces signed JWT tokens with 24h TTL",
            "routing_text": "SessionManager issue JWT token TTL session cookie",
            "symbol": "auth.session.SessionManager.issue",
            "relation_type": None,
            "repository_id": repo_id,
        },
        {
            "page_path": "wiki/pages/auth/session.md",
            "source_id": source_id,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 过期策略",
            "quote": "SessionManager.revoke invalidates tokens via Redis blacklist",
            "routing_text": "SessionManager revoke token Redis blacklist logout",
            "symbol": "auth.session.SessionManager.revoke",
            "relation_type": None,
            "repository_id": repo_id,
        },
        {
            "page_path": "wiki/pages/billing/invoice.md",
            "source_id": source_id,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 开票流程",
            "quote": "InvoiceService.generate renders PDF from order items",
            "routing_text": "InvoiceService generate PDF order billing invoice",
            "symbol": "billing.invoice.InvoiceService.generate",
            "relation_type": None,
            "repository_id": repo_id,
        },
        {
            "page_path": "wiki/pages/billing/payment.md",
            "source_id": source_id,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 支付网关",
            "quote": "PaymentProcessor.charge calls Stripe API via HTTP client",
            "routing_text": "PaymentProcessor charge Stripe HTTP payment gateway",
            "symbol": "billing.payment.PaymentProcessor.charge",
            "relation_type": None,
            "repository_id": repo_id,
        },
        {
            "page_path": "wiki/pages/search/index.md",
            "source_id": source_id,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 索引构建",
            "quote": "SearchEngine.build_index iterates documents and builds inverted list",
            "routing_text": "SearchEngine build index inverted document search indexer",
            "symbol": "search.engine.SearchEngine.build_index",
            "relation_type": None,
            "repository_id": repo_id,
        },
    ]

    symbols = [
        {
            "symbol_id": "sym-validate",
            "qualified_name": "auth.login.LoginService.validate",
            "repository_id": repo_id,
            "kind": "method",
            "path": "src/auth/login.py",
        },
        {
            "symbol_id": "sym-issue",
            "qualified_name": "auth.session.SessionManager.issue",
            "repository_id": repo_id,
            "kind": "method",
            "path": "src/auth/session.py",
        },
        {
            "symbol_id": "sym-revoke",
            "qualified_name": "auth.session.SessionManager.revoke",
            "repository_id": repo_id,
            "kind": "method",
            "path": "src/auth/session.py",
        },
        {
            "symbol_id": "sym-gen",
            "qualified_name": "billing.invoice.InvoiceService.generate",
            "repository_id": repo_id,
            "kind": "method",
            "path": "src/billing/invoice.py",
        },
        {
            "symbol_id": "sym-charge",
            "qualified_name": "billing.payment.PaymentProcessor.charge",
            "repository_id": repo_id,
            "kind": "method",
            "path": "src/billing/payment.py",
        },
        {
            "symbol_id": "sym-build",
            "qualified_name": "search.engine.SearchEngine.build_index",
            "repository_id": repo_id,
            "kind": "method",
            "path": "src/search/engine.py",
        },
    ]

    relations = [
        {
            "relation_id": "rel-1",
            "repository_id": repo_id,
            "type": "calls",
            "source_symbol_id": "sym-validate",
            "target_symbol_id": "sym-issue",
        },
        {
            "relation_id": "rel-2",
            "repository_id": repo_id,
            "type": "calls",
            "source_symbol_id": "sym-issue",
            "target_symbol_id": "sym-revoke",
        },
        {
            "relation_id": "rel-3",
            "repository_id": repo_id,
            "type": "calls",
            "source_symbol_id": "sym-gen",
            "target_symbol_id": "sym-charge",
        },
    ]

    return wiki_facts, symbols, relations


def _run_case(
    question: str,
    expected_sources: list[str],
    *,
    wiki_facts: list[dict],
    code_symbols: list[dict],
    code_relations: list[dict],
    repo_id: str,
    use_exact: bool = True,
) -> dict:
    visible = lambda s, v: True  # noqa: E731

    def _visible(source_id: str, source_version: int) -> bool:
        return True

    if use_exact:
        result = retrieve_candidates(
            Path("/tmp"),
            question,
            repository_id=repo_id,
            visible_source=_visible,
            max_pages=3,
            wiki_facts=wiki_facts,
            code_symbols=code_symbols,
            code_relations=code_relations,
        )
    else:
        filtered_facts = [dict(f) for f in wiki_facts]
        for f in filtered_facts:
            f["symbol"] = None
        result = retrieve_candidates(
            Path("/tmp"),
            question,
            repository_id=repo_id,
            visible_source=_visible,
            max_pages=3,
            wiki_facts=filtered_facts,
            code_symbols=[],
            code_relations=[],
        )

    found_pages = {c.page_path for c in result.candidates}
    found = all(es in found_pages for es in expected_sources)

    ranks: list[int] = []
    for idx, c in enumerate(result.candidates, start=1):
        if c.page_path in expected_sources:
            ranks.append(idx)
    mrr = sum(1.0 / r for r in ranks) / max(1, len(expected_sources)) if ranks else 0.0
    recall = sum(1 for es in expected_sources if es in found_pages) / len(expected_sources)

    return {
        "found": found,
        "recall_at_3": recall,
        "mrr_approx": round(mrr, 4),
        "candidate_count": len(result.candidates),
        "routes": list(result.routes),
        "semantic_status": result.semantic_status,
    }


def main() -> None:
    wiki_facts, code_symbols, code_relations = _build_fixtures()
    repo_id = "a" * 64

    cases = [
        (
            "How does LoginService.validate work?",
            ["wiki/pages/auth/login.md"],
        ),
        (
            "Explain session JWT tokens and SessionManager",
            ["wiki/pages/auth/login.md", "wiki/pages/auth/session.md"],
        ),
        (
            "Generate invoice PDF and payment with Stripe",
            ["wiki/pages/billing/invoice.md", "wiki/pages/billing/payment.md"],
        ),
        (
            "search inverted index engine build_index",
            ["wiki/pages/search/index.md"],
        ),
    ]

    hybrid_results = []
    lexical_results = []

    for question, expected in cases:
        hybrid = _run_case(
            question,
            expected,
            wiki_facts=wiki_facts,
            code_symbols=code_symbols,
            code_relations=code_relations,
            repo_id=repo_id,
            use_exact=True,
        )
        lexical = _run_case(
            question,
            expected,
            wiki_facts=wiki_facts,
            code_symbols=code_symbols,
            code_relations=code_relations,
            repo_id=repo_id,
            use_exact=False,
        )
        hybrid_results.append((question, hybrid))
        lexical_results.append((question, lexical))

    cases_data = []
    for (q, _), h, lx in zip(cases, hybrid_results, lexical_results):
        cases_data.append(
            {
                "question": q,
                "hybrid": h[1],
                "lexical_only": lx[1],
                "gain": {
                    "recall_delta": round(h[1]["recall_at_3"] - lx[1]["recall_at_3"], 4),
                    "mrr_delta": round(h[1]["mrr_approx"] - lx[1]["mrr_approx"], 4),
                },
            }
        )

    h_avg_recall = sum(r[1]["recall_at_3"] for r in hybrid_results) / len(hybrid_results)
    l_avg_recall = sum(r[1]["recall_at_3"] for r in lexical_results) / len(lexical_results)
    h_avg_mrr = sum(r[1]["mrr_approx"] for r in hybrid_results) / len(hybrid_results)
    l_avg_mrr = sum(r[1]["mrr_approx"] for r in lexical_results) / len(lexical_results)

    report = {
        "schema_version": 1,
        "experiment": "retrieval-v2-hybrid-bakeoff",
        "mode": "pure_memory_fixture",
        "case_count": len(cases),
        "summary": {
            "hybrid_recall_at_3": round(h_avg_recall, 4),
            "lexical_recall_at_3": round(l_avg_recall, 4),
            "recall_delta": round(h_avg_recall - l_avg_recall, 4),
            "hybrid_mrr": round(h_avg_mrr, 4),
            "lexical_mrr": round(l_avg_mrr, 4),
            "mrr_delta": round(h_avg_mrr - l_avg_mrr, 4),
        },
        "cases": cases_data,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
