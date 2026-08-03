from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "experiments" / "semantic_retrieval.py"
_spec = importlib.util.spec_from_file_location("semantic_retrieval", _SCRIPT)
assert _spec and _spec.loader
semantic_retrieval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(semantic_retrieval)


def test_proxy_features_include_cjk_ngrams_and_latin_tokens() -> None:
    features = semantic_retrieval._features("统计实验 uses Redis")

    assert features["统计"] == 1
    assert features["实验"] == 1
    assert features["redis"] == 1


def test_proxy_ranks_page_with_more_question_features_first(tmp_path: Path) -> None:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    (pages / "generic.md").write_text(
        "# Notes\n\n系统记录了一些实验内容。\n",
        encoding="utf-8",
    )
    (pages / "target.md").write_text(
        "# 实验统计\n\n统计器读取 Manifest 和 RunMeasurement，避免模拟结果。\n",
        encoding="utf-8",
    )

    ranked = semantic_retrieval._rank_proxy_pages(
        tmp_path,
        "怎样核验实验统计不是模拟出来的",
        max_pages=1,
    )

    assert ranked == [pages / "target.md"]
