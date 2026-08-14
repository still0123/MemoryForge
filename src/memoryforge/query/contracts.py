from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from memoryforge.compiler.wiki_facts import CitationPayload


class EvidencePayload(CitationPayload):
    text: str


class SupportComponents(TypedDict):
    exact_identifier_coverage: float
    core_term_coverage: float
    fact_co_location: float
    negation_alignment: float
    multi_source_coverage: float
    current_source_versions: float


class SupportPayload(TypedDict):
    score: float
    threshold: float
    sufficient: bool
    enforced: bool
    components: SupportComponents
    failed_hard_gates: list[str]


class TraceStep(TypedDict):
    level: Literal["L0", "L1", "L2", "L3"]
    artifact: str


class AskPayload(TypedDict):
    status: Literal["answered", "unknown"]
    evidence_status: Literal["grounded", "partial", "no_local_evidence"]
    answer: str
    supported_claims: list[str]
    unsupported_aspects: list[str]
    citations: list[CitationPayload]
    wiki_pages: list[str]
    source_id: str | None
    source_version: int | None
    locator: str | None
    quote: str | None
    model_status: NotRequired[Literal["used", "fallback"]]
    trace: NotRequired[list[TraceStep]]
    evidence: NotRequired[list[EvidencePayload]]
    support: NotRequired[SupportPayload]
    _retrieval_debug: NotRequired[dict[str, Any]]
