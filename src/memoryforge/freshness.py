"""Page freshness tracking and refresh impact routing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from memoryforge.models import Claim, ClaimStatus


class FreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    SUPERSEDED = "superseded"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"


class FreshnessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_path: str = Field(min_length=1)
    repository_id: str | None = None
    state: FreshnessState
    based_on_workspace_commit: str
    current_workspace_commit: str
    based_on_source_versions: tuple[tuple[str, int], ...] = ()
    stale_source_versions: tuple[tuple[str, int], ...] = ()
    open_conflict_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


def _extract_applied_source_versions(
    claims: Iterable[Claim],
    applied_source_versions: Mapping[str, int],
) -> list[tuple[str, int]]:
    collected: set[tuple[str, int]] = set()
    for claim in claims:
        for citation in claim.citations:
            source_id = citation.source_id
            if source_id in applied_source_versions:
                collected.add((source_id, applied_source_versions[source_id]))
    return sorted(collected, key=lambda x: (x[0], x[1]))


def page_freshness(
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
) -> FreshnessReport:
    claims_list = list(claims)
    based_on = _extract_applied_source_versions(claims_list, applied_source_versions)

    stale: list[tuple[str, int]] = []
    all_current = bool(based_on)
    for source_id, v_applied in based_on:
        if source_id in current_source_versions:
            v_current = current_source_versions[source_id]
            if v_current > v_applied:
                stale.append((source_id, v_applied))
                all_current = False
        else:
            all_current = False

    claim_superseded = any(
        claim.status == ClaimStatus.SUPERSEDED for claim in claims_list
    )

    open_conflict_list = list(open_conflicts)
    has_open_conflict = any(page_path == cp for (_, cp) in open_conflict_list)
    open_conflict_ids = tuple(
        sorted({cid for (cid, cp) in open_conflict_list if cp == page_path})
    )

    reason_codes: list[str] = []
    state: FreshnessState

    if has_open_conflict:
        state = FreshnessState.CONFLICTED
        reason_codes.append("open_conflict")
    elif claim_superseded:
        state = FreshnessState.SUPERSEDED
        reason_codes.append("claim_superseded")
    elif stale:
        state = FreshnessState.STALE
        reason_codes.append("source_version_advanced")
    elif based_on and all_current and not claim_superseded and not has_open_conflict:
        state = FreshnessState.FRESH
        reason_codes.append("all_sources_current")
    else:
        state = FreshnessState.UNKNOWN
        if not based_on:
            reason_codes.append("no_applied_evidence")
        else:
            reason_codes.append("unknown_source_state")

    return FreshnessReport(
        page_path=page_path,
        repository_id=repository_id,
        state=state,
        based_on_workspace_commit=workspace_base_commit,
        current_workspace_commit=workspace_current_commit,
        based_on_source_versions=tuple(based_on),
        stale_source_versions=tuple(sorted(stale, key=lambda x: (x[0], x[1]))),
        open_conflict_ids=open_conflict_ids,
        reason_codes=tuple(reason_codes),
    )


def impacted_pages_for_refresh(
    changed_sources: tuple[tuple[str, int], ...],
    *,
    source_to_pages: Mapping[tuple[str, int], tuple[str, ...]],
    code_dependents: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    pages: set[str] = set()
    code_sources_in_map: set[str] = set()
    if code_dependents is not None:
        code_sources_in_map = set(code_dependents.keys())

    for source_id, new_version in changed_sources:
        for (old_source_id, old_version), page_list in source_to_pages.items():
            if old_source_id == source_id:
                for p in page_list:
                    pages.add(p)
        if code_dependents is not None and source_id in code_sources_in_map:
            for dep in code_dependents.get(source_id, ()):
                dep_key = (dep, new_version)
                for p in source_to_pages.get(dep_key, ()):
                    pages.add(p)
                old_dep_key = (dep, old_version) if new_version else dep_key
                for p in source_to_pages.get(old_dep_key, ()):
                    pages.add(p)

    return tuple(sorted(pages))
