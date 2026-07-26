from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LocalDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_uri: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    media_type: Literal["text/markdown", "text/plain"]
    category: str = Field(min_length=1)
    suffix: Literal[".md", ".markdown", ".txt"]
    title: str = Field(min_length=1)
    content: str


class ImportResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["created", "updated", "unchanged"]
    source_id: str
    title: str
    source_uri: str
    category: str
    content_sha256: str
    snapshot_uri: str
    snapshot_path: str
    observed_at: datetime


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    title: str
    source_uri: str
    source_path: str
    snapshot_uri: str
    snapshot_path: str
    category: str
    snippet: str
    content_sha256: str
    observed_at: datetime
