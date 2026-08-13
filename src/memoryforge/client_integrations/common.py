from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ManagedFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    content_hash: str = Field(min_length=1)
    managed: bool
    mode: int | None = None


class IntegrationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent: Literal["codex", "claude", "cursor", "gemini"]
    scope: Literal["project", "user"]
    server_name: str = Field(min_length=1)
    commands: tuple[tuple[str, ...], ...] = ()
    file_changes: tuple[ManagedFileChange, ...] = ()
    hook_events: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class IntegrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["installed", "unchanged", "conflict", "unsupported"]
    server_name: str = Field(min_length=1)
    managed_files: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()


def host_id(agent: str, workspace: Path, project: Path) -> str:
    workspace_str = str(workspace.resolve())
    project_str = str(project.resolve())
    composite = f"{workspace_str}\0{project_str}"
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:8]
    return f"{agent}:{digest}"


def mcp_command(
    python: Path,
    workspace: Path,
    project: Path,
    host_id: str,
    profile: str,
) -> list[str]:
    python_abs = str(python.resolve())
    workspace_abs = str(workspace.resolve())
    project_abs = str(project.resolve())
    return [
        python_abs,
        "-m",
        "memoryforge",
        "mcp",
        "--workspace",
        workspace_abs,
        "--project",
        project_abs,
        "--host-id",
        host_id,
        "--profile",
        profile,
    ]


def server_name(agent: str) -> str:
    return f"memoryforge-{agent}"
