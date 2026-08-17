"""Phase 2 integration tests: LOW auto-apply, decision receipts, and deferral.

Covers the shared decision engine (§25.2), policy review/approval receipts
(§12.1/§12.2), the ``knowledge(auto): apply`` commit format (§12.4), and the
automation projection rows recorded in SQLite.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.automation.automation_apply import auto_apply_changeset, evaluate_staged
from memoryforge.automation.automation_policy import (
    PROFILE_MANUAL,
    AutomationPolicy,
    load_policy,
    save_policy,
)
from memoryforge.core.errors import ChangeSetStoreError
from memoryforge.core.models import (
    AutomationDecision,
    AutomationDecisionReceipt,
    RiskLevel,
)
from memoryforge.interface.cli import app
from memoryforge.storage.changesets import ChangeSetStore
from memoryforge.storage.workspace import Workspace


def _staged_changeset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    source = tmp_path / "cache.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    initialized = runner.invoke(app, ["init", str(workspace)])
    assert initialized.exit_code == 0, initialized.output
    imported = runner.invoke(app, ["import", str(source), "--workspace", str(workspace)])
    assert imported.exit_code == 0, imported.output
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    return workspace, json.loads(ingested.stdout)["changeset_id"]


def test_evaluate_staged_auto_applies_deterministic_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    opened = Workspace.open_readonly(workspace)
    stored = ChangeSetStore(opened).get(changeset_id)
    evaluation = evaluate_staged(opened, stored, load_policy(opened.root))
    assert evaluation.decision is AutomationDecision.AUTO_APPLY
    assert evaluation.risk is RiskLevel.LOW
    assert len(evaluation.validation_sha256) == 64
    assert len(evaluation.proposal_sha256) == 64
    assert evaluation.summary()["decision"] == "auto_apply"


def test_evaluate_staged_requires_review_for_open_page_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    opened = Workspace.open(workspace)
    stored = ChangeSetStore(opened).get(changeset_id)
    page_path = next(path for path in stored.candidate_files if path.startswith("wiki/pages/"))
    with sqlite3.connect(opened.index_path) as connection:
        connection.execute(
            """
            INSERT INTO knowledge_conflicts (
                conflict_id, repository_id, page_path, subject_key, kind,
                left_claim_id, left_source_id, left_source_version, left_locator,
                right_claim_id, right_source_id, right_source_version, right_locator,
                detected_at, resolution, resolved_by_changeset_id
            ) VALUES (?, NULL, ?, 'same_page_pending', 'same_target',
                      'left', 'pending', 1, 'chars:0-1',
                      'right', 'pending', 1, 'chars:0-1',
                      '2026-01-01T00:00:00+00:00', 'open', NULL)
            """,
            ("test_open_conflict", page_path),
        )
    evaluation = evaluate_staged(opened, stored, load_policy(opened.root))

    assert evaluation.decision is AutomationDecision.REVIEW_REQUIRED
    assert "open_conflict_block" in evaluation.reason_codes


def test_auto_apply_applies_low_and_records_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    result = auto_apply_changeset(workspace, changeset_id)
    assert result["status"] == "APPLIED"
    assert result["decision"] == "auto_apply"
    assert list((workspace / "wiki" / "pages").glob("*.md"))

    archived = workspace / ".memoryforge" / "staging" / "applied" / changeset_id
    decision = json.loads((archived / "decision.json").read_text(encoding="utf-8"))
    assert decision["decision"] == "auto_apply"
    assert len(decision["validation_sha256"]) == 64
    assert len(decision["policy_sha256"]) == 64
    review = json.loads((archived / "review.json").read_text(encoding="utf-8"))
    assert review["actor_type"] == "policy"
    assert review["review_mode"] == "policy"
    assert len(review["decision_sha256"]) == 64
    approval = json.loads((archived / "approval.json").read_text(encoding="utf-8"))
    assert approval["actor_type"] == "policy"
    assert approval["policy_sha256"] == decision["policy_sha256"]
    assert approval["decision_sha256"] == review["decision_sha256"]
    assert approval["approval_reason_codes"]

    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert subject == f"knowledge(auto): apply {changeset_id}"
    body = subprocess.run(
        ["git", "log", "-1", "--format=%b"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "MemoryForge-Decision: auto_apply" in body
    assert f"MemoryForge-Policy: {decision['policy_sha256']}" in body
    assert f"MemoryForge-Validation: {decision['validation_sha256']}" in body
    assert f"MemoryForge-Proposal: {decision['proposal_sha256']}" in body

    with sqlite3.connect(workspace / ".memoryforge" / "index.sqlite") as connection:
        row = connection.execute(
            "SELECT decision, risk FROM automation_decisions WHERE changeset_id = ?",
            (changeset_id,),
        ).fetchone()
        assert row == ("auto_apply", "low")
        events = connection.execute(
            "SELECT event_type FROM automation_events WHERE changeset_id = ? ORDER BY id",
            (changeset_id,),
        ).fetchall()
        assert [event[0] for event in events] == ["decided", "applied"]


def test_auto_apply_defers_when_policy_requires_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    save_policy(workspace, AutomationPolicy(profile=PROFILE_MANUAL))
    result = auto_apply_changeset(workspace, changeset_id)
    assert result["status"] == "deferred"
    assert result["decision"] == "review_required"
    assert not any((workspace / "wiki" / "pages").glob("*.md"))

    pending = workspace / ".memoryforge" / "staging" / changeset_id
    decision = json.loads((pending / "decision.json").read_text(encoding="utf-8"))
    assert decision["decision"] == "review_required"
    assert not (pending / "review.json").exists()
    assert not (pending / "approval.json").exists()


def test_decision_receipt_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    opened = Workspace.open(workspace)
    store = ChangeSetStore(opened)
    stored = store.get(changeset_id)
    receipt = AutomationDecisionReceipt(
        changeset_id=changeset_id,
        proposal_sha256=stored.proposal_sha256,
        validation_sha256="a" * 64,
        policy_id="balanced",
        policy_sha256="b" * 64,
        decision=AutomationDecision.AUTO_APPLY,
        risk=RiskLevel.LOW,
        reason_codes=("AUTO_MECHANICALLY_VERIFIED",),
        decided_at=datetime.now(UTC),
    )
    first = store.write_decision(stored, receipt)
    second = store.write_decision(stored, receipt)
    assert first == second
    assert store.read_decision(stored) == first


def test_decision_receipt_rejects_mismatched_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    opened = Workspace.open(workspace)
    store = ChangeSetStore(opened)
    stored = store.get(changeset_id)
    receipt = AutomationDecisionReceipt(
        changeset_id=changeset_id,
        proposal_sha256="0" * 64,
        validation_sha256="a" * 64,
        policy_id="balanced",
        policy_sha256="b" * 64,
        decision=AutomationDecision.AUTO_APPLY,
        risk=RiskLevel.LOW,
        reason_codes=(),
        decided_at=datetime.now(UTC),
    )
    with pytest.raises(ChangeSetStoreError, match="does not match"):
        store.write_decision(stored, receipt)
