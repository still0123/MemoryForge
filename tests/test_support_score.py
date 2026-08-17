from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from memoryforge.compiler.wiki_facts import AppliedCodeSymbolMatch, CitationPayload
from memoryforge.query import query as query_module


def test_partial_evidence_contract_keeps_supported_hint_and_citation() -> None:
    citation = _citation("alpha-mgr imports beta-mgr/api.")
    support = {
        "score": 70.0,
        "threshold": 75.0,
        "sufficient": False,
        "enforced": True,
        "components": {
            "exact_identifier_coverage": 1.0,
            "core_term_coverage": 0.5,
            "fact_co_location": 1.0,
            "negation_alignment": 1.0,
            "multi_source_coverage": 1.0,
            "source_group_coverage": 1.0,
            "current_source_versions": 1.0,
        },
        "failed_hard_gates": ["runtime_call_not_verified"],
    }

    payload = query_module._unknown_payload(
        False,
        [],
        support=support,
        answer="alpha-mgr imports beta-mgr/api.",
        selected=[("wiki/pages/alpha/client.md", citation)],
    )

    assert payload["status"] == "unknown"
    assert payload["evidence_status"] == "partial"
    assert payload["supported_claims"] == ["alpha-mgr imports beta-mgr/api."]
    assert payload["unsupported_aspects"] == ["runtime_call_not_verified"]
    assert payload["citations"] == [citation]


