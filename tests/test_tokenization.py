"""Golden tests locking the shared tokenization behavior (spec W1).

The expected values below are frozen from the pre-refactor inline
implementations (``_search_terms`` and ``retrieval_v2._tokenize``); any change
to these outputs must be a deliberate, re-frozen decision.
"""

from __future__ import annotations

from memoryforge.core.tokenization import bigram_tokens, index_terms, index_terms_text

CJK_SENTENCE = "我们讨论了缓存策略的调整方案"


def test_index_terms_cjk_unigrams_then_bigrams() -> None:
    assert index_terms("缓存设计") == [
        "缓",
        "存",
        "设",
        "计",
        "缓存",
        "存设",
        "设计",
    ]


def test_index_terms_continuous_run_has_no_run_token() -> None:
    # One continuous run: unigrams first, then overlapping bigrams, no whole-run term.
    terms = index_terms(CJK_SENTENCE)
    assert terms[: len(CJK_SENTENCE)] == list(CJK_SENTENCE)
    assert terms[len(CJK_SENTENCE) :] == [
        CJK_SENTENCE[i : i + 2] for i in range(len(CJK_SENTENCE) - 1)
    ]
    assert len(terms) == 2 * len(CJK_SENTENCE) - 1


def test_index_terms_latin_runs_are_lowercased() -> None:
    assert index_terms("FTS5 unicode61 tokenizer") == ["fts5", "unicode61", "tokenizer"]


def test_index_terms_mixed_content() -> None:
    assert index_terms("缓存策略Strategy") == [
        "缓",
        "存",
        "策",
        "略",
        "缓存",
        "存策",
        "策略",
        "strategy",
    ]


def test_index_terms_empty_and_punctuation_only() -> None:
    assert index_terms("") == []
    assert index_terms("，。！？///") == []


def test_index_terms_text_matches_column_format() -> None:
    assert index_terms_text(CJK_SENTENCE) == " ".join(index_terms(CJK_SENTENCE))


def test_bigram_tokens_cjk_only_bigrams() -> None:
    assert bigram_tokens(CJK_SENTENCE) == [
        CJK_SENTENCE[i : i + 2] for i in range(len(CJK_SENTENCE) - 1)
    ]


def test_bigram_tokens_single_cjk_char_and_latin() -> None:
    assert bigram_tokens("的 FTS5") == ["的", "fts5"]


def test_bigram_tokens_empty() -> None:
    assert bigram_tokens("") == []
