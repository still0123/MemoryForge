from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from memoryforge.retrieval_models import RetrievalCandidate, RetrievalResult
from memoryforge.retrieval_v2 import retrieve_candidates

REPO_ID = "a" * 64
OTHER_REPO_ID = "b" * 64
SRC_A = "f" * 64
SRC_B = "e" * 64


def _make_facts() -> list[dict]:
    return [
        {
            "page_path": "wiki/pages/auth/login.md",
            "source_id": SRC_A,
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
            "page_path": "wiki/pages/auth/session.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 过期策略",
            "quote": "SessionManager.revoke invalidates tokens via Redis blacklist",
            "routing_text": "SessionManager revoke token Redis blacklist logout",
            "symbol": "auth.session.SessionManager.revoke",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/billing/invoice.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 开票流程",
            "quote": "InvoiceService.generate renders PDF from order items",
            "routing_text": "InvoiceService generate PDF order billing invoice",
            "symbol": "billing.invoice.InvoiceService.generate",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/billing/payment.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## 支付网关",
            "quote": "PaymentProcessor.charge calls Stripe API via HTTP client",
            "routing_text": "PaymentProcessor charge Stripe API HTTP payment gateway",
            "symbol": "billing.payment.PaymentProcessor.charge",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/otherrepo/secret.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "## Secret",
            "quote": "secret content from other repository",
            "routing_text": "LoginService validate secret invoice",
            "symbol": None,
            "relation_type": None,
            "repository_id": OTHER_REPO_ID,
        },
        {
            "page_path": "wiki/pages/auth/login.md",
            "source_id": SRC_B,
            "source_version": 2,
            "locator": "chars:200-300",
            "section_path": "## 其他",
            "quote": "extra login fact hidden source",
            "routing_text": "LoginService validate private hidden",
            "symbol": None,
            "relation_type": None,
            "repository_id": REPO_ID,
        },
    ]


def _make_symbols() -> list[dict]:
    return [
        {
            "symbol_id": "sym-login",
            "qualified_name": "auth.login.LoginService.validate",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/auth/login.py",
        },
        {
            "symbol_id": "sym-revoke",
            "qualified_name": "auth.session.SessionManager.revoke",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/auth/session.py",
        },
        {
            "symbol_id": "sym-invoice",
            "qualified_name": "billing.invoice.InvoiceService.generate",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/billing/invoice.py",
        },
        {
            "symbol_id": "sym-charge",
            "qualified_name": "billing.payment.PaymentProcessor.charge",
            "repository_id": REPO_ID,
            "kind": "method",
            "path": "src/billing/payment.py",
        },
    ]


def _make_relations() -> list[dict]:
    return [
        {
            "relation_id": "r1",
            "repository_id": REPO_ID,
            "type": "calls",
            "source_symbol_id": "sym-login",
            "target_symbol_id": "sym-revoke",
        },
        {
            "relation_id": "r2",
            "repository_id": REPO_ID,
            "type": "calls",
            "source_symbol_id": "sym-invoice",
            "target_symbol_id": "sym-charge",
        },
    ]


def _all_visible(source_id: str, source_version: int) -> bool:
    return True


def _only_src_a(source_id: str, source_version: int) -> bool:
    return source_id == SRC_A


def test_four_lanes_contribute_routes() -> None:
    facts = _make_facts()
    symbols = _make_symbols()
    relations = _make_relations()

    result = retrieve_candidates(
        Path("/tmp"),
        "How does auth.login.LoginService.validate work with bcrypt?",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=3,
        wiki_facts=facts,
        code_symbols=symbols,
        code_relations=relations,
    )

    assert isinstance(result, RetrievalResult)
    assert "exact" in result.routes
    assert "lexical" in result.routes


