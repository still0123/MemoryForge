from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from memoryforge.semantic_index import SemanticIndex


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_available_detects_importable_backend(tmp_path: Path) -> None:
    idx = SemanticIndex(tmp_path, repository_id="a" * 64)

    try:
        import fastembed  # noqa: F401

        assert idx.available() is True
    except ImportError:
        try:
            import sentence_transformers  # noqa: F401

            assert idx.available() is True
        except ImportError:
            assert idx.available() is False


def test_build_stores_only_id_and_hash(tmp_path: Path) -> None:
    idx = SemanticIndex(tmp_path, repository_id="a" * 64)

    pages = [
        {
            "page_path": "wiki/pages/a.md",
            "content_hash": _sha("content of A"),
            "text": "raw content of A should not be retained as string",
        },
        {
            "page_path": "wiki/pages/b.md",
            "content_hash": _sha("content of B"),
            "text": "raw content of B should not be retained either",
        },
    ]
    symbols = [
        {
            "symbol_id": "sym-1",
            "qualified_name": "pkg.AClass.method",
        }
    ]

    idx.build(pages, symbols)

    assert set(idx.object_ids) == {
        "page:wiki/pages/a.md",
        "page:wiki/pages/b.md",
        "symbol:sym-1",
    }

    assert len(idx.object_hashes) == 3
    assert idx.object_hashes[0] == _sha("content of A")
    assert idx.object_hashes[1] == _sha("content of B")

    raw_text_parts = ["raw content of A", "raw content of B", "pkg.AClass.method"]
    for obj_id in idx.object_ids:
        for part in raw_text_parts:
            assert part not in obj_id
    assert "raw content" not in " ".join(idx.object_hashes)


def test_build_different_text_produces_different_index_id(tmp_path: Path) -> None:
    repo_id = "a" * 64
    idx1 = SemanticIndex(tmp_path / "a", repository_id=repo_id)
    idx2 = SemanticIndex(tmp_path / "b", repository_id=repo_id)

    pages_v1 = [
        {
            "page_path": "wiki/pages/x.md",
            "content_hash": "same-hash-declared",
            "text": "the actual content of page X version one",
        }
    ]
    pages_v2 = [
        {
            "page_path": "wiki/pages/x.md",
            "content_hash": "same-hash-declared",
            "text": "the actual content of page X version TWO DIFFERENT",
        }
    ]

    idx1.build(pages_v1, [])
    idx2.build(pages_v2, [])

    assert idx1.index_id is not None
    assert idx2.index_id is not None
    assert idx1.index_id != idx2.index_id


def test_build_same_content_same_index_id(tmp_path: Path) -> None:
    repo_id = "a" * 64
    idx1 = SemanticIndex(tmp_path / "a", repository_id=repo_id)
    idx2 = SemanticIndex(tmp_path / "b", repository_id=repo_id)

    pages = [
        {
            "page_path": "wiki/pages/x.md",
            "content_hash": "h1",
            "text": "identical content everywhere",
        },
        {
            "page_path": "wiki/pages/y.md",
            "content_hash": "h2",
            "text": "another page same across builds",
        },
    ]
    symbols = [
        {"symbol_id": "s1", "qualified_name": "foo.bar"}
    ]

    idx1.build(pages, symbols)
    idx2.build(pages, symbols)

    assert idx1.index_id == idx2.index_id


def test_search_returns_empty_when_backend_unavailable(tmp_path: Path) -> None:
    class AlwaysUnavailable(SemanticIndex):
        def available(self) -> bool:
            return False

    idx = AlwaysUnavailable(tmp_path, repository_id="a" * 64)
    idx.build(
        [{"page_path": "wiki/pages/a.md", "content_hash": "h1", "text": "hello"}],
        [],
    )
    hits = idx.search("hello", k=5)
    assert hits == []


def test_search_returns_ranked_list_when_built(tmp_path: Path) -> None:
    idx = SemanticIndex(tmp_path, repository_id="a" * 64)
    pages = [
        {"page_path": "wiki/pages/a.md", "content_hash": "ha", "text": "apple banana cherry date"},
        {"page_path": "wiki/pages/b.md", "content_hash": "hb", "text": "zebra yak walrus vole"},
    ]
    idx.build(pages, [])

    hits = idx.search("apple banana", k=3)
    assert isinstance(hits, list)
    assert len(hits) <= 3
    if hits:
        prev_score = float("inf")
        for obj_id, score in hits:
            assert isinstance(obj_id, str)
            assert isinstance(score, float)
            assert score <= prev_score + 1e-9
            prev_score = score


def test_search_before_build_is_empty(tmp_path: Path) -> None:
    idx = SemanticIndex(tmp_path, repository_id="a" * 64)
    assert idx.search("any query", k=5) == []
    assert idx.index_id is None


def test_object_count_matches_build_input(tmp_path: Path) -> None:
    idx = SemanticIndex(tmp_path, repository_id="a" * 64)
    pages = [
        {"page_path": f"wiki/pages/p{i}.md", "content_hash": f"h{i}", "text": f"content {i}"}
        for i in range(4)
    ]
    symbols = [
        {"symbol_id": f"s{i}", "qualified_name": f"pkg.f{i}"}
        for i in range(3)
    ]
    idx.build(pages, symbols)
    assert len(idx.object_ids) == 4 + 3
    assert len(idx.object_hashes) == 4 + 3
