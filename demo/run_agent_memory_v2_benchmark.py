#!/usr/bin/env python3
"""Runner for agent-memory-v2 hybrid retrieval fixture benchmark.

Reads registry + development/confirmation JSON, runs fixture-based
retrieve_candidates on every case, computes Recall@3 and MRR, and
writes the results JSON. Does not depend on a real workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from memoryforge.retrieval_v2 import retrieve_candidates

REGISTRY_PATH = REPO_ROOT / "demo/evaluation/agent_memory_v2_registry.json"
DEV_PATH = REPO_ROOT / "demo/evaluation/agent_memory_v2_development.json"
CONFIRM_PATH = REPO_ROOT / "demo/evaluation/agent_memory_v2_confirmation.json"
HOLDOUT_PATH = REPO_ROOT / "demo/evaluation/agent_memory_v2_holdout.json"


REPO_ID = "a" * 64
SOURCE_ID_PUBLIC = "f" * 64
SOURCE_ID_PRIVATE = "e" * 64


def _build_fixture_dataset() -> dict[str, Any]:
    wiki_facts = [
        {
            "page_path": "wiki/pages/auth/login.md",
            "source_id": SOURCE_ID_PUBLIC,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 验证流程",
            "quote": "LoginService.validate checks password hash with bcrypt",
            "routing_text": "LoginService validate password hash bcrypt authentication",
            "symbol": "auth.login.LoginService.validate",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/auth/login.md",
            "source_id": SOURCE_ID_PUBLIC,
            "source_version": 1,
            "locator": "chars:100-200",
            "section_path": "## 会话管理",
            "quote": "SessionManager.issue produces signed JWT tokens with 24h TTL",
            "routing_text": "SessionManager issue JWT token TTL session cookie 24 hours",
            "symbol": "auth.session.SessionManager.issue",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/auth/session.md",
            "source_id": SOURCE_ID_PUBLIC,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 过期策略",
            "quote": "SessionManager.revoke invalidates tokens via Redis blacklist",
            "routing_text": "SessionManager revoke token Redis blacklist logout invalidation",
            "symbol": "auth.session.SessionManager.revoke",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/billing/invoice.md",
            "source_id": SOURCE_ID_PUBLIC,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 开票流程",
            "quote": "InvoiceService.generate renders PDF from order items",
            "routing_text": "InvoiceService generate PDF order billing invoice render items",
            "symbol": "billing.invoice.InvoiceService.generate",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/billing/payment.md",
            "source_id": SOURCE_ID_PUBLIC,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 支付网关",
            "quote": "PaymentProcessor.charge calls Stripe API via HTTP client",
            "routing_text": "PaymentProcessor charge Stripe API HTTP payment gateway client",
            "symbol": "billing.payment.PaymentProcessor.charge",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/search/index.md",
            "source_id": SOURCE_ID_PUBLIC,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 索引构建",
            "quote": "SearchEngine.build_index iterates documents and builds inverted list",
            "routing_text": "SearchEngine build index inverted document iterate search engine",
            "symbol": "search.engine.SearchEngine.build_index",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
    ]

    code_symbols = [
        {
            "symbol_id": "sym-login-validate",
            "qualified_name": "auth.login.LoginService.validate",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/auth/login.py",
        },
        {
            "symbol_id": "sym-session-issue",
            "qualified_name": "auth.session.SessionManager.issue",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/auth/session.py",
        },
        {
            "symbol_id": "sym-session-revoke",
            "qualified_name": "auth.session.SessionManager.revoke",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/auth/session.py",
        },
        {
            "symbol_id": "sym-invoice-gen",
            "qualified_name": "billing.invoice.InvoiceService.generate",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/billing/invoice.py",
        },
        {
            "symbol_id": "sym-payment-charge",
            "qualified_name": "billing.payment.PaymentProcessor.charge",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/billing/payment.py",
        },
        {
            "symbol_id": "sym-search-build",
            "qualified_name": "search.engine.SearchEngine.build_index",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/search/engine.py",
        },
    ]

    code_relations = [
        {
            "relation_id": "rel-call-1",
            "repository_id": REPO_ID,
            "type": "calls",
            "source_symbol_id": "sym-login-validate",
            "target_symbol_id": "sym-session-issue",
        },
        {
            "relation_id": "rel-call-2",
            "repository_id": REPO_ID,
            "type": "calls",
            "source_symbol_id": "sym-session-issue",
            "target_symbol_id": "sym-session-revoke",
        },
        {
            "relation_id": "rel-call-3",
            "repository_id": REPO_ID,
            "type": "calls",
            "source_symbol_id": "sym-invoice-gen",
            "target_symbol_id": "sym-payment-charge",
        },
    ]

    return {
        "wiki_facts": wiki_facts,
        "code_symbols": code_symbols,
        "code_relations": code_relations,
    }


def _visible_public(source_id: str, source_version: int) -> bool:
    return source_id == SOURCE_ID_PUBLIC


def _run_cases(cases: list[dict[str, Any]], fixture: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["id"]
        question = case["question"]
        expected_status = case.get("expected_status", "answered")
        expected_sources = case.get("expected_sources", []) or []

        retrieval = retrieve_candidates(
            REPO_ROOT,
            question,
            repository_id=REPO_ID,
            visible_source=_visible_public,
            max_pages=3,
            wiki_facts=fixture["wiki_facts"],
            code_symbols=fixture["code_symbols"],
            code_relations=fixture["code_relations"],
        )

        candidate_pages = [c.page_path for c in retrieval.candidates]
        candidate_sources = [f"{c.source_id}|{c.source_version}|{c.locator}" for c in retrieval.candidates]

        if expected_status == "unanswerable":
            recall_at_3 = 1.0 if len(expected_sources) == 0 and len(candidate_pages) == 0 else 0.0
            if len(expected_sources) == 0:
                recall_at_3 = 1.0 if len(candidate_pages) == 0 else 0.5
            else:
                recall_at_3 = 0.0
            page_recall_at_3 = recall_at_3
            mrr = 1.0 if len(candidate_pages) == 0 else 0.0
            privacy_leaks = len(candidate_pages) > 0
        else:
            top_pages = candidate_pages[:3]
            hit_expected_pages = [p for p in expected_sources if p in top_pages]
            page_recall_at_3 = (
                len(hit_expected_pages) / len(expected_sources) if expected_sources else 1.0
            )
            recall_at_3 = page_recall_at_3

            ranks = []
            for idx, page in enumerate(candidate_pages, start=1):
                if page in expected_sources:
                    ranks.append(idx)
                    break
            mrr = (1.0 / ranks[0]) if ranks else 0.0

            privacy_leaks = any(
                c.source_id == SOURCE_ID_PRIVATE for c in retrieval.candidates
            )

        results.append(
            {
                "id": case_id,
                "case_type": case.get("case_type", "unknown"),
                "question": question,
                "expected_status": expected_status,
                "expected_sources": expected_sources,
                "retrieved_pages": candidate_pages,
                "retrieved_sources": candidate_sources,
                "routes_used": list(retrieval.routes),
                "semantic_status": retrieval.semantic_status,
                "page_recall_at_3": page_recall_at_3,
                "source_recall_at_3": recall_at_3,
                "reciprocal_rank": mrr,
                "privacy_leak_detected": bool(privacy_leaks),
            }
        )
    return results


def _aggregate(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(case_results) or 1
    page_recalls = [c["page_recall_at_3"] for c in case_results]
    source_recalls = [c["source_recall_at_3"] for c in case_results]
    rr = [c["reciprocal_rank"] for c in case_results]
    leak_count = sum(1 for c in case_results if c["privacy_leak_detected"])
    avg_page_recall = sum(page_recalls) / n
    avg_source_recall = sum(source_recalls) / n
    mrr = sum(rr) / n
    page_recall_at_3_pct = round(100.0 * avg_page_recall, 2)
    source_recall_at_3_pct = round(100.0 * avg_source_recall, 2)
    return {
        "case_count": len(case_results),
        "page_recall_at_3_pct": page_recall_at_3_pct,
        "source_recall_at_3_pct": source_recall_at_3_pct,
        "mean_reciprocal_rank": round(mrr, 4),
        "privacy_leak_count": leak_count,
    }


def _payload_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def run_benchmark(
    *,
    registry_path: Path = REGISTRY_PATH,
    dev_path: Path = DEV_PATH,
    confirm_path: Path = CONFIRM_PATH,
) -> dict[str, Any]:
    registry = cast(dict[str, Any], json.loads(registry_path.read_text(encoding="utf-8")))
    dev_cases_doc = cast(dict[str, Any], json.loads(dev_path.read_text(encoding="utf-8")))
    confirm_cases_doc = cast(dict[str, Any], json.loads(confirm_path.read_text(encoding="utf-8")))

    fixture = _build_fixture_dataset()

    dev_cases = cast(list[dict[str, Any]], dev_cases_doc["cases"])
    confirm_cases = cast(list[dict[str, Any]], confirm_cases_doc.get("cases", []))

    dev_results = _run_cases(dev_cases, fixture)
    confirm_results = _run_cases(confirm_cases, fixture) if confirm_cases else []

    dev_agg = _aggregate(dev_results)
    confirm_agg = _aggregate(confirm_results) if confirm_results else None

    combined_cases = dev_results + confirm_results
    combined_agg = _aggregate(combined_cases)

    evidence = {
        "schema_version": 1,
        "suite_id": registry.get("suite_id"),
        "suite_revision": registry.get("suite_revision"),
        "revision": registry.get("revision", 1),
        "runtime": {
            "backend": "pure_memory_fixture",
            "workspace_used": False,
            "fixture_fact_count": len(fixture["wiki_facts"]),
            "fixture_symbol_count": len(fixture["code_symbols"]),
            "fixture_relation_count": len(fixture["code_relations"]),
        },
        "splits": {
            "development": {
                "summary": dev_agg,
                "cases": dev_results,
            },
            "confirmation": {
                "summary": confirm_agg,
                "cases": confirm_results,
                "status": "not_run" if not confirm_results else "ran",
            },
            "holdout": {
                "status": "frozen_pending",
                "summary": None,
                "cases": [],
            },
        },
        "combined": combined_agg,
    }

    evidence["deterministic_hash"] = _payload_sha256(evidence)

    return evidence


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Write results JSON to this path")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--development", type=Path, default=DEV_PATH)
    parser.add_argument("--confirmation", type=Path, default=CONFIRM_PATH)
    args = parser.parse_args(argv)

    result = run_benchmark(
        registry_path=args.registry,
        dev_path=args.development,
        confirm_path=args.confirmation,
    )

    out_text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(out_text, encoding="utf-8")
        print(f"Wrote agent memory v2 benchmark to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out_text)


if __name__ == "__main__":
    main()
