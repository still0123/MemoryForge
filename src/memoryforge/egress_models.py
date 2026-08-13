from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from memoryforge.models import SourceId


class EgressClass(StrEnum):
    PUBLIC = "public"
    HOST_ALLOWED = "host_allowed"
    NEVER_MODEL = "never_model"


class SourceEgressRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    egress_class: EgressClass
    allowed_hosts: tuple[str, ...] = ()
    updated_at: datetime
    actor: str = Field(min_length=1)


class EgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    host_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    purpose: Literal["context", "evidence", "recall", "handoff", "proposal"]
    max_characters: int = Field(ge=0)


class EgressDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason_code: str = Field(min_length=1)
    egress_class: EgressClass


class DisclosureReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    host_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    purpose: Literal["context", "evidence", "recall", "handoff", "proposal"]
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_refs: tuple[tuple[str, int], ...] = ()
    character_count: int = Field(ge=0)
    redaction_count: int = Field(ge=0)
    disclosed_at: datetime
