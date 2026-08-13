"""Knowledge conflict detection and resolution proposal generation."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from memoryforge.models import Claim, ClaimStatus, ChangeOrigin, RiskLevel


class ProposalDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_path: str = Field(min_length=1)
    content: str = Field(min_length=1)
    citations: tuple[Any, ...] = ()
    origin: ChangeOrigin = ChangeOrigin.AGENT_PROPOSAL
    risk: RiskLevel = RiskLevel.HIGH
    reason_codes: tuple[str, ...] = ()


class ConflictKind(StrEnum):
    CONTRADICTION = "contradiction"
    SAME_TARGET = "same_target"
    STALE_PROPOSAL = "stale_proposal"


class ConflictResolution(StrEnum):
    OPEN = "open"
    SUPERSEDE_LEFT = "supersede_left"
    SUPERSEDE_RIGHT = "supersede_right"
    RECONCILED = "reconciled"
    DISMISSED = "dismissed"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: int = Field(ge=1)
    locator: str = Field(min_length=1)


class KnowledgeConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_id: str = Field(min_length=1)
    repository_id: str | None = None
    page_path: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    kind: ConflictKind
    left: EvidenceRef
    right: EvidenceRef
    detected_at: datetime
    resolution: ConflictResolution = ConflictResolution.OPEN
    resolved_by_changeset_id: str | None = None


SCHEMA_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS knowledge_conflicts (
        conflict_id TEXT PRIMARY KEY,
        repository_id TEXT,
        page_path TEXT NOT NULL,
        subject_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        left_claim_id TEXT NOT NULL,
        left_source_id TEXT NOT NULL,
        left_source_version INTEGER NOT NULL,
        left_locator TEXT NOT NULL,
        right_claim_id TEXT NOT NULL,
        right_source_id TEXT NOT NULL,
        right_source_version INTEGER NOT NULL,
        right_locator TEXT NOT NULL,
        detected_at TEXT NOT NULL,
        resolution TEXT NOT NULL DEFAULT 'open',
        resolved_by_changeset_id TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_knowledge_conflicts_page_resolution
        ON knowledge_conflicts (page_path, resolution);
    """,
    """
    CREATE TABLE IF NOT EXISTS conflict_resolution_audit (
        audit_id TEXT PRIMARY KEY,
        conflict_id TEXT NOT NULL,
        old_resolution TEXT NOT NULL,
        new_resolution TEXT NOT NULL,
        actor TEXT NOT NULL,
        at TEXT NOT NULL,
        citations_json TEXT NOT NULL
    );
    """,
)


def _make_conflict_id(
    page_path: str,
    subject_key: str,
    kind: ConflictKind,
    left_claim_id: str,
    right_claim_id: str,
) -> str:
    raw = f"{page_path}\0{subject_key}\0{kind.value}\0{left_claim_id}\0{right_claim_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _claim_to_evidence_ref(claim: Claim) -> EvidenceRef | None:
    if not claim.citations:
        return None
    cit = claim.citations[0]
    return EvidenceRef(
        claim_id=claim.claim_id,
        source_id=cit.source_id,
        source_version=1,
        locator=cit.locator,
    )


def _is_scalar_predicate(predicate: str) -> bool:
    return predicate in {
        "is",
        "equals",
        "has_value",
        "version",
        "type",
        "returns",
    }


def _normalize_scalar(value: str) -> str:
    return value.strip().lower()


def _canonical_subject_key(page_path: str, subject: str, predicate: str) -> str:
    return f"{page_path}:{subject}:{predicate}"


def detect_conflicts(
    *,
    candidate: ProposalDraft,
    existing_claims: Iterable[Claim],
    pending: Iterable[ProposalDraft] = (),
) -> tuple[KnowledgeConflict, ...]:
    conflicts: list[KnowledgeConflict] = []
    existing_list = list(existing_claims)
    pending_list = list(pending)
    now = datetime.now().astimezone()

    claims_by_key: dict[str, list[Claim]] = {}
    for claim in existing_list:
        if _is_scalar_predicate(claim.predicate):
            key = _canonical_subject_key(candidate.page_path, claim.subject, claim.predicate)
            claims_by_key.setdefault(key, []).append(claim)

    for claim in existing_list:
        if not _is_scalar_predicate(claim.predicate):
            continue
        if not claim.citations:
            continue
        key = _canonical_subject_key(candidate.page_path, claim.subject, claim.predicate)
        other_claims = claims_by_key.get(key, [])
        for other in other_claims:
            if other.claim_id == claim.claim_id:
                continue
            if not other.citations:
                continue
            left_val = _normalize_scalar(claim.object)
            right_val = _normalize_scalar(other.object)
            if left_val == right_val:
                continue
            left_eref = _claim_to_evidence_ref(claim)
            right_eref = _claim_to_evidence_ref(other)
            if left_eref is None or right_eref is None:
                continue
            subject_key = f"{claim.subject}:{claim.predicate}"
            cid = _make_conflict_id(
                candidate.page_path, subject_key, ConflictKind.CONTRADICTION,
                claim.claim_id, other.claim_id,
            )
            conflicts.append(
                KnowledgeConflict(
                    conflict_id=cid,
                    page_path=candidate.page_path,
                    subject_key=subject_key,
                    kind=ConflictKind.CONTRADICTION,
                    left=left_eref,
                    right=right_eref,
                    detected_at=now,
                )
            )

    for i, p1 in enumerate(pending_list + [candidate]):
        for p2 in pending_list + [candidate]:
            if p1 is p2:
                continue
            if p1.page_path != p2.page_path:
                continue
            left_eref = EvidenceRef(
                claim_id="pending_left",
                source_id="pending",
                source_version=1,
                locator="chars:0-1",
            )
            right_eref = EvidenceRef(
                claim_id="pending_right",
                source_id="pending",
                source_version=1,
                locator="chars:0-1",
            )
            subject_key = "same_page_pending"
            cid = _make_conflict_id(
                p1.page_path, subject_key, ConflictKind.SAME_TARGET,
                left_eref.claim_id, right_eref.claim_id,
            )
            already = any(c.conflict_id == cid for c in conflicts)
            if not already:
                conflicts.append(
                    KnowledgeConflict(
                        conflict_id=cid,
                        page_path=p1.page_path,
                        subject_key=subject_key,
                        kind=ConflictKind.SAME_TARGET,
                        left=left_eref,
                        right=right_eref,
                        detected_at=now,
                    )
                )

    superseded_sources: set[tuple[str, int]] = set()
    for claim in existing_list:
        if claim.status == ClaimStatus.SUPERSEDED:
            for cit in claim.citations:
                superseded_sources.add((cit.source_id, 0))

    if superseded_sources:
        for src_id, _ in superseded_sources:
            left_eref = EvidenceRef(
                claim_id="superseded_claim",
                source_id=src_id,
                source_version=1,
                locator="chars:0-1",
            )
            right_eref = EvidenceRef(
                claim_id="candidate_proposal",
                source_id=src_id,
                source_version=1,
                locator="chars:0-1",
            )
            subject_key = f"stale:{src_id}"
            cid = _make_conflict_id(
                candidate.page_path, subject_key, ConflictKind.STALE_PROPOSAL,
                left_eref.claim_id, right_eref.claim_id,
            )
            already = any(c.conflict_id == cid for c in conflicts)
            if not already:
                conflicts.append(
                    KnowledgeConflict(
                        conflict_id=cid,
                        page_path=candidate.page_path,
                        subject_key=subject_key,
                        kind=ConflictKind.STALE_PROPOSAL,
                        left=left_eref,
                        right=right_eref,
                        detected_at=now,
                    )
                )

    conflicts.sort(key=lambda c: (c.conflict_id,))
    return tuple(conflicts)


