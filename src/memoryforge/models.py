"""Stable data contracts shared across storage and future lifecycle modules."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceCategory(str, Enum):
    """MVP source categories defined by the workspace schema."""

    DESIGN = "design"
    POSTMORTEM = "postmortem"
    SUMMARY = "summary"
    NOTES = "notes"
    REFS = "refs"


class Sensitivity(str, Enum):
    """Controls whether a source can be sent to a remote provider."""

    PUBLIC = "public"
    LOCAL_ONLY = "local_only"


class ClaimStatus(str, Enum):
    """Lifecycle state of a knowledge claim."""

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class ChangeSetStatus(str, Enum):
    """The only legal stages before a stable Wiki write."""

    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class ChangeOperationType(str, Enum):
    """Page-level operations available to a ChangeSet."""

    CREATE_PAGE = "CREATE_PAGE"
    UPDATE_PAGE = "UPDATE_PAGE"
    ARCHIVE_PAGE = "ARCHIVE_PAGE"
    UPDATE_CLAIM = "UPDATE_CLAIM"


class LintSeverity(str, Enum):
    """Severity assigned to a knowledge-health finding."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def _validate_wiki_path(path: str) -> None:
    """Reject path traversal before a ChangeSet reaches filesystem code."""

    parts = path.split("/")
    if (
        "\\" in path
        or len(parts) < 2
        or parts[0] != "wiki"
        or any(part in {"", ".", ".."} for part in parts)
        or not path.endswith(".md")
    ):
        raise ValueError("Wiki paths must be Markdown files below wiki/ without traversal segments")


class Citation(BaseModel):
    """A byte-stable reference to evidence in an immutable Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^src_[a-f0-9]{16}$")
    quote_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    locator: str = Field(min_length=1)


class SourceDocument(BaseModel):
    """Manifest record for a source copied into a workspace's Raw layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^src_[a-f0-9]{16}$")
    uri: str = Field(pattern=r"^raw/")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str
    category: SourceCategory
    imported_at: datetime
    observed_at: Optional[datetime] = None
    supersedes_source_id: Optional[str] = Field(
        default=None,
        pattern=r"^src_[a-f0-9]{16}$",
    )
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    tags: tuple[str, ...] = ()


class Claim(BaseModel):
    """A source-backed statement stored by the future Wiki compiler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(pattern=r"^clm_[a-zA-Z0-9_-]+$")
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)
    status: ClaimStatus
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    supersedes_claim_id: Optional[str] = Field(
        default=None,
        pattern=r"^clm_[a-zA-Z0-9_-]+$",
    )
    citations: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_and_validity(self) -> Claim:
        """Reject source-free verified claims and inverted validity windows."""

        if self.status is ClaimStatus.VERIFIED and not self.citations:
            raise ValueError("VERIFIED claims require at least one citation")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        return self


class ChangeOperation(BaseModel):
    """One proposed, reviewable modification to stable Wiki content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ChangeOperationType
    path: str = Field(pattern=r"^wiki/")
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_path(self) -> ChangeOperation:
        """Limit operations to safe Markdown paths in the stable Wiki tree."""

        _validate_wiki_path(self.path)
        return self


class ChangeSetValidation(BaseModel):
    """Deterministic validation results saved with a ChangeSet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_coverage: float = Field(ge=0.0, le=1.0)
    unresolved_conflicts: int = Field(ge=0)
    schema_errors: int = Field(ge=0)


class ModelUsage(BaseModel):
    """Provider metadata retained for cost and reproducibility auditing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ChangeSet(BaseModel):
    """Staged proposed modifications, never a direct Wiki write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    changeset_id: str = Field(pattern=r"^chg_[a-zA-Z0-9_-]+$")
    base_commit: str
    source_ids: tuple[str, ...] = ()
    status: ChangeSetStatus
    operations: tuple[ChangeOperation, ...] = ()
    validation: Optional[ChangeSetValidation] = None
    model: Optional[ModelUsage] = None

    def can_transition_to(self, target: ChangeSetStatus) -> bool:
        """Return whether a status change obeys the review gate."""

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
    """A proposed UTF-8 Wiki file stored beside a staged ChangeSet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(pattern=r"^wiki/.+\.md$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_path(self) -> StagedWikiFile:
        """Reject traversal and non-Wiki candidate paths before persistence."""

        _validate_wiki_path(self.path)
        return self


class StagedChangeSet(BaseModel):
    """The on-disk staging record that joins a ChangeSet to its candidate files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    storage_version: int = Field(default=1, ge=1)
    staged_at: datetime
    changeset: ChangeSet
    proposed_files: tuple[StagedWikiFile, ...] = ()

    @model_validator(mode="after")
    def validate_proposed_files(self) -> StagedChangeSet:
        """Require every candidate file to map to a proposed page operation."""

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


class ChangeSetLifecycle(BaseModel):
    """Mutable review state stored separately from an immutable proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    changeset_id: str = Field(pattern=r"^chg_[a-zA-Z0-9_-]+$")
    status: ChangeSetStatus
    updated_at: datetime
    reviewed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    applied_commit: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{40}$")


class LintIssue(BaseModel):
    """A deterministic issue discovered by the future knowledge linter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str = Field(pattern=r"^lint_[a-zA-Z0-9_-]+$")
    severity: LintSeverity
    kind: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    evidence: tuple[str, ...] = ()
    suggested_action: str = Field(min_length=1)