def test_workspace_route_prioritizes_fact_linking_two_named_repositories() -> None:
    facts = [
        {
            "page_path": "wiki/pages/alpha/client.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "Dependencies",
            "quote": "The client imports beta-mgr/api.",
            "routing_text": "Go import dependency",
            "symbol": None,
            "relation_type": "imports",
            "repository_id": REPO_ID,
            "repository_name": "alpha-mgr",
        },
        {
            "page_path": "wiki/pages/000-general.md",
            "source_id": SRC_B,
            "source_version": 1,
            "locator": "chars:0-100",
            "section_path": "Overview",
            "quote": "alpha-mgr and beta-mgr are services.",
            "routing_text": "service overview",
            "symbol": None,
            "relation_type": None,
            "repository_id": OTHER_REPO_ID,
            "repository_name": "docs",
        },
    ]

    result = retrieve_candidates(
        Path("/tmp"),
        "How does alpha-mgr call beta-mgr?",
        repository_id=None,
        visible_source=_all_visible,
        wiki_facts=facts,
    )

    assert "cross_repository" in result.routes
    assert result.candidates[0].page_path == "wiki/pages/alpha/client.md"


def test_rrf_dedupe_and_tiebreak_stable() -> None:
    facts = [
        {
            "page_path": "wiki/pages/a.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:1-10",
            "section_path": "",
            "quote": "alpha beta gamma",
            "routing_text": "alpha beta gamma",
            "symbol": None,
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/b.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:1-10",
            "section_path": "",
            "quote": "alpha beta gamma",
            "routing_text": "alpha beta gamma",
            "symbol": None,
            "relation_type": None,
            "repository_id": REPO_ID,
        },
    ]

    result1 = retrieve_candidates(
        Path("/tmp"),
        "alpha beta gamma",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=3,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )
    result2 = retrieve_candidates(
        Path("/tmp"),
        "alpha beta gamma",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=3,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )

    pages1 = [(c.page_path, c.source_id, c.locator) for c in result1.candidates]
    pages2 = [(c.page_path, c.source_id, c.locator) for c in result2.candidates]
    assert pages1 == pages2

    for i in range(len(result1.candidates) - 1):
        a = result1.candidates[i]
        b = result1.candidates[i + 1]
        if a.fused_score == b.fused_score:
            assert (a.page_path, a.source_id, a.locator) < (b.page_path, b.source_id, b.locator)


def test_visibility_filters_before_scoring() -> None:
    facts = _make_facts()

    result = retrieve_candidates(
        Path("/tmp"),
        "LoginService validate private hidden",
        repository_id=REPO_ID,
        visible_source=_only_src_a,
        max_pages=5,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )

    for cand in result.candidates:
        assert cand.source_id == SRC_A, f"invisible source leaked: {cand.source_id}"
    assert not any(c.source_id == SRC_B for c in result.candidates)


def test_semantic_unavailable_degrades_gracefully() -> None:
    facts = _make_facts()

    class FakeIndex:
        def available(self) -> bool:
            return False

        def search(self, text, k=20):
            raise RuntimeError("must not be called when available() is False")

    result = retrieve_candidates(
        Path("/tmp"),
        "bcrypt password validation",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=3,
        semantic_index=FakeIndex(),  # type: ignore[arg-type]
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )

    assert result.semantic_status == "unavailable"
    assert "semantic" not in result.routes
    assert len(result.candidates) >= 1


def test_semantic_none_is_disabled() -> None:
    facts = _make_facts()
    result = retrieve_candidates(
        Path("/tmp"),
        "bcrypt password validation",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=3,
        semantic_index=None,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )
    assert result.semantic_status == "disabled"


def test_repository_scope_isolation() -> None:
    facts = _make_facts()

    result = retrieve_candidates(
        Path("/tmp"),
        "LoginService validate invoice",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=5,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )

    for cand in result.candidates:
        assert cand.page_path != "wiki/pages/otherrepo/secret.md"


