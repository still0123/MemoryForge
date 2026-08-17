from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from memoryforge.compiler.egress_policy import (
    SCHEMA_SQL,
    decide_egress,
    filter_visible_sources,
    record_disclosure,
    rule_sha256,
    upsert_rule,
)
from memoryforge.compiler.redaction import redact_for_model
from memoryforge.core.egress_models import (
    EgressClass,
    EgressRequest,
    SourceEgressRule,
)
from memoryforge.core.models import Sensitivity


def _make_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    for statement in SCHEMA_SQL:
        connection.execute(statement)
    return connection


def _source_id(n: int) -> str:
    return f"{n:064x}"


def _host_id(n: str) -> str:
    return f"host-{n}"


def _make_request(host_id: str = "host-trusted") -> EgressRequest:
    return EgressRequest(
        request_id="req-001",
        host_id=host_id,
        repository_id="repo-" + "0" * 56,
        purpose="context",
        max_characters=10000,
    )


def test_schema_sql_creates_tables_without_error() -> None:
    connection = _make_connection()
    cursor = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row[0] for row in cursor.fetchall()}
    assert "source_egress_rules" in tables
    assert "disclosure_receipts" in tables


def test_public_sensitivity_default_allows() -> None:
    connection = _make_connection()
    source_id = _source_id(1)
    request = _make_request()
    decision = decide_egress(
        connection,
        request=request,
        source_id=source_id,
        source_version=1,
        sensitivity=Sensitivity.PUBLIC,
    )
    assert decision.allowed is True
    assert decision.reason_code == "public_source"
    assert decision.egress_class is EgressClass.PUBLIC


