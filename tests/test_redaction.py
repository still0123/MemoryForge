from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from memoryforge.egress_models import (
    EgressClass,
    EgressRequest,
    SourceEgressRule,
)
from memoryforge.egress_policy import (
    SCHEMA_SQL,
    decide_egress,
    upsert_rule,
)
from memoryforge.models import Sensitivity
from memoryforge.redaction import PATTERNS, redact_for_model


def test_pem_private_key_redacted() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0jF5xqM8fK7e8bBcP1VdPpL6c2T1G6Jk4T0y7N3oQjJ8K\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = f"Some config with key:\n{pem}\nRest of file."
    result = redact_for_model(text)
    assert "<redacted:pem_key>" in result.redacted_text
    assert "MIIEowIBAAKCAQEA0jF5xqM8" not in result.redacted_text
    assert result.redaction_count >= 1
    first = result.replacements[0]
    start, end, tag = first
    assert tag == "pem_key"
    assert text[start:end].startswith("-----BEGIN RSA PRIVATE KEY-----")


def test_bearer_token_header_redacted() -> None:
    headers = "authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.rest.of.token"
    result = redact_for_model(headers)
    assert "<redacted:bearer_token>" in result.redacted_text
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result.redacted_text
    assert result.redaction_count == 1
    start, end, tag = result.replacements[0]
    assert tag == "bearer_token"
    assert headers[start:end].lower().startswith("authorization: bearer ")


def test_github_token_redacted() -> None:
    text = "Using token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD for access."
    result = redact_for_model(text)
    assert "<redacted:github_token>" in result.redacted_text
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD" not in result.redacted_text
    assert result.redaction_count == 1


def test_openai_api_key_redacted() -> None:
    text = "Export OPENAI_KEY=sk-1234567890abcdefghijklmnopqrstuvwxyz now."
    result = redact_for_model(text)
    assert "<redacted:openai_key>" in result.redacted_text
    assert "sk-1234567890abcdefghijklmnopqrstuvwxyz" not in result.redacted_text
    assert result.redaction_count == 1


def test_env_secret_key_value_redacted() -> None:
    text = (
        "DB_HOST=localhost\n"
        "API_TOKEN=supersecretvalue123\n"
        "APP_NAME=myservice\n"
        "MY_PASSWORD=anotherSecret\n"
        "api_key=12345-abcde\n"
    )
    result = redact_for_model(text)
    assert "DB_HOST=localhost" in result.redacted_text
    assert "APP_NAME=myservice" in result.redacted_text
    assert "<redacted:env_secret>" in result.redacted_text
    assert "supersecretvalue123" not in result.redacted_text
    assert "anotherSecret" not in result.redacted_text
    assert "12345-abcde" not in result.redacted_text
    assert result.redaction_count >= 3


def test_user_private_tag_redacted() -> None:
    text = "Public paragraph.\n<private>user only content with password=abc</private>\nMore public text."
    result = redact_for_model(text)
    assert "<redacted:user_private>" in result.redacted_text
    assert "user only content with password=abc" not in result.redacted_text
    assert "Public paragraph." in result.redacted_text
    assert "More public text." in result.redacted_text
    assert result.redaction_count == 1


def test_redaction_count_zero_for_plain_text() -> None:
    text = (
        "This is a perfectly ordinary document. It describes an algorithm. "
        "It talks about functions and variables. Nothing here is a secret."
    )
    result = redact_for_model(text)
    assert result.redacted_text == text
    assert result.redaction_count == 0
    assert result.replacements == ()


def test_replacements_match_original_positions() -> None:
    original = "Prefix. API_TOKEN=value123. Middle. DB_PASSWORD=x y z. Suffix."
    result = redact_for_model(original)
    assert result.redaction_count >= 2
    for start, end, tag in result.replacements:
        assert tag in {"env_secret"}
        assert 0 <= start < end <= len(original)
        segment = original[start:end]
        assert "=" in segment


def test_never_model_not_bypassed_by_redaction() -> None:
    connection = sqlite3.connect(":memory:")
    for statement in SCHEMA_SQL:
        connection.execute(statement)

    source_id = "a" * 64
    request = EgressRequest(
        request_id="req-never",
        host_id="host-any",
        repository_id="r" * 64,
        purpose="context",
        max_characters=1000,
    )

    decision_before = decide_egress(
        connection,
        request=request,
        source_id=source_id,
        source_version=1,
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    assert decision_before.allowed is False
    assert decision_before.reason_code == "never_model_default"

    text_with_secret = "LOCAL_ONLY doc. API_PASSWORD=letmein. More text."
    redacted = redact_for_model(text_with_secret)
    assert redacted.redaction_count >= 1
    assert "API_PASSWORD=letmein" not in redacted.redacted_text

    decision_after = decide_egress(
        connection,
        request=request,
        source_id=source_id,
        source_version=1,
        sensitivity=Sensitivity.LOCAL_ONLY,
    )
    assert decision_after.allowed is False
    assert decision_after.reason_code == "never_model_default"
    assert decision_after.egress_class is EgressClass.NEVER_MODEL