def test_byte_deterministic_two_runs() -> None:
    facts = _make_facts()
    symbols = _make_symbols()
    relations = _make_relations()

    r1 = retrieve_candidates(
        Path("/tmp"),
        "How does auth.login.LoginService.validate call SessionManager?",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=3,
        wiki_facts=facts,
        code_symbols=symbols,
        code_relations=relations,
    )
    r2 = retrieve_candidates(
        Path("/tmp"),
        "How does auth.login.LoginService.validate call SessionManager?",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=3,
        wiki_facts=facts,
        code_symbols=symbols,
        code_relations=relations,
    )

    b1 = pickle.dumps(r1)
    b2 = pickle.dumps(r2)
    assert b1 == b2

    assert [c.fused_score for c in r1.candidates] == [c.fused_score for c in r2.candidates]
    assert list(r1.routes) == list(r2.routes)
    assert r1.semantic_status == r2.semantic_status


def test_exact_full_identifier_boost() -> None:
    facts = [
        {
            "page_path": "wiki/pages/exact.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:0-50",
            "section_path": "## A",
            "quote": "Exact match FooService.do_bar with args",
            "routing_text": "FooService do_bar args exact",
            "symbol": "pkg.foo.FooService.do_bar",
            "relation_type": None,
            "repository_id": REPO_ID,
        },
        {
            "page_path": "wiki/pages/lexical.md",
            "source_id": SRC_A,
            "source_version": 1,
            "locator": "chars:0-50",
            "section_path": "## B",
            "quote": "FooService do bar lexical match with many terms exact args match",
            "routing_text": "FooService do bar lexical terms exact args match many",
            "symbol": None,
            "relation_type": None,
            "repository_id": REPO_ID,
        },
    ]

    result = retrieve_candidates(
        Path("/tmp"),
        "pkg.foo.FooService.do_bar exact args",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=2,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )

    exact_cand = next((c for c in result.candidates if c.page_path == "wiki/pages/exact.md"), None)
    lexical_cand = next((c for c in result.candidates if c.page_path == "wiki/pages/lexical.md"), None)

    assert exact_cand is not None
    assert exact_cand.exact_rank is not None
    if lexical_cand is not None and exact_cand.exact_rank and lexical_cand.lexical_rank:
        assert exact_cand.fused_score >= lexical_cand.fused_score - 1e-9


def test_relation_lane_expands_symbol_hits() -> None:
    facts = _make_facts()
    symbols = _make_symbols()
    relations = _make_relations()

    result = retrieve_candidates(
        Path("/tmp"),
        "auth.login.LoginService.validate revoke flow",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=4,
        wiki_facts=facts,
        code_symbols=symbols,
        code_relations=relations,
    )

    pages = {c.page_path for c in result.candidates}
    assert "wiki/pages/auth/session.md" in pages


def test_relation_lane_empty_without_code_snapshot() -> None:
    facts = _make_facts()

    result_no_snap = retrieve_candidates(
        Path("/tmp"),
        "auth.login.LoginService.validate revoke flow",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=4,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )
    assert "relation" not in result_no_snap.routes


def test_candidates_have_correct_type_tags() -> None:
    facts = _make_facts()
    result = retrieve_candidates(
        Path("/tmp"),
        "LoginService SessionManager auth login",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=5,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )
    for c in result.candidates:
        assert c.kind in {"page", "symbol", "relation"}
        if c.page_path in {"wiki/pages/auth/login.md", "wiki/pages/auth/session.md"}:
            assert c.kind == "symbol" or c.kind == "page"


def test_max_pages_limits_diversity_not_total_candidates() -> None:
    facts = _make_facts()
    result = retrieve_candidates(
        Path("/tmp"),
        "service validate token invoice PDF payment stripe",
        repository_id=REPO_ID,
        visible_source=_all_visible,
        max_pages=2,
        wiki_facts=facts,
        code_symbols=[],
        code_relations=[],
    )
    page_paths = {c.page_path for c in result.candidates}
    assert len(page_paths) <= 2