def test_local_only_sensitivity_default_denies() -> None:
    connection = _make_connection()
    source_id = _source_id(2)
    request = _make_request()
    decision = decide_egress(
        connection,
        request=request,
        source_id=source_id,
        source_version=1,
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    assert decision.allowed is False
    assert decision.reason_code == "never_model_default"
    assert decision.egress_class is EgressClass.NEVER_MODEL


def test_host_allowed_rule_matches_host() -> None:
    connection = _make_connection()
    source_id = _source_id(3)
    rule = SourceEgressRule(
        source_id=source_id,
        egress_class=EgressClass.HOST_ALLOWED,
        allowed_hosts=(_host_id("alpha"), _host_id("beta")),
        updated_at=datetime.now(UTC),
        actor="human-admin",
    )
    upsert_rule(connection, rule)
    request = _make_request(host_id=_host_id("alpha"))
    decision = decide_egress(
        connection,
        request=request,
        source_id=source_id,
        source_version=1,
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    assert decision.allowed is True
    assert decision.reason_code == "host_allowed_match"
    assert decision.egress_class is EgressClass.HOST_ALLOWED


def test_host_allowed_rule_rejects_unknown_host() -> None:
    connection = _make_connection()
    source_id = _source_id(4)
    rule = SourceEgressRule(
        source_id=source_id,
        egress_class=EgressClass.HOST_ALLOWED,
        allowed_hosts=(_host_id("alpha"),),
        updated_at=datetime.now(UTC),
        actor="human-admin",
    )
    upsert_rule(connection, rule)
    request = _make_request(host_id=_host_id("intruder"))
    decision = decide_egress(
        connection,
        request=request,
        source_id=source_id,
        source_version=1,
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    assert decision.allowed is False
    assert decision.reason_code == "host_not_in_allowlist"
    assert decision.egress_class is EgressClass.HOST_ALLOWED


def test_decide_egress_disallows_nonexistent_host_id() -> None:
    connection = _make_connection()
    source_id = _source_id(5)
    rule = SourceEgressRule(
        source_id=source_id,
        egress_class=EgressClass.HOST_ALLOWED,
        allowed_hosts=(_host_id("alpha"),),
        updated_at=datetime.now(UTC),
        actor="human-admin",
    )
    upsert_rule(connection, rule)
    forged_request = EgressRequest(
        request_id="req-forged",
        host_id="totally-made-up-host",
        repository_id="repo-" + "0" * 56,
        purpose="context",
        max_characters=10000,
    )
    decision = decide_egress(
        connection,
        request=forged_request,
        source_id=source_id,
        source_version=1,
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    assert decision.allowed is False
    assert decision.reason_code == "host_not_in_allowlist"


def test_filter_visible_sources() -> None:
    connection = _make_connection()

    source_ids = [_source_id(i) for i in range(10, 15)]
    for idx, sid in enumerate(source_ids):
        if idx == 3:
            rule = SourceEgressRule(
                source_id=sid,
                egress_class=EgressClass.HOST_ALLOWED,
                allowed_hosts=(_host_id("filter-host"),),
                updated_at=datetime.now(UTC),
                actor="human-admin",
            )
            upsert_rule(connection, rule)

    request = _make_request(host_id=_host_id("filter-host"))
    candidates = [
        {"source_id": sid, "sensitivity": Sensitivity.PUBLIC if i < 2 else Sensitivity.LOCAL_ONLY}
        for i, sid in enumerate(source_ids)
    ]

    def decide(candidate: dict) -> object:
        return decide_egress(
            connection,
            request=request,
            source_id=candidate["source_id"],
            source_version=1,
            sensitivity=candidate["sensitivity"],
        )

    visible = filter_visible_sources(candidates, decide=decide)
    visible_ids = [c["source_id"] for c in visible]
    assert source_ids[0] in visible_ids
    assert source_ids[1] in visible_ids
    assert source_ids[3] in visible_ids
    assert len(visible) == 3


def test_policy_sha256_is_stable() -> None:
    updated_at = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    rule_a = SourceEgressRule(
        source_id=_source_id(100),
        egress_class=EgressClass.HOST_ALLOWED,
        allowed_hosts=("host-a", "host-b"),
        updated_at=updated_at,
        actor="admin",
    )
    rule_a_copy = SourceEgressRule.model_construct(
        source_id=_source_id(100),
        egress_class=EgressClass.HOST_ALLOWED,
        allowed_hosts=("host-a", "host-b"),
        updated_at=updated_at,
        actor="admin",
    )
    rule_different_order = SourceEgressRule.model_construct(
        source_id=_source_id(100),
        egress_class=EgressClass.HOST_ALLOWED,
        allowed_hosts=("host-b", "host-a"),
        updated_at=updated_at,
        actor="admin",
    )
    assert rule_sha256(rule_a) == rule_sha256(rule_a_copy)
    assert rule_sha256(rule_a) != rule_sha256(rule_different_order)


def test_record_disclosure_writes_receipt_without_body() -> None:
    connection = _make_connection()
    source_id = _source_id(200)
    request = _make_request()
    text = "This is context text with a secret API_TOKEN=abc123 inside."
    redaction = redact_for_model(text)
    source_refs = ((source_id, 1),)
    policy_sha = "a" * 64

    receipt = record_disclosure(
        connection,
        request=request,
        text=text,
        source_refs=source_refs,
        redaction=redaction,
        policy_sha256=policy_sha,
    )

    assert receipt.request_id == request.request_id
    assert receipt.host_id == request.host_id
    assert receipt.repository_id == request.repository_id
    assert receipt.purpose == request.purpose
    assert receipt.policy_sha256 == policy_sha
    assert receipt.character_count == len(text)
    assert receipt.redaction_count == redaction.redaction_count
    assert receipt.source_refs == source_refs
    assert "secret API_TOKEN=abc123" not in receipt.model_dump_json()

    cursor = connection.execute(
        "SELECT content_sha256, character_count, redaction_count "
        "FROM disclosure_receipts WHERE request_id = ?",
        (request.request_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == receipt.content_sha256
    assert row[1] == receipt.character_count
    assert row[2] == receipt.redaction_count
