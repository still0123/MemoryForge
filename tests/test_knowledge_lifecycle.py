from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from memoryforge.compiler.freshness import FreshnessState
from memoryforge.compiler.knowledge_lifecycle import run_page_lifecycle
from memoryforge.core.models import (
    ChangeOrigin,
    Citation,
    Claim,
    ClaimStatus,
    RiskLevel,
)


SOURCE_ID_A = "a" * 64
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


def test_run_page_lifecycle_combines_freshness_and_conflicts(tmp_path: Path):
    claims = []
    from memoryforge.compiler.knowledge_conflicts import ProposalDraft
    candidate = ProposalDraft(
        page_path="wiki/pages/run.md",
        content="# Run\n\ntest",
        citations=(),
        origin=ChangeOrigin.AGENT_PROPOSAL,
        risk=RiskLevel.HIGH,
    )
    report, conflicts = run_page_lifecycle(
        tmp_path,
        "wiki/pages/run.md",
        repository_id=None,
        applied_source_versions={},
        current_source_versions={},
        workspace_base_commit="c1",
        workspace_current_commit="c1",
        open_conflicts=(),
        claims=claims,
        candidate_proposal=candidate,
        pending_proposals=(),
    )
    assert report.state in {FreshnessState.FRESH, FreshnessState.UNKNOWN, FreshnessState.STALE}
    assert isinstance(conflicts, tuple)
