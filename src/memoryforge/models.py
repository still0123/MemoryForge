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
    suffix: Literal[".md", ".markdown", ".txt"]
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

    @model_validator(mode="after")
    def validate_path(self) -> ChangeOperation:
        _validate_wiki_path(self.path)
        return self


class ChangeSetValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_coverage: float = Field(ge=0.0, le=1.0)
    unresolved_conflicts: int = Field(ge=0)
    schema_errors: int = Field(ge=0)


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
    status: ChangeSetStatus
    operations: tuple[ChangeOperation, ...] = ()
    claims: tuple[Claim, ...] = ()
    validation: ChangeSetValidation | None = None
    model: ModelUsage | None = None

    @model_validator(mode="after")
    def validate_evidence_references(self) -> ChangeSet:
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("ChangeSet source_ids must not contain duplicates")
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


class LintIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str = Field(pattern=r"^lint_[a-zA-Z0-9_-]+$")
    severity: LintSeverity
    kind: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    evidence: tuple[str, ...] = ()
    suggested_action: str = Field(min_length=1)
