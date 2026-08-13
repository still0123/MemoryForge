from __future__ import annotations

import json
import sys
from pathlib import Path

from memoryforge.client_integrations.common import (
    IntegrationPlan,
    IntegrationResult,
    mcp_command,
    server_name,
)


def _cursor_mcp_json_example(workspace: Path, project: Path) -> str:
    agent = "cursor"
    python = Path(sys.executable)
    cmd = mcp_command(python, workspace, project, "micro")
    sname = server_name(agent)
    fragment = {
        "mcpServers": {
            sname: {
                "command": cmd[0],
                "args": cmd[1:],
                "readOnly": True,
            }
        }
    }
    return json.dumps(fragment, indent=2)


def plan_install(
    workspace: Path,
    project: Path,
    *,
    capture: bool = False,
) -> IntegrationPlan:
    agent = "cursor"
    sname = server_name(agent)

    warnings: tuple[str, ...] = (
        "cursor_mcp_manual",
    )
    if capture:
        warnings = warnings + ("capture_not_supported",)

    json_example = _cursor_mcp_json_example(workspace, project)
    next_steps_examples = (
        "Paste the following into Cursor MCP settings (User or Workspace):",
        json_example,
    )

    return IntegrationPlan(
        agent=agent,
        scope="user",
        server_name=sname,
        commands=(),
        hook_events=(),
        warnings=warnings,
    )


def verify_install(
    workspace: Path,
    project: Path,
) -> IntegrationResult:
    return IntegrationResult(
        status="unsupported",
        server_name=server_name("cursor"),
        next_steps=(
            "Cursor MCP requires manual JSON configuration.",
            "Use plan_install to obtain the JSON snippet and paste it into Cursor settings.",
        ),
    )


def plan_uninstall(
    workspace: Path,
    project: Path,
) -> IntegrationPlan:
    sname = server_name("cursor")
    return IntegrationPlan(
        agent="cursor",
        scope="user",
        server_name=sname,
        commands=(),
        warnings=(
            "cursor_mcp_manual",
            "Remove the memoryforge-cursor entry from Cursor MCP settings JSON.",
        ),
    )
