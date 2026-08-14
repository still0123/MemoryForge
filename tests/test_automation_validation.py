"""Unit tests for deterministic risk classification and diff assessment (§10, §25.1).

Each risk rule and its stable reason code is covered, plus the highest-risk-wins
page-atomicity rule and the semantic NOOP diff heuristic.
"""

from __future__ import annotations

from memoryforge.automation.automation_validation import (
    AUTO_MECHANICALLY_VERIFIED,
    REVIEW_AGENT_PROPOSAL,
    REVIEW_ARCHIVE,
    REVIEW_CROSS_SOURCE_MERGE,
    REVIEW_LLM_SYNTHESIS,
    REVIEW_MODERATE,
    REVIEW_OVER_LIMIT,
    REVIEW_PROTECTED_PAGE,
    REVIEW_UNKNOWN_ORIGIN,
    REVIEW_UNTRUSTED_SOURCE,
    REVIEW_USER_AUTHORED,
    change_set_risk,
    classify_risk,
    diff_assessment,
)
from memoryforge.core.models import (
    ChangeOperationType,
    ChangeOrigin,
    OperationAssessment,
    RiskLevel,
    SourceTrust,
)


def test_deterministic_single_source_import_is_low() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.DETERMINISTIC_IMPORT,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.LOW
    assert AUTO_MECHANICALLY_VERIFIED in codes


def test_code_index_is_low() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.CODE_INDEX,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.LOW
    assert AUTO_MECHANICALLY_VERIFIED in codes


def test_navigation_aggregates_sources_without_cross_source_penalty() -> None:
    # Navigation is a projection over accepted pages, not a source combination.
    risk, codes = classify_risk(
        origin=ChangeOrigin.DETERMINISTIC_NAVIGATION,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=7,
    )
    assert risk == RiskLevel.LOW
    assert REVIEW_CROSS_SOURCE_MERGE not in codes


def test_deterministic_cross_source_merge_is_moderate() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.DETERMINISTIC_IMPORT,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=2,
    )
    assert risk == RiskLevel.MODERATE
    assert REVIEW_CROSS_SOURCE_MERGE in codes


def test_deterministic_cleanup_is_moderate() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.DETERMINISTIC_CLEANUP,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.MODERATE
    assert REVIEW_MODERATE in codes


def test_llm_compilation_is_high() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.LLM_COMPILATION,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.HIGH
    assert REVIEW_LLM_SYNTHESIS in codes


def test_llm_module_synthesis_is_high() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.LLM_MODULE_SYNTHESIS,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.HIGH
    assert REVIEW_LLM_SYNTHESIS in codes


def test_agent_proposal_is_high() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.AGENT_PROPOSAL,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.HIGH
    assert REVIEW_AGENT_PROPOSAL in codes


def test_user_authored_is_high() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.USER_AUTHORED,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.HIGH
    assert REVIEW_USER_AUTHORED in codes


def test_missing_origin_is_high_unknown_origin() -> None:
    # Legacy ChangeSets (pre-origin) must downgrade to manual review, not auto-apply.
    risk, codes = classify_risk(
        origin=None,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
    )
    assert risk == RiskLevel.HIGH
    assert REVIEW_UNKNOWN_ORIGIN in codes


def test_untrusted_source_is_high() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.DETERMINISTIC_IMPORT,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
        source_trust=SourceTrust.UNTRUSTED,
    )
    assert risk == RiskLevel.HIGH
    assert REVIEW_UNTRUSTED_SOURCE in codes


def test_archive_is_critical() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.DETERMINISTIC_CLEANUP,
        operation_type=ChangeOperationType.ARCHIVE_PAGE,
        source_count=1,
        source_trust=SourceTrust.TRUSTED,
    )
    assert risk == RiskLevel.CRITICAL
    assert REVIEW_ARCHIVE in codes


