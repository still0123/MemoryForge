from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from memoryforge.core.egress_models import (
    DisclosureReceipt,
    EgressClass,
    EgressDecision,
    EgressRequest,
    SourceEgressRule,
)
from memoryforge.core.models import Sensitivity

SCHEMA_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS source_egress_rules (
        source_id TEXT PRIMARY KEY NOT NULL,
        egress_class TEXT NOT NULL,
        allowed_hosts TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        actor TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_source_egress_rules_updated_at
    ON source_egress_rules(updated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_source_egress_rules_actor
    ON source_egress_rules(actor)
    """,
    """
    CREATE TABLE IF NOT EXISTS disclosure_receipts (
        request_id TEXT PRIMARY KEY NOT NULL,
        host_id TEXT NOT NULL,
        repository_id TEXT NOT NULL,
        purpose TEXT NOT NULL,
        policy_sha256 TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        source_refs TEXT NOT NULL,
        character_count INTEGER NOT NULL,
        redaction_count INTEGER NOT NULL,
        disclosed_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_disclosure_receipts_host_id
    ON disclosure_receipts(host_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_disclosure_receipts_repository_id
    ON disclosure_receipts(repository_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_disclosure_receipts_disclosed_at
    ON disclosure_receipts(disclosed_at)
    """,
)


def _rule_canonical_json(rule: SourceEgressRule) -> str:
    return json.dumps(
        rule.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def rule_sha256(rule: SourceEgressRule) -> str:
    return hashlib.sha256(_rule_canonical_json(rule).encode("utf-8")).hexdigest()


def _get_rule(connection: sqlite3.Connection, source_id: str) -> SourceEgressRule | None:
    cursor = connection.execute(
        "SELECT source_id, egress_class, allowed_hosts, updated_at, actor "
        "FROM source_egress_rules WHERE source_id = ?",
        (source_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    source_id, egress_class_str, allowed_hosts_json, updated_at_str, actor = row
    allowed_hosts = tuple(json.loads(allowed_hosts_json))
    updated_at = datetime.fromisoformat(updated_at_str)
    return SourceEgressRule(
        source_id=source_id,
        egress_class=EgressClass(egress_class_str),
        allowed_hosts=allowed_hosts,
        updated_at=updated_at,
        actor=actor,
    )


def decide_egress(
    connection: sqlite3.Connection,
    *,
    request: EgressRequest,
    source_id: str,
    source_version: int,
    sensitivity: Sensitivity,
) -> EgressDecision:
    rule = _get_rule(connection, source_id)

    if rule is not None:
        if rule.egress_class is EgressClass.PUBLIC:
            return EgressDecision(
                allowed=True,
                reason_code="public_source",
                egress_class=EgressClass.PUBLIC,
            )
        if rule.egress_class is EgressClass.HOST_ALLOWED:
            if request.host_id in rule.allowed_hosts:
                return EgressDecision(
                    allowed=True,
                    reason_code="host_allowed_match",
                    egress_class=EgressClass.HOST_ALLOWED,
                )
            else:
                return EgressDecision(
                    allowed=False,
                    reason_code="host_not_in_allowlist",
                    egress_class=EgressClass.HOST_ALLOWED,
                )
        if rule.egress_class is EgressClass.NEVER_MODEL:
            return EgressDecision(
                allowed=False,
                reason_code="never_model_default",
                egress_class=EgressClass.NEVER_MODEL,
            )

    if sensitivity is Sensitivity.PUBLIC:
        return EgressDecision(
            allowed=True,
            reason_code="public_source",
            egress_class=EgressClass.PUBLIC,
        )

    return EgressDecision(
        allowed=False,
        reason_code="never_model_default",
        egress_class=EgressClass.NEVER_MODEL,
    )


def filter_visible_sources(candidates: Iterable, *, decide: Callable) -> tuple:
    visible = []
    for candidate in candidates:
        decision = decide(candidate)
        if decision.allowed:
            visible.append(candidate)
    return tuple(visible)


def record_disclosure(
    connection: sqlite3.Connection,
    *,
    request: EgressRequest,
    text: str,
    source_refs: tuple[tuple[str, int], ...],
    redaction,
    policy_sha256: str,
) -> DisclosureReceipt:
    content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    character_count = len(text)
    redaction_count = redaction.redaction_count
    disclosed_at = datetime.now(UTC)
    source_refs_json = json.dumps(list(source_refs), sort_keys=True, separators=(",", ":"))

    connection.execute(
        "INSERT INTO disclosure_receipts ("
        "request_id, host_id, repository_id, purpose, policy_sha256, "
        "content_sha256, source_refs, character_count, redaction_count, disclosed_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            request.request_id,
            request.host_id,
            request.repository_id,
            request.purpose,
            policy_sha256,
            content_sha256,
            source_refs_json,
            character_count,
            redaction_count,
            disclosed_at.isoformat(),
        ),
    )
    connection.commit()

    return DisclosureReceipt(
        request_id=request.request_id,
        host_id=request.host_id,
        repository_id=request.repository_id,
        purpose=request.purpose,
        policy_sha256=policy_sha256,
        content_sha256=content_sha256,
        source_refs=source_refs,
        character_count=character_count,
        redaction_count=redaction_count,
        disclosed_at=disclosed_at,
    )


def _allowed_hosts_to_json(allowed_hosts: tuple[str, ...]) -> str:
    return json.dumps(list(allowed_hosts), sort_keys=True, separators=(",", ":"))


def upsert_rule(connection: sqlite3.Connection, rule: SourceEgressRule) -> None:
    allowed_hosts_json = _allowed_hosts_to_json(rule.allowed_hosts)
    connection.execute(
        "INSERT OR REPLACE INTO source_egress_rules ("
        "source_id, egress_class, allowed_hosts, updated_at, actor"
        ") VALUES (?, ?, ?, ?, ?)",
        (
            rule.source_id,
            rule.egress_class.value,
            allowed_hosts_json,
            rule.updated_at.isoformat(),
            rule.actor,
        ),
    )
    connection.commit()
