from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CaptureEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    repository_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    client: Literal["codex", "claude", "gemini"]
    session_id: str = Field(min_length=1, max_length=128)
    event_type: Literal[
        "session_start",
        "user_prompt",
        "file_changed",
        "test_result",
        "decision",
        "pre_compact",
        "session_end",
    ]
    observed_at: datetime
    text: str
    paths: tuple[str, ...] = ()
    sensitivity: Literal["local_only"] = "local_only"
    unverified: Literal[True] = True
    origin_sha256: str = Field(default="", pattern=r"^[a-f0-9]{0,64}$")
    redaction_count: int = Field(default=0, ge=0)
    truncated: bool = False


class InboxSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    client: Literal["codex", "claude", "gemini"]
    session_id: str = Field(min_length=1, max_length=128)
    state: Literal["open", "ready", "ignored", "proposed"]
    first_seen_at: datetime
    last_seen_at: datetime


class HandoffPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: str = Field(pattern=r"^[a-f0-9]{0,16}$")
    repository_id: str = Field(default="", pattern=r"^[a-f0-9]{0,64}$")
    before: datetime | None = None
    character_count: int = Field(default=0, ge=0)
    recent_task_line: str | None = None
    decisions: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    last_test_result: str | None = None
    unfinished_items: tuple[str, ...] = ()
    content: str
