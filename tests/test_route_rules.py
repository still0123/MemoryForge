from __future__ import annotations

import pytest

from memoryforge.query.route_rules import is_global_question


@pytest.mark.parametrize(
    "question",
    [
        "这个项目为什么这样设计？",
        "整体架构是什么？",
        "Give me the big picture.",
        "How does the system work overall?",
    ],
)
def test_global_questions_route_to_map(question: str) -> None:
    assert is_global_question(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "为什么 `CacheStore` 使用这个架构？",
        "How does cache_store work overall?",
        "Explain pkg.cache.Store architecture.",
    ],
)
def test_global_words_with_explicit_symbol_keep_exact_route(question: str) -> None:
    assert is_global_question(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "CacheStore",
        "pkg.cache.Store",
        "cache_store",
    ],
)
def test_symbol_questions_are_not_global(question: str) -> None:
    assert is_global_question(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "缓存多久过期？",
        "Where is the configuration file?",
        "飞书登录步骤是什么？",
    ],
)
def test_normal_questions_are_not_global(question: str) -> None:
    assert is_global_question(question) is False