def test_protected_page_is_critical_even_mechanical() -> None:
    risk, codes = classify_risk(
        origin=ChangeOrigin.DETERMINISTIC_IMPORT,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        source_count=1,
        touches_user_protected_content=True,
    )
    assert risk == RiskLevel.CRITICAL
    assert REVIEW_PROTECTED_PAGE in codes


def _assessment(
    path: str,
    origin: ChangeOrigin | None,
    risk: RiskLevel,
    codes: tuple[str, ...],
    changed_lines: int = 1,
) -> OperationAssessment:
    return OperationAssessment(
        path=path,
        origin=origin,
        operation_type=ChangeOperationType.UPDATE_PAGE,
        risk=risk,
        reason_codes=codes,
        changed_lines=changed_lines,
        source_count=1,
    )


def test_change_set_risk_takes_highest_page() -> None:
    low = _assessment(
        "wiki/pages/a.md",
        ChangeOrigin.DETERMINISTIC_IMPORT,
        RiskLevel.LOW,
        (AUTO_MECHANICALLY_VERIFIED,),
    )
    high = _assessment(
        "wiki/pages/b.md",
        ChangeOrigin.LLM_COMPILATION,
        RiskLevel.HIGH,
        (REVIEW_LLM_SYNTHESIS,),
    )
    risk, codes = change_set_risk((low, high), low_max_changed_pages=25, low_max_changed_lines=800)
    assert risk == RiskLevel.HIGH
    assert REVIEW_LLM_SYNTHESIS in codes


def test_change_set_risk_over_limit_bumps_low_to_moderate() -> None:
    low = _assessment(
        "wiki/pages/a.md",
        ChangeOrigin.DETERMINISTIC_IMPORT,
        RiskLevel.LOW,
        (AUTO_MECHANICALLY_VERIFIED,),
        changed_lines=900,
    )
    risk, codes = change_set_risk((low,), low_max_changed_pages=25, low_max_changed_lines=800)
    assert risk == RiskLevel.MODERATE
    assert REVIEW_OVER_LIMIT in codes


def test_change_set_risk_over_page_limit_bumps_to_moderate() -> None:
    assessments = tuple(
        _assessment(
            f"wiki/pages/p{i}.md",
            ChangeOrigin.DETERMINISTIC_IMPORT,
            RiskLevel.LOW,
            (AUTO_MECHANICALLY_VERIFIED,),
        )
        for i in range(26)
    )
    risk, codes = change_set_risk(assessments, low_max_changed_pages=25, low_max_changed_lines=800)
    assert risk == RiskLevel.MODERATE
    assert REVIEW_OVER_LIMIT in codes


def test_diff_assessment_detects_semantic_noop_for_generated_timestamp() -> None:
    before = "---\ntitle: T\ngenerated_at: 2026-01-01\n---\nBody.\n"
    after = "---\ntitle: T\ngenerated_at: 2026-01-02\n---\nBody.\n"
    diff = diff_assessment(before, after, operation_type=ChangeOperationType.UPDATE_PAGE)
    assert diff.semantic_noop is True
    assert diff.body_changed is False


def test_diff_assessment_reports_real_body_change() -> None:
    before = "# T\n\nold.\n"
    after = "# T\n\nnew.\n"
    diff = diff_assessment(before, after, operation_type=ChangeOperationType.UPDATE_PAGE)
    assert diff.body_changed is True
    assert diff.semantic_noop is False
    assert diff.changed_lines > 0


def test_diff_assessment_detects_citation_change() -> None:
    before = "Body with [^1].\n"
    after = "Body without.\n"
    diff = diff_assessment(before, after, operation_type=ChangeOperationType.UPDATE_PAGE)
    assert diff.facts_changed is True


def test_diff_assessment_archive_is_flagged() -> None:
    diff = diff_assessment("old.\n", "", operation_type=ChangeOperationType.ARCHIVE_PAGE)
    assert diff.archived is True
    assert diff.changed_lines > 0


def test_diff_assessment_create_is_flagged() -> None:
    diff = diff_assessment("", "new.\n", operation_type=ChangeOperationType.CREATE_PAGE)
    assert diff.created is True
