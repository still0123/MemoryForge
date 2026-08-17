"""Deterministic query routing rules for progressive map-first retrieval."""

from __future__ import annotations

import re

from memoryforge.query.support import _explicit_code_identifiers

_CJK_GLOBAL_MARKERS = (
    "为什么",
    "整体",
    "全局",
    "架构",
    "全景",
    "工作原理",
    "如何协作",
    "怎么协作",
)
_ENGLISH_GLOBAL_MARKERS = (
    "architecture",
    "big picture",
    "end to end",
    "overall",
    "system design",
    "why",
)
_PUNCTUATION = re.compile(r"[^\w\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def is_global_question(question: str) -> bool:
    """Return whether a question should receive the navigation map first."""
    normalized = " ".join(_PUNCTUATION.sub(" ", question.casefold()).split())
    if not normalized or _explicit_code_identifiers(question):
        return False
    compact = normalized.replace(" ", "")
    if any(marker in compact for marker in _CJK_GLOBAL_MARKERS):
        return True
    padded = f" {normalized} "
    return any(f" {marker} " in padded for marker in _ENGLISH_GLOBAL_MARKERS)
