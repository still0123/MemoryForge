from __future__ import annotations

import sys
from pathlib import Path

from memoryforge.client_integrations.common import (
    IntegrationPlan,
    IntegrationResult,
    host_id,
    mcp_command,
    server_name,
)


def plan_install(
    workspace: Path,
    project: Path,
    *,
    capture: bool = False,
) -> IntegrationPlan:
    agent = "claude"
    python = Path(sys.executable)
    hid = host_id(agent, workspace, project)
    cmd = mcp_command(python, workspace, project, hid, "micro")
    sname = server_name(agent)

    commands: tuple[tuple[str, ...], ...] = (
        ("claude", "mcp", "add", sname, "--", *cmd),
    )

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
            "claude_mcp_json_scope_project",
        )

    return IntegrationPlan(
        agent=agent,
        scope="project",
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
        server_name=server_name("claude"),
        next_steps=(
            "Dry-run mode only. Execute plan commands via claude CLI to install.",
            "Alternatively write project .mcp.json with the server entry.",
        ),
    )


def plan_uninstall(
    workspace: Path,
    project: Path,
) -> IntegrationPlan:
    sname = server_name("claude")
    return IntegrationPlan(
        agent="claude",
        scope="project",
        server_name=sname,
        commands=(("claude", "mcp", "remove", sname),),
    )
