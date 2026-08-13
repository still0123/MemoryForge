from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceId = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_CHAR_LOCATOR = re.compile(r"^chars:(?P<start>\d+)-(?P<end>\d+)$")


class SourceCategory(StrEnum):
    DESIGN = "design"
    POSTMORTEM = "postmortem"
    SUMMARY = "summary"
    NOTES = "notes"
    REFS = "refs"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    LOCAL_ONLY = "local_only"


class ClaimStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class ChangeSetStatus(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class ChangeOperationType(StrEnum):
    CREATE_PAGE = "CREATE_PAGE"
    UPDATE_PAGE = "UPDATE_PAGE"
    ARCHIVE_PAGE = "ARCHIVE_PAGE"
    UPDATE_CLAIM = "UPDATE_CLAIM"


class LintSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ChangeOrigin(StrEnum):
    """Trusted compile-path origin of one Wiki change. Never set by a Provider."""

    DETERMINISTIC_IMPORT = "deterministic_import"
    CODE_INDEX = "code_index"
    DETERMINISTIC_NAVIGATION = "deterministic_navigation"
    DETERMINISTIC_CLEANUP = "deterministic_cleanup"
    LLM_COMPILATION = "llm_compilation"
    LLM_MODULE_SYNTHESIS = "llm_module_synthesis"
    AGENT_PROPOSAL = "agent_proposal"
    USER_AUTHORED = "user_authored"


class RiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class AutomationDecision(StrEnum):
    AUTO_APPLY = "auto_apply"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    NOOP = "noop"


class SourceTrust(StrEnum):
    UNTRUSTED = "untrusted"
    STANDARD = "standard"
    TRUSTED = "trusted"


class ReviewActorType(StrEnum):
    HUMAN = "human"
    POLICY = "policy"


def _validate_wiki_path(path: str) -> None:
    parts = path.split("/")
    if (
        "\\" in path
        or len(parts) < 2
        or parts[0] != "wiki"
        or any(part in {"", ".", ".."} for part in parts)
        or not path.endswith(".md")
    ):
        raise ValueError("Wiki paths must be Markdown files below wiki/ without traversal segments")


class LocalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_uri: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    media_type: Literal["text/markdown", "text/plain"]
    category: SourceCategory
    suffix: Literal[".md", ".markdown", ".txt", ".go", ".py", ".ts", ".tsx"]
    title: str = Field(min_length=1)
    content: str
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    tags: tuple[str, ...] = ()


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["created", "updated", "unchanged"]
    source_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str
    source_uri: str
    category: SourceCategory
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_uri: str
    snapshot_path: str
    observed_at: datetime


class GitRepositoryRecord(BaseModel):
    """A registered local Git checkout."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(min_length=1)
    checkout_path: str = Field(min_length=1)
    remote_name: str | None = None
    remote_url: str | None = None
    sensitivity: Sensitivity = Sensitivity.LOCAL_ONLY
    registered_at: datetime
    last_synced_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40,64}$")


class GitDocumentSyncResult(BaseModel):
    """One documentation file imported from a committed Git snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    relative_path: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    status: Literal["created", "updated", "unchanged"]


class GitRepositorySyncResult(BaseModel):
    """Summary of one local checkout sync."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    head_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    skipped: tuple[str, ...] = ()
    documents: tuple[GitDocumentSyncResult, ...] = ()


class FolderDocumentSyncResult(BaseModel):
    """One local file imported from a recursive folder snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    relative_path: str = Field(min_length=1)
    status: Literal["created", "updated", "unchanged"]


class FolderSyncResult(BaseModel):
    """Deterministic summary of one recursive folder import."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    folder_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    deleted: int = Field(ge=0)
    documents: tuple[FolderDocumentSyncResult, ...] = ()

    @model_validator(mode="after")
    def validate_counts_and_paths(self) -> FolderSyncResult:
        if self.created + self.updated + self.unchanged != len(self.documents):
            raise ValueError("folder sync counts must match documents")
        paths = tuple(document.relative_path for document in self.documents)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("folder sync paths must be sorted and unique")
        return self


class TopicGroup(BaseModel):
    """One model-proposed navigation topic for already compiled source pages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_ids: tuple[SourceId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_topic_group(self) -> TopicGroup:
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("TopicGroup source_ids must not contain duplicates")
        validate_llm_title(self.title)
        validate_llm_summary(self.summary)
        return self


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str
    source_uri: str
    source_path: str
    snapshot_uri: str
    snapshot_path: str
    category: SourceCategory
    snippet: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime


class SourceVersionManifest(BaseModel):
    """Immutable, append-only audit record for one imported SourceVersion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    storage_version: int = Field(default=1, ge=1)
    source_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    legacy_source_id: str | None = Field(default=None, pattern=r"^src_[a-f0-9]{16}$")
    source_uri: str = Field(pattern=r"^mf://source/[a-f0-9]{64}$")
    source_path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_uri: str = Field(pattern=r"^mf://blob/[a-f0-9]{64}$")
    snapshot_path: str = Field(pattern=r"^raw/blobs/")
    media_type: Literal["text/markdown", "text/plain"]
    category: SourceCategory
    title: str = Field(min_length=1)
    observed_at: datetime
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    tags: tuple[str, ...] = ()
    legacy_category: str | None = None

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> SourceVersionManifest:
        if self.snapshot_uri != f"mf://blob/{self.content_sha256}":
            raise ValueError("snapshot_uri must identify the manifest content_sha256")
        return self


class PageCitation(BaseModel):
    """A source locator attached to an LLM-proposed Wiki page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    locator: str = Field(pattern=r"^chars:\d+-\d+$")

    @model_validator(mode="after")
    def validate_locator(self) -> PageCitation:
        match = _CHAR_LOCATOR.fullmatch(self.locator)
        if match is None or int(match.group("end")) <= int(match.group("start")):
            raise ValueError("locator must contain a non-empty character range")
        return self


class PageChange(BaseModel):
    """A reviewable page proposal returned by an LLM compiler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    page_type: Literal["entity", "concept", "synthesis"]
    summary: str = Field(min_length=1)
    body: str = Field(min_length=1)
    source_ids: tuple[SourceId, ...] = Field(min_length=1)
    citations: tuple[PageCitation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_page_change(self) -> PageChange:
        filename = self.path.removeprefix("wiki/pages/")
        if (
            "\\" in self.path
            or not self.path.startswith("wiki/pages/")
            or "/" in filename
            or not filename.endswith(".md")
            or filename in {".md", "..md"}
            or "\x00" in filename
        ):
            raise ValueError("PageChange paths must be Markdown files below wiki/pages/")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("PageChange source_ids must not contain duplicates")
        declared_sources = set(self.source_ids)
        cited_sources = {citation.source_id for citation in self.citations}
        if cited_sources != declared_sources:
            raise ValueError("PageChange citations must cover exactly all declared source_ids")
        validate_llm_title(self.title)
        validate_llm_summary(self.summary)
        validate_llm_body(self.body)
        return self


class PlannedPage(BaseModel):
    """One small, reviewable page target proposed before page generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    action: Literal["create", "update"]
    source_ids: tuple[SourceId, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)
    related_pages: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_plan_path(self) -> PlannedPage:
        if (
            not self.path.startswith("wiki/pages/")
            or not self.path.endswith(".md")
            or "\\" in self.path
            or any(part in {"", ".", ".."} for part in self.path.split("/"))
        ):
            raise ValueError("planned page paths must be Markdown files below wiki/pages/")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("planned page source_ids must not contain duplicates")
        return self


class CompilationPlan(BaseModel):
    """Structured routing notes kept with a proposed compiler ChangeSet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pages: tuple[PlannedPage, ...] = Field(min_length=1)
    conflicts: tuple[str, ...] = ()


def validate_llm_title(title: str) -> None:
    """Keep model titles from injecting Markdown structure into local page templates."""
    if not title.strip() or len(title.splitlines()) != 1:
        raise ValueError("PageChange title must be one non-empty line")
    if re.search(r"(?:^|\s)(?:#{1,6}\s|---(?:\s|$)|```|\[\^)", title):
        raise ValueError("PageChange title must not contain Markdown structure")


def validate_llm_summary(summary: str) -> None:
    """Keep INDEX rows to one locally-rendered line."""
    if not summary.strip() or len(summary.splitlines()) != 1:
        raise ValueError("PageChange summary must be one non-empty line")


def validate_llm_body(body: str) -> None:
    """Keep reserved, locally-rendered evidence sections out of model output."""
    if body.lstrip().startswith("---"):
        raise ValueError("PageChange body must not contain frontmatter")
    if re.search(
        r"^ {0,3}#{1,6}[ \t]+(?:Verified[ \t]+facts|Sources)(?:[ \t]+#+)?[ \t]*$",
        body,
        re.MULTILINE | re.IGNORECASE,
    ):
        raise ValueError("PageChange body must not contain reserved Wiki sections")
    if "[^" in body:
        raise ValueError("PageChange body must not contain footnote markers")


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_uri: str = Field(pattern=r"^mf://blob/[a-f0-9]{64}$")
    quote: str = Field(min_length=1)
    quote_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    locator: str = Field(pattern=r"^chars:\d+-\d+$")

    @model_validator(mode="after")
    def validate_source_version(self) -> Citation:
        if self.snapshot_uri != f"mf://blob/{self.content_sha256}":
            raise ValueError("snapshot_uri must identify the cited content_sha256")
        if hashlib.sha256(self.quote.encode()).hexdigest() != self.quote_sha256:
            raise ValueError("quote_sha256 must identify the exact quoted text")
        match = _CHAR_LOCATOR.fullmatch(self.locator)
        if match is None or int(match.group("end")) <= int(match.group("start")):
            raise ValueError("locator must contain a non-empty character range")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(pattern=r"^clm_[a-zA-Z0-9_-]+$")
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)
    status: ClaimStatus
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes_claim_id: str | None = Field(
        default=None,
        pattern=r"^clm_[a-zA-Z0-9_-]+$",
    )
    citations: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_and_validity(self) -> Claim:
        if self.status is ClaimStatus.VERIFIED and not self.citations:
            raise ValueError("VERIFIED claims require at least one citation")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        return self


class ChangeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ChangeOperationType
    path: str = Field(pattern=r"^wiki/")
    details: dict[str, Any] = Field(default_factory=dict)
    origin: ChangeOrigin | None = None

    @model_validator(mode="after")
    def validate_path(self) -> ChangeOperation:
        _validate_wiki_path(self.path)
        return self


class ChangeSetValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_coverage: float = Field(ge=0.0, le=1.0)
    unresolved_conflicts: int = Field(ge=0)
    schema_errors: int = Field(ge=0)


class OperationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(pattern=r"^wiki/")
    origin: ChangeOrigin | None = None
    operation_type: ChangeOperationType
    risk: RiskLevel
    reason_codes: tuple[str, ...] = ()
    changed_lines: int = Field(ge=0)
    source_count: int = Field(ge=0)
    touches_verified_facts: bool = False
    touches_user_protected_content: bool = False


class ValidationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=1)
    status: Literal["passed", "failed", "not_applicable"]
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    checks: tuple[ValidationCheck, ...] = ()
    candidate_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_versions_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    generated_at: datetime


class AutomationDecisionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    changeset_id: str = Field(pattern=r"^chg_[a-zA-Z0-9_-]+$")
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_id: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: AutomationDecision
    risk: RiskLevel
    reason_codes: tuple[str, ...] = ()
    decided_at: datetime


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ChangeSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    changeset_id: str = Field(pattern=r"^chg_[a-zA-Z0-9_-]+$")
    base_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    source_ids: tuple[SourceId, ...] = ()
    source_versions: dict[SourceId, int] = Field(default_factory=dict)
    status: ChangeSetStatus
    operations: tuple[ChangeOperation, ...] = ()
    claims: tuple[Claim, ...] = ()
    validation: ChangeSetValidation | None = None
    model: ModelUsage | None = None

    @model_validator(mode="after")
    def validate_evidence_references(self) -> ChangeSet:
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("ChangeSet source_ids must not contain duplicates")
        if self.source_versions and set(self.source_versions) != set(self.source_ids):
            raise ValueError("ChangeSet source_versions must match source_ids")
        if any(version_id < 1 for version_id in self.source_versions.values()):
            raise ValueError("ChangeSet source_versions must contain positive version IDs")
        operation_paths = [operation.path for operation in self.operations]
        if len(operation_paths) != len(set(operation_paths)):
            raise ValueError("ChangeSet operations must not conflict on the same Wiki path")
        declared = set(self.source_ids)
        cited = {
            citation.source_id
            for claim in self.claims
            if claim.status is ClaimStatus.VERIFIED
            for citation in claim.citations
        }
        if not cited <= declared:
            raise ValueError("VERIFIED claim citations must be declared in source_ids")
        return self

    def can_transition_to(self, target: ChangeSetStatus) -> bool:
        allowed = {
            ChangeSetStatus.PROPOSED: {
                ChangeSetStatus.VALIDATED,
                ChangeSetStatus.REJECTED,
            },
            ChangeSetStatus.VALIDATED: {
                ChangeSetStatus.APPROVED,
                ChangeSetStatus.REJECTED,
            },
            ChangeSetStatus.APPROVED: {
                ChangeSetStatus.APPLIED,
                ChangeSetStatus.REJECTED,
            },
            ChangeSetStatus.APPLIED: set(),
            ChangeSetStatus.REJECTED: set(),
        }
        return target in allowed[self.status]


class StagedWikiFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(pattern=r"^wiki/.+\.md$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_path(self) -> StagedWikiFile:
        _validate_wiki_path(self.path)
        return self


class StagedChangeSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    storage_version: int = Field(default=1, ge=1)
    staged_at: datetime
    changeset: ChangeSet
    proposed_files: tuple[StagedWikiFile, ...] = ()

    @model_validator(mode="after")
    def validate_proposed_files(self) -> StagedChangeSet:
        paths = [proposed.path for proposed in self.proposed_files]
        if len(paths) != len(set(paths)):
            raise ValueError("A staged ChangeSet cannot contain duplicate candidate paths")
        editable_paths = {
            operation.path
            for operation in self.changeset.operations
            if operation.type
            in {
                ChangeOperationType.CREATE_PAGE,
                ChangeOperationType.UPDATE_PAGE,
            }
        }
        unknown_paths = set(paths) - editable_paths
        if unknown_paths:
            listed = ", ".join(sorted(unknown_paths))
            raise ValueError(f"Candidate files lack a create/update operation: {listed}")
        missing_paths = editable_paths - set(paths)
        if missing_paths:
            listed = ", ".join(sorted(missing_paths))
            raise ValueError(f"Create/update operations lack a candidate file: {listed}")
        return self


class ReviewReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    changeset_id: str = Field(pattern=r"^chg_[a-zA-Z0-9_-]+$")
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["VALIDATED"] = "VALIDATED"
    review_mode: Literal["displayed", "inline_legacy"] = "displayed"
    reviewed_at: datetime


class ApprovalReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    changeset_id: str = Field(pattern=r"^chg_[a-zA-Z0-9_-]+$")
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["APPROVED"] = "APPROVED"
    approved_at: datetime


class LintIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str = Field(pattern=r"^lint_[a-zA-Z0-9_-]+$")
    severity: LintSeverity
    kind: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    evidence: tuple[str, ...] = ()
    suggested_action: str = Field(min_length=1)
