from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from memoryforge.compiler.freshness import (
    FreshnessState,
    impacted_pages_for_refresh,
    page_freshness,
)
from memoryforge.core.models import Citation, Claim, ClaimStatus

SOURCE_ID_A = "a" * 64
SOURCE_ID_B = "b" * 64
CONTENT_SHA = "c" * 64
QUOTE_SHA = "d" * 64


def _citation(source_id: str, version: int = 1) -> Citation:
    return Citation(
        source_id=source_id,
        content_sha256=CONTENT_SHA,
        snapshot_uri=f"mf://blob/{CONTENT_SHA}",
        quote="hello",
        quote_sha256=hashlib.sha256(b"hello").hexdigest(),
        locator="chars:0-5",
    )


def _claim(
    claim_id: str,
    *,
    status: ClaimStatus = ClaimStatus.VERIFIED,
    citations: tuple[Citation, ...] = (),
) -> Claim:
    return Claim(
        claim_id=claim_id,
        subject="S",
        predicate="P",
        object="O",
        status=status,
        confidence=1.0,
        valid_from=datetime.utcnow(),
        citations=citations,
    )


def test_page_freshness_stale_when_source_advanced(tmp_path: Path):
    citations = (_citation(SOURCE_ID_A), _citation(SOURCE_ID_B))
    claims = [_claim("clm_1", citations=citations)]
    applied = {SOURCE_ID_A: 1, SOURCE_ID_B: 1}
    current = {SOURCE_ID_A: 2, SOURCE_ID_B: 1}
    report = page_freshness(
        tmp_path,
        "wiki/pages/foo.md",
        repository_id=None,
        applied_source_versions=applied,
        current_source_versions=current,
        workspace_base_commit="c1",
        workspace_current_commit="c2",
        open_conflicts=(),
        claims=claims,
    )
    assert report.state == FreshnessState.STALE
    assert "source_version_advanced" in report.reason_codes
    assert len(report.stale_source_versions) >= 1


def test_page_freshness_fresh_when_aligned(tmp_path: Path):
    citations = (_citation(SOURCE_ID_A),)
    claims = [_claim("clm_2", citations=citations)]
    applied = {SOURCE_ID_A: 3}
    current = {SOURCE_ID_A: 3}
    report = page_freshness(
        tmp_path,
        "wiki/pages/bar.md",
        repository_id="repo1",
        applied_source_versions=applied,
        current_source_versions=current,
        workspace_base_commit="c1",
        workspace_current_commit="c1",
        claims=claims,
    )
    assert report.state == FreshnessState.FRESH
    assert "all_sources_current" in report.reason_codes


def test_page_freshness_superseded_claim(tmp_path: Path):
    citations = (_citation(SOURCE_ID_A),)
    claims = [_claim("clm_3", status=ClaimStatus.SUPERSEDED, citations=citations)]
    applied = {SOURCE_ID_A: 1}
    current = {SOURCE_ID_A: 1}
    report = page_freshness(
        tmp_path,
        "wiki/pages/x.md",
        repository_id=None,
        applied_source_versions=applied,
        current_source_versions=current,
        workspace_base_commit="c",
        workspace_current_commit="c",
        claims=claims,
    )
    assert report.state == FreshnessState.SUPERSEDED
    assert "claim_superseded" in report.reason_codes


def test_page_freshness_conflicted(tmp_path: Path):
    applied = {SOURCE_ID_A: 1}
    current = {SOURCE_ID_A: 1}
    open_conflicts = [("c_123", "wiki/pages/y.md")]
    report = page_freshness(
        tmp_path,
        "wiki/pages/y.md",
        repository_id=None,
        applied_source_versions=applied,
        current_source_versions=current,
        workspace_base_commit="c",
        workspace_current_commit="c",
        open_conflicts=open_conflicts,
        claims=[],
    )
    assert report.state == FreshnessState.CONFLICTED
    assert "open_conflict" in report.reason_codes
    assert "c_123" in report.open_conflict_ids


def test_page_freshness_unknown_when_no_evidence(tmp_path: Path):
    applied: dict[str, int] = {}
    current: dict[str, int] = {}
    report = page_freshness(
        tmp_path,
        "wiki/pages/z.md",
        repository_id=None,
        applied_source_versions=applied,
        current_source_versions=current,
        workspace_base_commit="c",
        workspace_current_commit="c",
        claims=[],
    )
    assert report.state == FreshnessState.UNKNOWN
    assert "no_applied_evidence" in report.reason_codes


def test_page_freshness_workspace_commits_tracked(tmp_path: Path):
    citations = (_citation(SOURCE_ID_A),)
    claims = [_claim("clm_w", citations=citations)]
    report = page_freshness(
        tmp_path,
        "wiki/pages/w.md",
        repository_id=None,
        applied_source_versions={SOURCE_ID_A: 1},
        current_source_versions={SOURCE_ID_A: 1},
        workspace_base_commit="base1",
        workspace_current_commit="current2",
        claims=claims,
    )
    assert report.based_on_workspace_commit == "base1"
    assert report.current_workspace_commit == "current2"


def test_impacted_pages_for_refresh_single_source_multiple_pages():
    changed = ((SOURCE_ID_A, 2),)
    source_to_pages = {
        (SOURCE_ID_A, 1): ("wiki/pages/a.md", "wiki/pages/shared.md"),
        (SOURCE_ID_B, 1): ("wiki/pages/b.md",),
    }
    result = impacted_pages_for_refresh(changed, source_to_pages=source_to_pages)
    assert "wiki/pages/a.md" in result
    assert "wiki/pages/shared.md" in result
    assert "wiki/pages/b.md" not in result


def test_impacted_pages_for_refresh_code_dependents_extend():
    changed = ((SOURCE_ID_A, 2),)
    source_to_pages = {
        (SOURCE_ID_A, 1): ("wiki/pages/a.md",),
        ("mod_b.py", 1): ("wiki/pages/mod-b.md",),
    }
    code_dependents = {
        SOURCE_ID_A: ("mod_b.py",),
    }
    result = impacted_pages_for_refresh(
        changed,
        source_to_pages=source_to_pages,
        code_dependents=code_dependents,
    )
    assert "wiki/pages/mod-b.md" in result


def test_impacted_pages_for_refresh_sorted_and_unique():
    changed = ((SOURCE_ID_A, 2), (SOURCE_ID_B, 3))
    source_to_pages = {
        (SOURCE_ID_A, 1): ("wiki/pages/b.md", "wiki/pages/a.md"),
        (SOURCE_ID_B, 2): ("wiki/pages/a.md",),
    }
    result = impacted_pages_for_refresh(changed, source_to_pages=source_to_pages)
    assert tuple(sorted(result)) == result
    assert len(result) == len(set(result))