def test_code_support_rejects_topic_only_evidence(tmp_path: Path) -> None:
    citation = _citation("`agent_loop.run_bash` (function): `def run_bash(command: str) -> str:`")

    support = query_module._support_score(
        tmp_path,
        "Which function stores agent embeddings in a vector database?",
        query_module._terms("Which function stores agent embeddings in a vector database?"),
        [("wiki/pages/code/repository/agent-loop.md", citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={"wiki/pages/code/repository/agent-loop.md"},
    )

    assert support["score"] < support["threshold"]
    assert not support["sufficient"]
    assert support["components"]["core_term_coverage"] < 0.2
    assert support["failed_hard_gates"] == ["score_below_threshold"]


def test_multi_part_question_enforces_support_threshold(tmp_path: Path) -> None:
    citation = _citation("MemoryForge 当前提供本地 Portal 控制面。")
    question = "MemoryForge 当前架构和未来商业收入分别是什么？"

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [("wiki/pages/readme.md", citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths=set(),
    )

    assert support["enforced"]
    assert not support["sufficient"]
    assert "score_below_threshold" in support["failed_hard_gates"]


def test_support_prefers_strong_identifier_over_product_name(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/multi.md"
    citation = _citation(
        "`multi.LockKey` (function): `func LockKey(key string, timeout time.Duration) bool`"
    )
    question = "ANAS DataFlow 并发重名如何通过 multi.LockKey 控制？"

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [(page_path, citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["components"]["exact_identifier_coverage"] == 1.0
    assert "exact_identifier_not_covered" not in support["failed_hard_gates"]


def test_quantity_support_requires_number_and_subject_in_the_same_fact(tmp_path: Path) -> None:
    question = "Lark Channel Bridge 会话里累计产出了多少个 MR？"
    version_citation = {
        **_citation("Codex CLI 已从 0.141.0 升级到 0.145.0。"),
        "section_path": "Assistant conclusions",
    }
    mr_citation = {
        **_citation("累计产出 45 个 MR。"),
        "section_path": "Assistant conclusions",
    }

    unsupported = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [("wiki/pages/bridge.md", version_citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths=set(),
    )
    supported_quantity = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [("wiki/pages/bridge.md", mr_citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths=set(),
    )

    assert "quantity_not_covered" in unsupported["failed_hard_gates"]
    assert "quantity_not_covered" not in supported_quantity["failed_hard_gates"]


def test_code_support_rejects_quantity_question_without_numeric_evidence(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/ap.md"
    citation = _citation(
        "`testcases.EFS.efs_mgr.ap.delete` (module): `module testcases.EFS.efs_mgr.ap.delete`"
    )
    question = "EFS AP IAM 的测试覆盖和线上缺陷率分别是多少？"

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [(page_path, citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
    )

    assert not support["sufficient"]
    assert "quantity_not_covered" in support["failed_hard_gates"]


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
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["score"] >= support["threshold"]
    assert support["sufficient"]
    assert support["failed_hard_gates"] == []


def test_conversation_support_also_enforces_score_threshold(tmp_path: Path) -> None:
    citation = {
        **_citation("DataFlow is a service."),
        "section_path": "Assistant conclusions",
    }

    support = query_module._support_score(
        tmp_path,
        "How is concurrent CreateDataFlow duplication prevented?",
        query_module._terms("How is concurrent CreateDataFlow duplication prevented?"),
        [("wiki/pages/dataflow.md", citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths=set(),
    )

    assert support["enforced"]
    assert not support["sufficient"]
    assert "score_below_threshold" in support["failed_hard_gates"]


def test_conversation_summary_supports_a_named_capability_question(tmp_path: Path) -> None:
    citation = {
        **_citation("MemoryForge 支持代码仓库和飞书文档导入、自动编译 Wiki，并提供知识问答。"),
        "section_path": "Assistant conclusions",
        "routing_text": "Codex 会话：MemoryForge Wiki 项目建设",
        "is_summary": True,
    }
    question = "MemoryForge 当前完成了哪些核心功能？"

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._expanded_question_terms(query_module._terms(question)),
        [("wiki/pages/memoryforge.md", citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths=set(),
    )

    assert support["sufficient"]
    assert support["components"]["core_term_coverage"] >= 0.75


def test_cleanup_skip_aligns_with_a_negative_cleanup_question(tmp_path: Path) -> None:
    citation = {
        **_citation("YAML 使用 stop_on_error；一旦中间步骤失败，后面的 delete 清理会被 skip。"),
        "section_path": "Assistant conclusions",
        "routing_text": "Codex 会话：查流水线失败原因",
    }
    question = "流水线失败后有没有自动清理？"

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._expanded_question_terms(query_module._terms(question)),
        [("wiki/pages/pipeline.md", citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths=set(),
    )

    assert support["sufficient"]
    assert support["components"]["negation_alignment"] == 1.0


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
        required_sources=1,
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
        required_sources=2,
        code_page_paths={page_path},
    )

    assert support["components"]["multi_source_coverage"] == 0.5
    assert "multi_source_incomplete" in support["failed_hard_gates"]


def test_required_source_groups_reject_three_sources_from_one_group(tmp_path: Path) -> None:
    citations = [
        (
            f"wiki/pages/feishu-{index}.md",
            {
                **_citation("Cache expiry is documented."),
                "source_id": source_id,
                "locator": f"chars:{index}-{index + 1}",
            },
        )
        for index, source_id in enumerate(("a" * 64, "b" * 64, "c" * 64), start=1)
    ]

    support = query_module._support_score(
        tmp_path,
        "How do cache expiry and session rollback correspond?",
        query_module._terms("How do cache expiry and session rollback correspond?"),
        citations,
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=2,
        required_source_groups=(
            frozenset({("a" * 64, 1), ("b" * 64, 1), ("c" * 64, 1)}),
            frozenset({("d" * 64, 2)}),
        ),
        code_page_paths=set(),
    )

    assert support["components"]["multi_source_coverage"] == 1.0
    assert support["components"]["source_group_coverage"] == 0.5
    assert "required_source_group_incomplete" in support["failed_hard_gates"]
    assert not support["sufficient"]


def test_required_source_groups_accept_one_citation_per_group(tmp_path: Path) -> None:
    feishu = {**_citation("Cache expires after sixty seconds."), "source_id": "a" * 64}
    conversation = {
        **_citation("Session rollback runs after cache expiry."),
        "source_id": "d" * 64,
        "source_version": 2,
        "locator": "chars:11-20",
    }

    support = query_module._support_score(
        tmp_path,
        "How do cache expiry and session rollback work?",
        query_module._terms("How do cache expiry and session rollback work?"),
        [("wiki/pages/feishu.md", feishu), ("wiki/pages/session.md", conversation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        required_source_groups=(
            frozenset({("a" * 64, 1)}),
            frozenset({("d" * 64, 2)}),
        ),
        code_page_paths=set(),
    )

    assert support["components"]["source_group_coverage"] == 1.0
    assert "required_source_group_incomplete" not in support["failed_hard_gates"]
    assert support["sufficient"]


def test_top_matches_prioritizes_an_uncovered_source_group() -> None:
    first = {**_citation("Cache expiry policy."), "source_id": "a" * 64}
    repeated = {
        **_citation("Cache expiry policy details."),
        "source_id": "a" * 64,
        "locator": "chars:11-20",
    }
    conversation = {
        **_citation("Cache expiry policy."),
        "source_id": "d" * 64,
        "source_version": 2,
    }

    selected = query_module._top_matches(
        [
            ((3,), "wiki/pages/feishu.md", first),
            ((2,), "wiki/pages/feishu.md", repeated),
            ((1,), "wiki/pages/session.md", conversation),
        ],
        2,
        question_terms={"cache", "expiry", "policy"},
        required_source_groups=(
            frozenset({("a" * 64, 1)}),
            frozenset({("d" * 64, 2)}),
        ),
    )

    assert [(citation["source_id"], citation["source_version"]) for _, citation in selected] == [
        ("a" * 64, 1),
        ("d" * 64, 2),
    ]


def test_merged_page_source_versions_cover_separate_groups(tmp_path: Path) -> None:
    first = {**_citation("Cache expiry policy."), "source_id": "a" * 64}
    second = {
        **_citation("Session rollback policy."),
        "source_id": "d" * 64,
        "source_version": 2,
        "locator": "chars:11-20",
    }

    support = query_module._support_score(
        tmp_path,
        "Cache expiry and session rollback policy",
        {"cache", "expiry", "session", "rollback", "policy"},
        [("wiki/pages/merged.md", first), ("wiki/pages/merged.md", second)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        required_source_groups=(
            frozenset({("a" * 64, 1)}),
            frozenset({("d" * 64, 2)}),
        ),
        code_page_paths=set(),
    )

    assert support["components"]["source_group_coverage"] == 1.0
    assert support["sufficient"]


def test_relationship_support_requires_both_sides_in_fact_bodies(tmp_path: Path) -> None:
    question = "学习手册的 AP 权限概念与 AI 会话中的 IAM 测试覆盖如何对应？"
    citations = [
        (
            "wiki/pages/guide.md",
            {
                **_citation("EFS 分为协议接入层、元数据面和数据面。"),
                "routing_text": "学习手册 AP 权限概念",
            },
        ),
        (
            "wiki/pages/session.md",
            {
                **_citation("两条链路使用的不是同一组 AP 和 FS。"),
                "routing_text": "AI 会话 IAM 测试覆盖",
                "locator": "chars:11-20",
            },
        ),
    ]

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        citations,
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths=set(),
    )

    assert not support["sufficient"]
    assert "relationship_side_not_covered" in support["failed_hard_gates"]


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
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["components"]["fact_co_location"] == 0.0
    assert "condition_not_co_located" in support["failed_hard_gates"]


def test_field_support_accepts_container_identifier_from_the_selected_page(
    tmp_path: Path,
) -> None:
    page_path = "wiki/pages/code/repository/cache.md"
    question = "Which CacheManager fields?"

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [(page_path, _citation("Field entries dict[str, str]"))],
        symbol_matches=(_symbol_match(page_path, "CacheManager"),),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
        code_page_identifiers={page_path: {"CacheManager"}},
    )

    assert support["components"]["exact_identifier_coverage"] == 1.0
    assert support["sufficient"]


def test_page_identifier_does_not_support_an_unrelated_selected_fact(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/cache.md"
    question = "What does DangerousFunction return?"

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [(page_path, _citation("SafeFunction returns a cached value."))],
        symbol_matches=(_symbol_match(page_path, "DangerousFunction"),),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["components"]["exact_identifier_coverage"] == 0.0
    assert "exact_identifier_not_covered" in support["failed_hard_gates"]


def test_module_context_identifier_is_not_an_answer_hard_gate(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/task.md"
    question = "Which class represents a task in s12_task_system.code?"
    citation: CitationPayload = {
        **_citation("`Task` (class): `class Task(BaseModel):`"),
        "routing_text": "Task represents a task in s12_task_system.code.",
    }

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [(page_path, citation)],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["components"]["exact_identifier_coverage"] == 1.0
    assert "exact_identifier_not_covered" not in support["failed_hard_gates"]
    assert support["sufficient"]


def test_explicit_identifier_requires_symbol_or_page_fact_coverage(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/cache.md"

    support = query_module._support_score(
        tmp_path,
        "Which MissingCacheManager manages cache entries?",
        query_module._terms("Which MissingCacheManager manages cache entries?"),
        [(page_path, _citation("CacheManager manages cache entries."))],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
        code_page_identifiers={page_path: {"CacheManager"}},
    )

    assert support["components"]["exact_identifier_coverage"] == 0.0
    assert "exact_identifier_not_covered" in support["failed_hard_gates"]


def test_explicit_identifier_does_not_match_a_camel_case_suffix(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/runner.md"

    support = query_module._support_score(
        tmp_path,
        "Which RunnerAdapter runs production?",
        query_module._terms("Which RunnerAdapter runs production?"),
        [(page_path, _citation("SkillUpRunnerAdapter runs production."))],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["components"]["exact_identifier_coverage"] == 0.0
    assert "exact_identifier_not_covered" in support["failed_hard_gates"]


def test_explicit_identifier_is_case_sensitive(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/runner.md"

    support = query_module._support_score(
        tmp_path,
        "What does `runnerAdapter` return?",
        query_module._terms("What does `runnerAdapter` return?"),
        [(page_path, _citation("RunnerAdapter returns a result."))],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["components"]["exact_identifier_coverage"] == 0.0
    assert "exact_identifier_not_covered" in support["failed_hard_gates"]


def test_all_explicit_identifiers_require_coverage(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/runner.md"

    support = query_module._support_score(
        tmp_path,
        "Which RunnerAdapter and CacheAdapter run production?",
        query_module._terms("Which RunnerAdapter and CacheAdapter run production?"),
        [(page_path, _citation("RunnerAdapter runs production."))],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["components"]["exact_identifier_coverage"] == 0.5
    assert "exact_identifier_not_covered" in support["failed_hard_gates"]


def test_support_does_not_truncate_explicit_identifier_coverage(tmp_path: Path) -> None:
    page_path = "wiki/pages/code/repository/adapters.md"
    identifiers = [
        "AdapterOne",
        "AdapterTwo",
        "AdapterThree",
        "AdapterFour",
        "AdapterFive",
        "AdapterSix",
        "AdapterSeven",
        "AdapterEight",
        "AdapterNine",
    ]
    covered = identifiers[:-1]
    question = "Which " + ", ".join(identifiers) + " run production?"

    support = query_module._support_score(
        tmp_path,
        question,
        query_module._terms(question),
        [(page_path, _citation(" ".join(covered) + " run production."))],
        symbol_matches=(),
        exact_symbol_fact_keys=set(),
        required_sources=1,
        code_page_paths={page_path},
    )

    assert support["components"]["exact_identifier_coverage"] == 0.8889
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
        required_sources=1,
        code_page_paths=set(),
    )

    assert runner._valid_support_payload(support, 75.0)
    assert not runner._valid_support_payload({}, 75.0)
    contradictory = {
        **support,
        "score": 100.0,
        "components": {key: 0.0 for key in support["components"]},
        "sufficient": True,
        "failed_hard_gates": [],
    }
    assert not runner._valid_support_payload(contradictory, 75.0)
    assert runner._valid_case_support(
        {"memoryforge": {"answer_status": "unknown", "support": None}},
        75.0,
    )
    assert not runner._valid_case_support(
        {"memoryforge": {"answer_status": "answered", "support": None}},
        75.0,
    )
    missing_score_gate = {
        **support,
        "score": 35.0,
        "components": {
            "exact_identifier_coverage": 0.0,
            "core_term_coverage": 0.0,
            "fact_co_location": 0.0,
            "negation_alignment": 1.0,
            "multi_source_coverage": 1.0,
            "source_group_coverage": 1.0,
            "current_source_versions": 1.0,
        },
        "sufficient": True,
        "enforced": True,
        "failed_hard_gates": [],
    }
    assert not runner._valid_case_support(
        {
            "category": "unanswerable",
            "question": "Which function stores embeddings?",
            "memoryforge": {
                "answer_status": "answered",
                "support": missing_score_gate,
            },
        },
        75.0,
    )


def test_agent_answer_requires_each_clause_in_one_citation() -> None:
    citations = [
        _citation("Cache entries expire after sixty seconds."),
        {
            **_citation("Administrators revoke active sessions."),
            "locator": "chars:11-20",
        },
    ]

    assert not query_module.answer_is_supported(
        "Cache entries revoke active sessions after sixty seconds.",
        citations,
    )
    assert query_module.answer_is_supported(
        "Cache entries expire after sixty seconds. Administrators revoke active sessions.",
        citations,
    )
    assert not query_module.answer_is_supported(
        "Active sessions revoke administrators.",
        [citations[1]],
    )
    assert not query_module.answer_is_supported(
        "Cache entries expire after sixty seconds and revoke active sessions.",
        citations,
    )
    assert not query_module.answer_is_supported(
        "check_permission",
        [_citation("check_deny_permission")],
    )
    assert not query_module.answer_is_supported(
        "Administrators clear caches.",
        [_citation("Administrators revoke sessions and users clear caches.")],
    )
    assert not query_module.answer_is_supported(
        "Cache",
        [_citation("CacheManager returns value.")],
    )
    assert not query_module.answer_is_supported(
        "Administrators clear caches.",
        [_citation("Administrators revoke sessions but users clear caches.")],
    )
    assert not query_module.answer_is_supported(
        "Administrators clear caches.",
        [_citation("Administrators revoke sessions, users clear caches.")],
    )


def test_support_benchmark_requires_an_external_output_path(tmp_path: Path) -> None:
    runner = _support_runner()
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(SystemExit, match="outside MemoryForge"):
        runner._require_external_output(repository / "evidence.json", repository)

    runner._require_external_output(tmp_path / "evidence.json", repository)


def test_support_benchmark_replay_includes_structural_evidence() -> None:
    runner = _support_runner()
    runs = [
        {"structural_sha256": "a", "evaluation_sha256": "b"},
        {"structural_sha256": "c", "evaluation_sha256": "b"},
    ]

    assert not runner._deterministic_replay(runs)


def _citation(quote: str) -> CitationPayload:
    return {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-10",
        "quote": quote,
    }


def _symbol_match(page_path: str, identifier: str) -> AppliedCodeSymbolMatch:
    return AppliedCodeSymbolMatch(
        fact_id="b" * 64,
        page_path=page_path,
        repository_id="c" * 64,
        source_id="a" * 64,
        source_version=1,
        locator="chars:21-30",
        section_path="Code: cache.py",
        quote=f"`cache.{identifier}` (class): `class {identifier}:`",
        routing_text="",
        symbol=f"cache.{identifier}",
        relation_type=None,
        identifier=identifier,
        match_kind="display_name",
    )


def _support_runner() -> ModuleType:
    script = Path(__file__).resolve().parent.parent / "demo/run_support_score_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_support_score_benchmark_test", script)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner
