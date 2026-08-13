from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from memoryforge.knowledge_conflicts import (
    ConflictKind,
    ConflictResolution,
    EvidenceRef,
    KnowledgeConflict,
    ProposalDraft,
    SCHEMA_SQL,
    detect_conflicts,
    resolve_conflict,
)
from memoryforge.models import (
    ChangeOrigin,
    Citation,
    Claim,
    ClaimStatus,
    RiskLevel,
)


SOURCE_ID_A = "a" * 64
SOURCE_ID_B = "b" * 64
CONTENT_SHA = "c" * 64


def _citation(source_id: str) -> Citation:
    return Citation(
        source_id=source_id,
        content_sha256=CONTENT_SHA,
        snapshot_uri=f"mf://blob/{CONTENT_SHA}",
        quote="q",
        quote_sha256=hashlib.sha256(b"q").hexdigest(),
        locator="chars:0-1",
    )


def _claim(claim_id: str, subject: str, predicate: str, obj: str, *, status: ClaimStatus = ClaimStatus.VERIFIED, source_id: str = SOURCE_ID_A) -> Claim:
    return Claim(
        claim_id=claim_id,
        subject=subject,
        predicate=predicate,
        object=obj,
        status=status,
        confidence=1.0,
        valid_from=datetime.utcnow(),
        citations=(_citation(source_id),),
    )


def _draft(page_path: str = "wiki/pages/p.md") -> ProposalDraft:
    return ProposalDraft(
        page_path=page_path,
        content="# Page\n\nbody",
        citations=(),
        origin=ChangeOrigin.AGENT_PROPOSAL,
        risk=RiskLevel.HIGH,
    )


def test_detect_conflicts_contradiction_stable_id():
    existing = [
        _claim("clm_L", "X", "equals", "1"),
        _claim("clm_R", "X", "equals", "2"),
    ]
    candidate = _draft()
    c1 = detect_conflicts(candidate=candidate, existing_claims=existing)
    c2 = detect_conflicts(candidate=candidate, existing_claims=existing)
    contradiction1 = [c for c in c1 if c.kind == ConflictKind.CONTRADICTION]
    contradiction2 = [c for c in c2 if c.kind == ConflictKind.CONTRADICTION]
    assert len(contradiction1) >= 1
    assert len(contradiction2) >= 1
    assert contradiction1[0].conflict_id == contradiction2[0].conflict_id


def test_detect_conflicts_free_text_no_conflict():
    existing = [
        _claim("clm_1", "X", "description", "Alpha version"),
        _claim("clm_2", "X", "description", "Beta release"),
    ]
    candidate = _draft()
    conflicts = detect_conflicts(candidate=candidate, existing_claims=existing)
    contradictions = [c for c in conflicts if c.kind == ConflictKind.CONTRADICTION]
    assert len(contradictions) == 0


def test_resolve_conflict_returns_proposal_not_db_update():
    conflict = KnowledgeConflict(
        conflict_id="c" * 24,
        page_path="wiki/pages/p.md",
        subject_key="X:equals",
        kind=ConflictKind.CONTRADICTION,
        left=EvidenceRef(claim_id="clm_L", source_id=SOURCE_ID_A, source_version=1, locator="chars:0-1"),
        right=EvidenceRef(claim_id="clm_R", source_id=SOURCE_ID_B, source_version=1, locator="chars:0-1"),
        detected_at=datetime.utcnow(),
    )
    citations = (
        EvidenceRef(claim_id="clm_L", source_id=SOURCE_ID_A, source_version=1, locator="chars:0-1"),
    )
    proposal = resolve_conflict(conflict, resolution=ConflictResolution.SUPERSEDE_LEFT, citations=citations)
    assert isinstance(proposal, ProposalDraft)
    assert proposal.page_path == "wiki/pages/p.md"
    assert proposal.risk == RiskLevel.HIGH
    assert "conflict_resolution" in proposal.reason_codes
    assert "resolution:supersede_left" in proposal.reason_codes
    assert conflict.resolved_by_changeset_id is None


def test_detect_conflicts_same_target_pending():
    p1 = ProposalDraft(
        page_path="wiki/pages/share.md",
        content="A",
        citations=(),
        origin=ChangeOrigin.LLM_COMPILATION,
        risk=RiskLevel.MODERATE,
    )
    p2 = ProposalDraft(
        page_path="wiki/pages/share.md",
        content="B",
        citations=(),
        origin=ChangeOrigin.LLM_COMPILATION,
        risk=RiskLevel.MODERATE,
    )
    candidate = _draft("wiki/pages/share.md")
    conflicts = detect_conflicts(
        candidate=candidate,
        existing_claims=[],
        pending=[p1, p2],
    )
    same_target = [c for c in conflicts if c.kind == ConflictKind.SAME_TARGET]
    assert len(same_target) >= 1


def test_schema_sql_contains_tables():
    joined = "\n".join(SCHEMA_SQL).lower()
    assert "knowledge_conflicts" in joined
    assert "conflict_resolution_audit" in joined
    assert "primary key" in joined
