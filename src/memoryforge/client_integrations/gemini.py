from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from memoryforge.client_integrations.common import (
    IntegrationPlan,
    IntegrationResult,
    mcp_command,
    server_name,
)


def plan_install(
    workspace: Path,
    project: Path,
    *,
    capture: bool = False,
) -> IntegrationPlan:
    agent: Literal["gemini"] = "gemini"
    python = Path(sys.executable)
    cmd = mcp_command(python, workspace, project, "micro")
    sname = server_name(agent)

    commands: tuple[tuple[str, ...], ...] = (("gemini", "mcp", "add", sname, "--", *cmd),)

    hook_events: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    if capture:
        hook_events = (
            "SessionStart",
            "UserPromptSubmit",
            "PostToolUse",
            "PreCompact",
            "SessionEnd",
        )
        warnings = (
            "capture_opt_in",
            "hook_trust_review_required",
            "gemini_scope_user_default",
        )

    return IntegrationPlan(
        agent=agent,
        scope="user",
        server_name=sname,
        commands=commands,
        hook_events=hook_events,
        warnings=warnings,
    )


def verify_install(
    workspace: Path,
    project: Path,
) -> IntegrationResult:
    return IntegrationResult(
        status="unsupported",
        server_name=server_name("gemini"),
        next_steps=("Dry-run mode only. Execute plan commands via gemini CLI to install.",),
    )


def plan_uninstall(
    workspace: Path,
    project: Path,
) -> IntegrationPlan:
    sname = server_name("gemini")
    return IntegrationPlan(
        agent="gemini",
        scope="user",
        server_name=sname,
        commands=(("gemini", "mcp", "remove", sname),),
    )
