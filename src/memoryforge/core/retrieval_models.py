from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

VisibleSource = Callable[[str, int], bool]


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    source_version: int


@dataclass(frozen=True)
class RetrievalCandidate:
    page_path: str
    source_id: str
    source_version: int
    locator: str
    kind: Literal["page", "symbol", "relation"]
    exact_rank: int | None
    lexical_rank: int | None
    semantic_rank: int | None
    relation_rank: int | None
    fused_score: float


@dataclass(frozen=True)
class RetrievalResult:
    candidates: tuple[RetrievalCandidate, ...]
    routes: tuple[str, ...]
    semantic_status: Literal["used", "disabled", "unavailable", "stale"]
