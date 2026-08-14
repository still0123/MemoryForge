"""Deterministic risk classification and diff assessment for proposed Wiki changes.

Risk is a pure function of origin, operation type, diff structure, page
ownership and source trust. It never consults model self-reported confidence,
so every decision can be replayed offline (§5.1, §9.4, §10).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from memoryforge.core.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeOrigin,
    OperationAssessment,
    RiskLevel,
    SourceTrust,
    ValidationCheck,
    ValidationReport,
)

# Stable reason codes (§20.2). These feed UI badges, tests, and statistics and
# must not change between releases.
AUTO_MECHANICALLY_VERIFIED = "AUTO_MECHANICALLY_VERIFIED"
AUTO_TRUSTED_SOURCE = "AUTO_TRUSTED_SOURCE"
REVIEW_LLM_SYNTHESIS = "REVIEW_LLM_SYNTHESIS"
REVIEW_AGENT_PROPOSAL = "REVIEW_AGENT_PROPOSAL"
REVIEW_CROSS_SOURCE_MERGE = "REVIEW_CROSS_SOURCE_MERGE"
REVIEW_ARCHIVE = "REVIEW_ARCHIVE"
REVIEW_PROTECTED_PAGE = "REVIEW_PROTECTED_PAGE"
REVIEW_UNTRUSTED_SOURCE = "REVIEW_UNTRUSTED_SOURCE"
REVIEW_UNKNOWN_ORIGIN = "REVIEW_UNKNOWN_ORIGIN"
REVIEW_USER_AUTHORED = "REVIEW_USER_AUTHORED"
REVIEW_MODERATE = "REVIEW_MODERATE"
REVIEW_OVER_LIMIT = "REVIEW_OVER_LIMIT"
REVIEW_CRITICAL = "REVIEW_CRITICAL"
REVIEW_MANUAL_PROFILE = "REVIEW_MANUAL_PROFILE"
BLOCK_CITATION_REPLAY_FAILED = "BLOCK_CITATION_REPLAY_FAILED"
BLOCK_STALE_SOURCE_VERSION = "BLOCK_STALE_SOURCE_VERSION"
BLOCK_BASE_COMMIT_CHANGED = "BLOCK_BASE_COMMIT_CHANGED"
BLOCK_CANDIDATE_LINT_FAILED = "BLOCK_CANDIDATE_LINT_FAILED"
BLOCK_PRIVACY_EXPANSION = "BLOCK_PRIVACY_EXPANSION"

MECHANICAL_ORIGINS = frozenset(
    {
        ChangeOrigin.DETERMINISTIC_IMPORT,
        ChangeOrigin.CODE_INDEX,
        ChangeOrigin.DETERMINISTIC_NAVIGATION,
        ChangeOrigin.DETERMINISTIC_CLEANUP,
    }
)

_LLM_ORIGINS = frozenset({ChangeOrigin.LLM_COMPILATION, ChangeOrigin.LLM_MODULE_SYNTHESIS})

_RISK_RANK = {
    RiskLevel.LOW: 0,
    RiskLevel.MODERATE: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_GENERATED_FIELD = re.compile(r"^(generated_at|updated_at|rendered_at):\s*")


@dataclass(frozen=True)
class DiffAssessment:
    created: bool
    archived: bool
    changed_lines: int
    frontmatter_changed: bool
    body_changed: bool
    facts_changed: bool
    navigation_changed: bool
    semantic_noop: bool


def diff_assessment(
    before: str,
    after: str,
    *,
    operation_type: ChangeOperationType,
) -> DiffAssessment:
    """Classify one candidate against its stable-tree predecessor without an LLM."""
    if operation_type is ChangeOperationType.ARCHIVE_PAGE:
        return DiffAssessment(
            created=False,
            archived=True,
            changed_lines=len(before.splitlines()),
            frontmatter_changed=False,
            body_changed=True,
            facts_changed=True,
            navigation_changed=False,
            semantic_noop=False,
        )
    created = operation_type is ChangeOperationType.CREATE_PAGE
    before_frontmatter, before_body = _split_frontmatter(before)
    after_frontmatter, after_body = _split_frontmatter(after)
    frontmatter_changed = before_frontmatter != after_frontmatter
    body_changed = before_body != after_body
    return DiffAssessment(
        created=created,
        archived=False,
        changed_lines=_changed_line_count(before, after),
        frontmatter_changed=frontmatter_changed,
        body_changed=body_changed,
        facts_changed=_citation_count(before_body) != _citation_count(after_body),
        navigation_changed=frontmatter_changed and not body_changed,
        semantic_noop=_normalize_for_noop(before) == _normalize_for_noop(after),
    )


def classify_risk(
    *,
    origin: ChangeOrigin | None,
    operation_type: ChangeOperationType,
    source_count: int,
    touches_user_protected_content: bool = False,
    source_trust: SourceTrust = SourceTrust.STANDARD,
) -> tuple[RiskLevel, tuple[str, ...]]:
    """Map one operation to a risk level and stable reason codes (§10).

    The highest-risk property decides the whole page (§5.2). Archive and
    protected-page changes are always CRITICAL; LLM/Agent/untrusted changes are
    always HIGH; mechanically-verified single-source changes are LOW.
    """
    if touches_user_protected_content:
        return RiskLevel.CRITICAL, (REVIEW_PROTECTED_PAGE,)
    if operation_type is ChangeOperationType.ARCHIVE_PAGE:
        return RiskLevel.CRITICAL, (REVIEW_ARCHIVE,)
    if origin is None:
        return RiskLevel.HIGH, (REVIEW_UNKNOWN_ORIGIN,)
    if origin in _LLM_ORIGINS:
        return RiskLevel.HIGH, (REVIEW_LLM_SYNTHESIS,)
    if origin is ChangeOrigin.AGENT_PROPOSAL:
        return RiskLevel.HIGH, (REVIEW_AGENT_PROPOSAL,)
    if origin is ChangeOrigin.USER_AUTHORED:
        return RiskLevel.HIGH, (REVIEW_USER_AUTHORED,)
    if source_trust is SourceTrust.UNTRUSTED:
        return RiskLevel.HIGH, (REVIEW_UNTRUSTED_SOURCE,)
    if origin is ChangeOrigin.DETERMINISTIC_NAVIGATION:
        # Navigation projection aggregates accepted pages, not source evidence.
        return RiskLevel.LOW, (AUTO_MECHANICALLY_VERIFIED,)
    if source_count > 1:
        return RiskLevel.MODERATE, (REVIEW_CROSS_SOURCE_MERGE,)
    if origin is ChangeOrigin.DETERMINISTIC_CLEANUP:
        return RiskLevel.MODERATE, (REVIEW_MODERATE,)
    return RiskLevel.LOW, (AUTO_MECHANICALLY_VERIFIED,)


def assess_operation(
    operation: ChangeOperation,
    *,
    before: str,
    after: str,
    source_count: int,
    source_trust: SourceTrust = SourceTrust.STANDARD,
    touches_user_protected_content: bool = False,
) -> OperationAssessment:
    """Build the per-operation assessment used by the decision engine."""
    diff = diff_assessment(before, after, operation_type=operation.type)
    risk, reason_codes = classify_risk(
        origin=operation.origin,
        operation_type=operation.type,
        source_count=source_count,
        touches_user_protected_content=touches_user_protected_content,
        source_trust=source_trust,
    )
    return OperationAssessment(
        path=operation.path,
        origin=operation.origin,
        operation_type=operation.type,
        risk=risk,
        reason_codes=reason_codes,
        changed_lines=diff.changed_lines,
        source_count=source_count,
        touches_verified_facts=diff.facts_changed,
        touches_user_protected_content=touches_user_protected_content,
    )


def change_set_risk(
    assessments: tuple[OperationAssessment, ...],
    *,
    low_max_changed_pages: int,
    low_max_changed_lines: int,
) -> tuple[RiskLevel, tuple[str, ...]]:
    """Take the highest page risk; bump LOW over the diff limit to MODERATE."""
    if not assessments:
        return RiskLevel.LOW, ()
    risk = max((item.risk for item in assessments), key=_RISK_RANK.__getitem__)
    over_limit = _over_limit(assessments, low_max_changed_pages, low_max_changed_lines)
    if risk is RiskLevel.LOW and over_limit:
        risk = RiskLevel.MODERATE
    codes = list(dict.fromkeys(code for item in assessments for code in item.reason_codes))
    if risk is RiskLevel.MODERATE and over_limit and REVIEW_OVER_LIMIT not in codes:
        codes.append(REVIEW_OVER_LIMIT)
    if risk is RiskLevel.MODERATE and not any(code.startswith("REVIEW") for code in codes):
        codes.append(REVIEW_MODERATE)
    if risk is RiskLevel.CRITICAL and not any(code.startswith("REVIEW") for code in codes):
        codes.insert(0, REVIEW_CRITICAL)
    return risk, tuple(codes)


def _over_limit(
    assessments: tuple[OperationAssessment, ...],
    low_max_changed_pages: int,
    low_max_changed_lines: int,
) -> bool:
    return (
        len(assessments) > low_max_changed_pages
        or sum(item.changed_lines for item in assessments) > low_max_changed_lines
    )


def build_validation_report(
    *,
    checks: tuple[ValidationCheck, ...],
    candidate_tree_sha256: str,
    source_versions_sha256: str,
) -> ValidationReport:
    return ValidationReport(
        schema_version=1,
        checks=checks,
        candidate_tree_sha256=candidate_tree_sha256,
        source_versions_sha256=source_versions_sha256,
        generated_at=datetime.now(UTC),
    )


def candidate_tree_sha256(candidate_files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate_files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(candidate_files[path].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def source_versions_sha256(source_versions: Mapping[str, int]) -> str:
    payload = "".join(f"{key}:{value}\n" for key, value in sorted(source_versions.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _changed_line_count(before: str, after: str) -> int:
    if before == after:
        return 0
    matcher = difflib.SequenceMatcher(
        a=before.splitlines(),
        b=after.splitlines(),
        autojunk=False,
    )
    return sum(
        0 if tag == "equal" else max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    )


def _citation_count(body: str) -> int:
    return body.count("[^")


def _split_frontmatter(content: str) -> tuple[str, str]:
    match = _FRONTMATTER.match(content)
    if not match:
        return "", content
    return match.group(1), content[match.end() :]


def _normalize_for_noop(content: str) -> str:
    """Ignore generated timestamp fields and trailing whitespace for NOOP checks."""
    lines: list[str] = []
    in_frontmatter = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            lines.append(stripped)
            continue
        if in_frontmatter and _GENERATED_FIELD.match(stripped):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()
