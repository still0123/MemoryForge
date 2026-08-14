"""Unit tests for the automation policy: precedence, hash, and decisions (§7, §17, §25.1)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from memoryforge.automation.automation_policy import (
    PROFILE_AUTONOMOUS,
    PROFILE_BALANCED,
    PROFILE_MANUAL,
    AutomationPolicy,
    decide,
    default_policy,
    effective_profile,
    effective_trust,
    evaluate,
    load_policy,
    policy_sha256,
    save_policy,
)
from memoryforge.automation.automation_validation import (
    AUTO_MECHANICALLY_VERIFIED,
    AUTO_TRUSTED_SOURCE,
    REVIEW_CRITICAL,
    REVIEW_CROSS_SOURCE_MERGE,
    REVIEW_LLM_SYNTHESIS,
    REVIEW_UNKNOWN_ORIGIN,
    classify_risk,
)
from memoryforge.core.models import (
    AutomationDecision,
    ChangeOperationType,
    RiskLevel,
    SourceTrust,
)


def test_default_policy_is_balanced() -> None:
    assert default_policy().profile == PROFILE_BALANCED


def test_canonical_hash_is_stable_across_field_order() -> None:
    a = AutomationPolicy.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "profile": "balanced",
                "limits": {"low_max_changed_pages": 10, "low_max_changed_lines": 20},
            }
        )
    )
    b = AutomationPolicy.model_validate_json(
        json.dumps(
            {
                "profile": "balanced",
                "limits": {"low_max_changed_lines": 20, "low_max_changed_pages": 10},
                "schema_version": 1,
            }
        )
    )
    assert policy_sha256(a) == policy_sha256(b)


def test_unknown_policy_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AutomationPolicy.model_validate_json(
            json.dumps({"profile": "balanced", "extra_field": True})
        )


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutomationPolicy.model_validate_json(json.dumps({"profile": "yolo"}))


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutomationPolicy.model_validate_json(json.dumps({"schema_version": 999}))


def test_policy_roundtrip(tmp_path) -> None:
    policy = AutomationPolicy(profile=PROFILE_AUTONOMOUS)
    save_policy(tmp_path, policy)
    loaded = load_policy(tmp_path)
    assert loaded == policy
    assert policy_sha256(loaded) == policy_sha256(policy)


def test_load_policy_returns_default_when_absent(tmp_path) -> None:
    assert load_policy(tmp_path) == default_policy()


def test_manual_profile_never_auto_applies() -> None:
    policy = AutomationPolicy(profile=PROFILE_MANUAL)
    result = evaluate(policy, profile=PROFILE_MANUAL, risk=RiskLevel.LOW)
    assert result.decision == AutomationDecision.REVIEW_REQUIRED


def test_balanced_auto_applies_low() -> None:
    policy = default_policy()
    result = evaluate(policy, profile=PROFILE_BALANCED, risk=RiskLevel.LOW)
    assert result.decision == AutomationDecision.AUTO_APPLY
    assert AUTO_MECHANICALLY_VERIFIED in result.reason_codes


def test_balanced_reviews_high() -> None:
    policy = default_policy()
    result = evaluate(
        policy,
        profile=PROFILE_BALANCED,
        risk=RiskLevel.HIGH,
        reason_codes=(REVIEW_LLM_SYNTHESIS,),
    )
    assert result.decision == AutomationDecision.REVIEW_REQUIRED


def test_trusted_source_adds_auto_trusted_reason() -> None:
    policy = default_policy()
    result = evaluate(
        policy,
        profile=PROFILE_BALANCED,
        risk=RiskLevel.LOW,
        source_trust=SourceTrust.TRUSTED,
    )
    assert AUTO_TRUSTED_SOURCE in result.reason_codes


def test_critical_never_auto_applies_even_autonomous() -> None:
    policy = AutomationPolicy(profile=PROFILE_AUTONOMOUS)
    result = evaluate(
        policy,
        profile=PROFILE_AUTONOMOUS,
        risk=RiskLevel.CRITICAL,
        reason_codes=(REVIEW_CRITICAL,),
    )
    assert result.decision == AutomationDecision.REVIEW_REQUIRED
    assert REVIEW_CRITICAL in result.reason_codes


def test_autonomous_allows_trusted_cross_source_when_enabled() -> None:
    policy = AutomationPolicy(
        profile=PROFILE_AUTONOMOUS,
        moderate={"allow_deterministic_cross_source": True},
    )
    result = evaluate(
        policy,
        profile=PROFILE_AUTONOMOUS,
        risk=RiskLevel.MODERATE,
        reason_codes=(REVIEW_CROSS_SOURCE_MERGE,),
        source_trust=SourceTrust.TRUSTED,
    )
    assert result.decision == AutomationDecision.AUTO_APPLY
    assert AUTO_TRUSTED_SOURCE in result.reason_codes


def test_autonomous_denies_cross_source_when_disabled() -> None:
    policy = AutomationPolicy(profile=PROFILE_AUTONOMOUS)
    result = evaluate(
        policy,
        profile=PROFILE_AUTONOMOUS,
        risk=RiskLevel.MODERATE,
        reason_codes=(REVIEW_CROSS_SOURCE_MERGE,),
        source_trust=SourceTrust.TRUSTED,
    )
    assert result.decision == AutomationDecision.REVIEW_REQUIRED


def test_autonomous_denies_moderate_from_standard_source() -> None:
    policy = AutomationPolicy(
        profile=PROFILE_AUTONOMOUS,
        moderate={"allow_deterministic_cross_source": True},
    )
    result = evaluate(
        policy,
        profile=PROFILE_AUTONOMOUS,
        risk=RiskLevel.MODERATE,
        reason_codes=(REVIEW_CROSS_SOURCE_MERGE,),
        source_trust=SourceTrust.STANDARD,
    )
    assert result.decision == AutomationDecision.REVIEW_REQUIRED


def test_hard_block_wins_over_profile() -> None:
    policy = default_policy()
    result = decide(
        policy,
        profile=PROFILE_BALANCED,
        risk=RiskLevel.LOW,
        block_reasons=("BLOCK_BASE_COMMIT_CHANGED",),
    )
    assert result.decision == AutomationDecision.BLOCKED
    assert "BLOCK_BASE_COMMIT_CHANGED" in result.reason_codes


def test_effective_profile_conflicting_overrides_fall_back_to_manual() -> None:
    policy = AutomationPolicy(
        source_overrides={
            "a" * 64: {"profile": PROFILE_AUTONOMOUS},
            "b" * 64: {"profile": PROFILE_BALANCED},
        }
    )
    assert effective_profile(policy, ["a" * 64, "b" * 64]) == PROFILE_MANUAL
    assert effective_profile(policy, ["a" * 64]) == PROFILE_AUTONOMOUS
    assert effective_profile(policy, ["c" * 64]) == PROFILE_BALANCED


def test_effective_trust_uses_most_restrictive() -> None:
    policy = AutomationPolicy(source_overrides={"a" * 64: {"trust": "trusted"}})
    trust = effective_trust(
        policy,
        ["a" * 64, "b" * 64],
        defaults={"b" * 64: SourceTrust.UNTRUSTED},
    )
    assert trust == SourceTrust.UNTRUSTED


def test_effective_trust_defaults_to_standard() -> None:
    policy = default_policy()
    assert effective_trust(policy, ["a" * 64], defaults={}) == SourceTrust.STANDARD
    assert effective_trust(policy, [], defaults={}) == SourceTrust.STANDARD


def test_old_changeset_without_origin_is_manual_review() -> None:
    risk, codes = classify_risk(
        origin=None,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.HIGH
    assert REVIEW_UNKNOWN_ORIGIN in codes
    policy = default_policy()
    result = decide(policy, profile=PROFILE_BALANCED, risk=risk, reason_codes=codes)
    assert result.decision == AutomationDecision.REVIEW_REQUIRED
