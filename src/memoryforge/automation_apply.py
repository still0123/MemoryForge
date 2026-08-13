"""Shared decision engine and Phase 2 LOW auto-apply integration.

This module is the single place where staged ChangeSets are evaluated against
the automation policy (§25.2: Portal, CLI and ``automation-run`` share one
decision engine). ``evaluate_staged`` is read-only and used by ``policy
simulate`` and ``changeset-list --decision``; ``auto_apply_changeset`` takes
an ``AUTO_APPLY`` decision, binds policy review/approval receipts and applies
it through the existing lifecycle, reusing the apply journal, lint, Git
commit and projection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memoryforge.automation_policy import (
    AutomationPolicy,
    decide,
    effective_profile,
    effective_trust,
    load_policy,
)
from memoryforge.automation_validation import (
    BLOCK_BASE_COMMIT_CHANGED,
    BLOCK_STALE_SOURCE_VERSION,
    assess_operation,
    build_validation_report,
    candidate_tree_sha256,
    change_set_risk,
    source_versions_sha256,
)
from memoryforge.changesets import ChangeSetStore, StoredChangeSet
from memoryforge.lifecycle import _apply_stored
from memoryforge.models import (
    AutomationDecision,
    AutomationDecisionReceipt,
    OperationAssessment,
    ReviewActorType,
    RiskLevel,
    SourceTrust,
    ValidationCheck,
)
from memoryforge.obsidian import build_obsidian
from memoryforge.portal_jobs import run_automation
from memoryforge.workspace import (
    Workspace,
    _connect_readonly,
    candidate_page_sources,
)


@dataclass(frozen=True)
class StagedEvaluation:
    """One deterministic automation decision plus its verification inputs."""

    changeset_id: str
    proposal_sha256: str
    decision: AutomationDecision
    risk: RiskLevel
    reason_codes: tuple[str, ...]
    profile: str
    policy_sha256: str
    source_trust: SourceTrust
    validation_sha256: str
    assessments: tuple[OperationAssessment, ...]

    def summary(self) -> dict[str, object]:
        return {
            "changeset_id": self.changeset_id,
            "proposal_sha256": self.proposal_sha256,
            "decision": self.decision.value,
            "risk": self.risk.value,
            "reason_codes": list(self.reason_codes),
            "profile": self.profile,
            "policy_sha256": self.policy_sha256,
            "source_trust": self.source_trust.value,
            "validation_sha256": self.validation_sha256,
            "operations": [
                {
                    "path": item.path,
                    "origin": item.origin.value if item.origin is not None else None,
                    "risk": item.risk.value,
                    "reason_codes": list(item.reason_codes),
                    "changed_lines": item.changed_lines,
                }
                for item in self.assessments
            ],
        }


def current_source_versions(
    opened: Workspace,
    source_ids: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    """Map source_id to its current version id and untrusted-tag flag."""
    if not source_ids:
        return {}
    placeholders = ", ".join("?" for _ in source_ids)
    with _connect_readonly(opened.index_path) as connection:
        rows = connection.execute(
            f"""
            SELECT sources.source_id, versions.id, versions.tags_json
            FROM sources
            JOIN source_versions AS versions ON versions.source_id = sources.id
            WHERE sources.source_id IN ({placeholders}) AND versions.is_current = 1
            """,
            tuple(source_ids),
        ).fetchall()
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        tags = json.loads(str(row["tags_json"]))
        result[str(row["source_id"])] = {
            "version_id": int(row["id"]),
            "untrusted": "conversation" in tags or "platform:codex" in tags,
        }
    return result


def evaluate_staged(
    opened: Workspace,
    stored: StoredChangeSet,
    policy: AutomationPolicy,
) -> StagedEvaluation:
    """Run the shadow decision engine over one staged ChangeSet (read-only)."""
    changeset = stored.changeset
    base_commit_changed = changeset.base_commit != opened.current_commit()
    current_versions = current_source_versions(opened, changeset.source_ids)
    stale_versions = any(
        changeset.source_versions.get(source_id) != row["version_id"]
        for source_id, row in current_versions.items()
    )
    block_reasons: list[str] = []
    if base_commit_changed:
        block_reasons.append(BLOCK_BASE_COMMIT_CHANGED)
    if stale_versions:
        block_reasons.append(BLOCK_STALE_SOURCE_VERSION)
    trust_defaults = {
        source_id: SourceTrust.UNTRUSTED if row["untrusted"] else SourceTrust.STANDARD
        for source_id, row in current_versions.items()
    }
    trust = effective_trust(policy, changeset.source_ids, trust_defaults)
    profile = effective_profile(policy, changeset.source_ids)
    page_sources = candidate_page_sources(stored.candidate_files)
    assessments = []
    for operation in changeset.operations:
        before = opened.version_store.read_text_at(changeset.base_commit, operation.path) or ""
        after = stored.candidate_files.get(operation.path, "")
        source_count = len(page_sources.get(operation.path, ())) or len(changeset.source_ids)
        assessments.append(
            assess_operation(
                operation,
                before=before,
                after=after,
                source_count=source_count,
                source_trust=trust,
            )
        )
    risk, reason_codes = change_set_risk(
        tuple(assessments),
        low_max_changed_pages=policy.limits.low_max_changed_pages,
        low_max_changed_lines=policy.limits.low_max_changed_lines,
    )
    candidate_pages = tuple(
        sorted(
            path
            for path in stored.candidate_files
            if path.startswith("wiki/pages/") and path.endswith(".md")
        )
    )
    open_conflict_ids: tuple[str, ...] = ()
    if candidate_pages:
        placeholders = ", ".join("?" for _ in candidate_pages)
        with _connect_readonly(opened.index_path) as connection:
            rows = connection.execute(
                f"""
                SELECT conflict_id
                FROM knowledge_conflicts
                WHERE resolution = 'open' AND page_path IN ({placeholders})
                ORDER BY conflict_id
                """,
                candidate_pages,
            ).fetchall()
        open_conflict_ids = tuple(str(row["conflict_id"]) for row in rows)
    evaluation = decide(
        policy,
        profile=profile,
        risk=risk,
        reason_codes=reason_codes,
        source_trust=trust,
        block_reasons=tuple(block_reasons),
        open_conflict_ids=open_conflict_ids,
    )
    validation = build_validation_report(
        checks=(
            ValidationCheck(
                check_id="base_commit",
                status="failed" if base_commit_changed else "passed",
            ),
            ValidationCheck(
                check_id="source_versions",
                status="failed" if stale_versions else "passed",
            ),
        ),
        candidate_tree_sha256=candidate_tree_sha256(stored.candidate_files),
        source_versions_sha256=source_versions_sha256(changeset.source_versions),
    )
    validation_sha256 = hashlib.sha256(
        (validation.model_dump_json(indent=2) + "\n").encode()
    ).hexdigest()
    return StagedEvaluation(
        changeset_id=changeset.changeset_id,
        proposal_sha256=stored.proposal_sha256,
        decision=evaluation.decision,
        risk=evaluation.risk,
        reason_codes=evaluation.reason_codes,
        profile=evaluation.policy_id,
        policy_sha256=evaluation.policy_sha256,
        source_trust=trust,
        validation_sha256=validation_sha256,
        assessments=tuple(assessments),
    )


def auto_apply_changeset(
    workspace: Path,
    changeset_id: str,
    *,
    obsidian_builder: Callable[[Path], dict[str, object]] = build_obsidian,
) -> dict[str, Any]:
    """Evaluate one staged ChangeSet and auto-apply it when the policy allows."""
    opened = Workspace.open(workspace)
    with opened.exclusive_lock():
        store = ChangeSetStore(opened)
        stored = store.get(changeset_id)
        policy = load_policy(opened.root)
        evaluation = evaluate_staged(opened, stored, policy)
        decision = AutomationDecisionReceipt(
            changeset_id=evaluation.changeset_id,
            proposal_sha256=evaluation.proposal_sha256,
            validation_sha256=evaluation.validation_sha256,
            policy_id=evaluation.profile,
            policy_sha256=evaluation.policy_sha256,
            decision=evaluation.decision,
            risk=evaluation.risk,
            reason_codes=evaluation.reason_codes,
            decided_at=datetime.now(UTC),
        )
        store.write_decision(stored, decision)
        opened.record_automation_decision(
            evaluation.changeset_id,
            proposal_sha256=evaluation.proposal_sha256,
            validation_sha256=evaluation.validation_sha256,
            policy_sha256=evaluation.policy_sha256,
            decision=evaluation.decision.value,
            risk=evaluation.risk.value,
            reason_codes=evaluation.reason_codes,
        )
        opened.record_automation_event(
            "decided",
            changeset_id=evaluation.changeset_id,
            details={
                "decision": evaluation.decision.value,
                "risk": evaluation.risk.value,
                "reason_codes": list(evaluation.reason_codes),
            },
        )
        if evaluation.decision is not AutomationDecision.AUTO_APPLY:
            return {
                "changeset_id": evaluation.changeset_id,
                "status": "deferred",
                "decision": evaluation.decision.value,
                "risk": evaluation.risk.value,
                "reason_codes": list(evaluation.reason_codes),
            }
        decision_sha256 = hashlib.sha256(
            (decision.model_dump_json(indent=2) + "\n").encode()
        ).hexdigest()
        store.record_review(
            stored,
            mode="policy",
            actor_type=ReviewActorType.POLICY,
            actor_id=evaluation.profile,
            decision_sha256=decision_sha256,
        )
        store.approve(
            stored,
            actor_type=ReviewActorType.POLICY,
            actor_id=evaluation.profile,
            decision_sha256=decision_sha256,
            policy_sha256=evaluation.policy_sha256,
            approval_reason_codes=evaluation.reason_codes,
        )
        commit_message = _auto_commit_message(
            changeset_id=evaluation.changeset_id,
            proposal_sha256=evaluation.proposal_sha256,
            validation_sha256=evaluation.validation_sha256,
            policy_sha256=evaluation.policy_sha256,
        )
        result = _apply_stored(
            opened,
            store,
            stored,
            obsidian_builder=obsidian_builder,
            commit_message=commit_message,
        )
        opened.record_automation_event(
            "applied",
            changeset_id=evaluation.changeset_id,
            details={"commit": result["commit"]},
        )
        result["decision"] = evaluation.decision.value
        result["risk"] = evaluation.risk.value
        result["reason_codes"] = list(evaluation.reason_codes)
        return result


def run_automation_apply(workspace: Path) -> dict[str, Any]:
    """Run one refresh, validate, decide and auto-apply cycle (§16.1)."""
    refreshed = run_automation(workspace)
    if refreshed.get("status") == "disabled":
        return {"status": "disabled", "changeset_ids": [], "decisions": [], "applied": []}
    opened = Workspace.open_readonly(workspace)
    policy = load_policy(opened.root)
    store = ChangeSetStore(opened)
    decisions = [evaluate_staged(opened, stored, policy).summary() for stored in store.list_all()]
    applied: list[dict[str, Any]] = []
    for summary in sorted(decisions, key=lambda item: str(item["changeset_id"])):
        if summary["decision"] != AutomationDecision.AUTO_APPLY.value:
            continue
        applied.append(auto_apply_changeset(workspace, str(summary["changeset_id"])))
        break
    return {
        "status": refreshed.get("status"),
        "changeset_ids": refreshed.get("changeset_ids", []),
        "decisions": decisions,
        "applied": applied,
    }


def _auto_commit_message(
    *,
    changeset_id: str,
    proposal_sha256: str,
    validation_sha256: str,
    policy_sha256: str,
) -> str:
    return (
        f"knowledge(auto): apply {changeset_id}\n"
        "\n"
        "MemoryForge-Decision: auto_apply\n"
        f"MemoryForge-Policy: {policy_sha256}\n"
        f"MemoryForge-Validation: {validation_sha256}\n"
        f"MemoryForge-Proposal: {proposal_sha256}\n"
    )
