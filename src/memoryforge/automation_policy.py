"""Versioned automation policy: load, normalize, hash, and evaluate.

The policy is stored as canonical JSON under ``.memoryforge/policy.json`` and
hashed with SHA256. Unknown fields are rejected so a typo can never silently
relax automation (§17). Evaluation is a pure function of risk, reason codes,
profile and source trust, so decisions replay deterministically (§5.1).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memoryforge.automation_validation import (
    AUTO_MECHANICALLY_VERIFIED,
    AUTO_TRUSTED_SOURCE,
    REVIEW_ARCHIVE,
    REVIEW_CRITICAL,
    REVIEW_CROSS_SOURCE_MERGE,
    REVIEW_MANUAL_PROFILE,
)
from memoryforge.models import AutomationDecision, RiskLevel, SourceTrust

POLICY_FILENAME = "policy.json"
POLICY_SCHEMA_VERSION = 1
MAX_POLICY_BYTES = 64 * 1024

PROFILE_MANUAL = "manual"
PROFILE_BALANCED = "balanced"
PROFILE_AUTONOMOUS = "autonomous"
PROFILE_CUSTOM = "custom"
BUILTIN_PROFILES = frozenset(
    {PROFILE_MANUAL, PROFILE_BALANCED, PROFILE_AUTONOMOUS, PROFILE_CUSTOM}
)

DEFAULT_LOW_MAX_CHANGED_PAGES = 25
DEFAULT_LOW_MAX_CHANGED_LINES = 800

_TRUST_RANK = {SourceTrust.UNTRUSTED: 0, SourceTrust.STANDARD: 1, SourceTrust.TRUSTED: 2}


class PolicyLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    low_max_changed_pages: int = Field(default=DEFAULT_LOW_MAX_CHANGED_PAGES, ge=1)
    low_max_changed_lines: int = Field(default=DEFAULT_LOW_MAX_CHANGED_LINES, ge=1)


class PolicyModerate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allow_deterministic_cross_source: bool = False
    allow_single_source_archive: bool = False


class SourceOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trust: SourceTrust = SourceTrust.STANDARD
    profile: str | None = None
    allow_single_source_archive: bool = False

    @model_validator(mode="after")
    def validate_profile(self) -> SourceOverride:
        if self.profile is not None and self.profile not in BUILTIN_PROFILES:
            raise ValueError(f"unknown profile: {self.profile}")
        return self


class AutomationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=POLICY_SCHEMA_VERSION, ge=1)
    profile: str = PROFILE_BALANCED
    limits: PolicyLimits = Field(default_factory=PolicyLimits)
    moderate: PolicyModerate = Field(default_factory=PolicyModerate)
    source_overrides: dict[str, SourceOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_profile(self) -> AutomationPolicy:
        if self.profile not in BUILTIN_PROFILES:
            raise ValueError(f"unknown profile: {self.profile}")
        if self.schema_version != POLICY_SCHEMA_VERSION:
            raise ValueError(f"unsupported policy schema_version: {self.schema_version}")
        return self


@dataclass(frozen=True)
class PolicyEvaluation:
    decision: AutomationDecision
    risk: RiskLevel
    reason_codes: tuple[str, ...]
    policy_id: str
    policy_sha256: str


def default_policy() -> AutomationPolicy:
    """Return the built-in balanced profile for new Workspaces (§7.1)."""
    return AutomationPolicy()


def canonical_json(policy: AutomationPolicy) -> str:
    return json.dumps(
        policy.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def policy_sha256(policy: AutomationPolicy) -> str:
    return hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest()


def policy_path(workspace: Path) -> Path:
    return workspace / ".memoryforge" / POLICY_FILENAME


def load_policy(workspace: Path) -> AutomationPolicy:
    """Read the policy file, or return the balanced default when absent."""
    path = policy_path(workspace)
    if not path.exists():
        return default_policy()
    if path.is_symlink():
        raise ValueError("automation policy must not be a symlink")
    payload = path.read_bytes()
    if len(payload) > MAX_POLICY_BYTES:
        raise ValueError("automation policy exceeds its size limit")
    return AutomationPolicy.model_validate_json(payload)


def save_policy(workspace: Path, policy: AutomationPolicy) -> AutomationPolicy:
    """Atomically write a normalized policy with 0600 permissions (§17)."""
    path = policy_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("automation policy path is unsafe")
    rendered = (policy.model_dump_json(indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".policy.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(rendered)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    os.chmod(path, 0o600)
    return policy


def effective_profile(policy: AutomationPolicy, source_ids: Sequence[str]) -> str:
    """Resolve the profile from source overrides; conflicts fall back to manual."""
    profiles: set[str] = set()
    for source_id in source_ids:
        override = policy.source_overrides.get(source_id)
        if override is not None and override.profile is not None:
            profiles.add(override.profile)
    if not profiles:
        return policy.profile
    if len(profiles) == 1:
        return next(iter(profiles))
    return PROFILE_MANUAL


def resolve_source_trust(
    policy: AutomationPolicy,
    source_id: str,
    *,
    default: SourceTrust,
) -> SourceTrust:
    override = policy.source_overrides.get(source_id)
    if override is not None:
        return override.trust
    return default


def effective_trust(
    policy: AutomationPolicy,
    source_ids: Sequence[str],
    defaults: Mapping[str, SourceTrust],
) -> SourceTrust:
    """Take the most restrictive trust across a ChangeSet's sources (§7.2)."""
    if not source_ids:
        return SourceTrust.STANDARD
    return min(
        (
            resolve_source_trust(
                policy, source_id, default=defaults.get(source_id, SourceTrust.STANDARD)
            )
            for source_id in source_ids
        ),
        key=_TRUST_RANK.__getitem__,
    )


