from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

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
    exact_rank: Optional[int]
    lexical_rank: Optional[int]
    semantic_rank: Optional[int]
    relation_rank: Optional[int]
    fused_score: float


@dataclass(frozen=True)
class RetrievalResult:
    candidates: tuple[RetrievalCandidate, ...]
    routes: tuple[str, ...]
    semantic_status: Literal["used", "disabled", "unavailable", "stale"]