def persist_conflicts(connection: Any, conflicts: Iterable[KnowledgeConflict]) -> None:
    """Persist detected conflicts without overwriting an existing resolution."""
    for conflict in conflicts:
        connection.execute(
            """
            INSERT INTO knowledge_conflicts (
                conflict_id, repository_id, page_path, subject_key, kind,
                left_claim_id, left_source_id, left_source_version, left_locator,
                right_claim_id, right_source_id, right_source_version, right_locator,
                detected_at, resolution, resolved_by_changeset_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conflict_id) DO NOTHING
            """,
            (
                conflict.conflict_id,
                conflict.repository_id,
                conflict.page_path,
                conflict.subject_key,
                conflict.kind.value,
                conflict.left.claim_id,
                conflict.left.source_id,
                conflict.left.source_version,
                conflict.left.locator,
                conflict.right.claim_id,
                conflict.right.source_id,
                conflict.right.source_version,
                conflict.right.locator,
                conflict.detected_at.isoformat(),
                conflict.resolution.value,
                conflict.resolved_by_changeset_id,
            ),
        )
    connection.commit()


def conflict_from_row(row: Mapping[str, Any]) -> KnowledgeConflict:
    return KnowledgeConflict(
        conflict_id=str(row["conflict_id"]),
        repository_id=(str(row["repository_id"]) if row["repository_id"] is not None else None),
        page_path=str(row["page_path"]),
        subject_key=str(row["subject_key"]),
        kind=ConflictKind(str(row["kind"])),
        left=EvidenceRef(
            claim_id=str(row["left_claim_id"]),
            source_id=str(row["left_source_id"]),
            source_version=int(row["left_source_version"]),
            locator=str(row["left_locator"]),
        ),
        right=EvidenceRef(
            claim_id=str(row["right_claim_id"]),
            source_id=str(row["right_source_id"]),
            source_version=int(row["right_source_version"]),
            locator=str(row["right_locator"]),
        ),
        detected_at=datetime.fromisoformat(str(row["detected_at"])),
        resolution=ConflictResolution(str(row["resolution"])),
        resolved_by_changeset_id=(
            str(row["resolved_by_changeset_id"])
            if row["resolved_by_changeset_id"] is not None
            else None
        ),
    )


def resolve_conflict(
    conflict: KnowledgeConflict,
    *,
    resolution: ConflictResolution,
    citations: tuple[EvidenceRef, ...],
) -> ProposalDraft:
    resolution_title = f"Conflict resolution: {conflict.conflict_id}"
    left_cite = f"- [{conflict.left.claim_id}] {conflict.left.source_id}@{conflict.left.source_version}:{conflict.left.locator}"
    right_cite = f"- [{conflict.right.claim_id}] {conflict.right.source_id}@{conflict.right.source_version}:{conflict.right.locator}"
    additional_cites = "\n".join(
        f"- [{c.claim_id}] {c.source_id}@{c.source_version}:{c.locator}" for c in citations
    )
    body_parts = [
        f"# {resolution_title}",
        "",
        f"- conflict_id: {conflict.conflict_id}",
        f"- resolution: {resolution.value}",
        f"- kind: {conflict.kind.value}",
        f"- page_path: {conflict.page_path}",
        f"- subject_key: {conflict.subject_key}",
        "",
        "## Evidence",
        left_cite,
        right_cite,
    ]
    if additional_cites:
        body_parts.append(additional_cites)
    content = "\n".join(body_parts)

    return ProposalDraft(
        page_path=conflict.page_path,
        content=content,
        citations=tuple(citations),
        origin=ChangeOrigin.AGENT_PROPOSAL,
        risk=RiskLevel.HIGH,
        reason_codes=("conflict_resolution", f"resolution:{resolution.value}"),
    )
