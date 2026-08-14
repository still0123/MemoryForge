"""Thin lifecycle glue combining freshness and knowledge conflict detection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from memoryforge.compiler.freshness import FreshnessReport, page_freshness
from memoryforge.compiler.knowledge_conflicts import (
    KnowledgeConflict,
    ProposalDraft,
    detect_conflicts,
)
from memoryforge.core.models import Claim


def run_page_lifecycle(
    workspace: Path,
    page_path: str,
    *,
    repository_id: str | None,
    applied_source_versions: Mapping[str, int],
    current_source_versions: Mapping[str, int],
    workspace_base_commit: str,
    workspace_current_commit: str,
    open_conflicts: Iterable[tuple[str, str]] = (),
    claims: Iterable[Claim] = (),
    candidate_proposal: ProposalDraft | None = None,
    pending_proposals: Iterable[ProposalDraft] = (),
) -> tuple[FreshnessReport, tuple[KnowledgeConflict, ...]]:
    freshness_report = page_freshness(
        workspace,
        page_path,
        repository_id=repository_id,
        applied_source_versions=applied_source_versions,
        current_source_versions=current_source_versions,
        workspace_base_commit=workspace_base_commit,
        workspace_current_commit=workspace_current_commit,
        open_conflicts=open_conflicts,
        claims=claims,
    )

    if candidate_proposal is not None:
        conflicts = detect_conflicts(
            candidate=candidate_proposal,
            existing_claims=claims,
            pending=pending_proposals,
        )
    else:
        conflicts = ()

    return (freshness_report, conflicts)