def evaluate(
    policy: AutomationPolicy,
    *,
    profile: str,
    risk: RiskLevel,
    reason_codes: tuple[str, ...] = (),
    source_trust: SourceTrust = SourceTrust.STANDARD,
) -> PolicyEvaluation:
    """Map a validated risk to an automation decision for one profile (§7)."""
    digest = policy_sha256(policy)
    if risk is RiskLevel.CRITICAL:
        return PolicyEvaluation(
            AutomationDecision.REVIEW_REQUIRED,
            risk,
            (REVIEW_CRITICAL, *reason_codes),
            profile,
            digest,
        )
    if profile == PROFILE_MANUAL:
        return PolicyEvaluation(
            AutomationDecision.REVIEW_REQUIRED,
            risk,
            (REVIEW_MANUAL_PROFILE, *reason_codes),
            profile,
            digest,
        )
    if risk is RiskLevel.LOW:
        codes = [AUTO_MECHANICALLY_VERIFIED]
        if source_trust is SourceTrust.TRUSTED:
            codes.append(AUTO_TRUSTED_SOURCE)
        codes.extend(reason_codes)
        return PolicyEvaluation(
            AutomationDecision.AUTO_APPLY,
            risk,
            tuple(dict.fromkeys(codes)),
            profile,
            digest,
        )
    if (
        profile == PROFILE_AUTONOMOUS
        and risk is RiskLevel.MODERATE
        and _autonomous_moderate_allowed(policy, reason_codes, source_trust)
    ):
        return PolicyEvaluation(
            AutomationDecision.AUTO_APPLY,
            risk,
            tuple(dict.fromkeys((AUTO_TRUSTED_SOURCE, *reason_codes))),
            profile,
            digest,
        )
    return PolicyEvaluation(AutomationDecision.REVIEW_REQUIRED, risk, reason_codes, profile, digest)


def decide(
    policy: AutomationPolicy,
    *,
    profile: str,
    risk: RiskLevel,
    reason_codes: tuple[str, ...] = (),
    source_trust: SourceTrust = SourceTrust.STANDARD,
    block_reasons: tuple[str, ...] = (),
) -> PolicyEvaluation:
    """Shared decision engine: hard blocks win, then profile evaluation (§7.3)."""
    if block_reasons:
        return PolicyEvaluation(
            AutomationDecision.BLOCKED,
            risk,
            tuple(dict.fromkeys(block_reasons)),
            profile,
            policy_sha256(policy),
        )
    return evaluate(
        policy,
        profile=profile,
        risk=risk,
        reason_codes=reason_codes,
        source_trust=source_trust,
    )


def _autonomous_moderate_allowed(
    policy: AutomationPolicy,
    reason_codes: tuple[str, ...],
    source_trust: SourceTrust,
) -> bool:
    if source_trust is not SourceTrust.TRUSTED:
        return False
    if REVIEW_CROSS_SOURCE_MERGE in reason_codes:
        return policy.moderate.allow_deterministic_cross_source
    if REVIEW_ARCHIVE in reason_codes:
        return policy.moderate.allow_single_source_archive
    return False
