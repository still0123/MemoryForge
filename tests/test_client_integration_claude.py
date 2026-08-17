from __future__ import annotations

from pathlib import Path

from memoryforge.client_integrations import claude
from memoryforge.client_integrations.common import IntegrationPlan, IntegrationResult


def test_plan_install_claude_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = claude.plan_install(workspace, project)
    assert isinstance(plan, IntegrationPlan)
    assert plan.agent == "claude"
    assert plan.scope == "project"
    assert plan.server_name == "memoryforge-claude"
    assert len(plan.commands) == 1
    cmd = plan.commands[0]
    assert cmd[0] == "claude"
    assert cmd[1] == "mcp"
    assert cmd[2] == "add"
    assert cmd[3] == "memoryforge-claude"
    assert cmd[4] == "--"


def test_plan_install_claude_capture_hooks(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = claude.plan_install(workspace, project, capture=True)
    assert len(plan.hook_events) == 5
    assert "SessionStart" in plan.hook_events
    assert "SessionEnd" in plan.hook_events
    assert "capture_opt_in" in plan.warnings
    assert "claude_mcp_json_scope_project" in plan.warnings


def test_verify_install_claude_unsupported(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    result = claude.verify_install(workspace, project)
    assert isinstance(result, IntegrationResult)
    assert result.status == "unsupported"
    assert "mcp.json" in " ".join(result.next_steps).lower() or ".mcp.json" in " ".join(
        result.next_steps
    )


def test_plan_uninstall_claude(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = claude.plan_uninstall(workspace, project)
    cmd = plan.commands[0]
    assert cmd[0] == "claude"
    assert cmd[1] == "mcp"
    assert cmd[2] == "remove"
    assert cmd[3] == "memoryforge-claude"
