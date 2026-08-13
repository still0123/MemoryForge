from __future__ import annotations

from pathlib import Path

from memoryforge.client_integrations import gemini
from memoryforge.client_integrations.common import IntegrationPlan, IntegrationResult


def test_plan_install_gemini_commands_and_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = gemini.plan_install(workspace, project)
    assert isinstance(plan, IntegrationPlan)
    assert plan.agent == "gemini"
    assert plan.scope == "user"
    assert plan.server_name == "memoryforge-gemini"
    assert len(plan.commands) == 1
    cmd = plan.commands[0]
    assert cmd[0] == "gemini"
    assert cmd[1] == "mcp"
    assert cmd[2] == "add"
    assert cmd[3] == "memoryforge-gemini"
    assert cmd[4] == "--"


def test_plan_install_gemini_capture_warnings(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = gemini.plan_install(workspace, project, capture=True)
    assert "gemini_scope_user_default" in plan.warnings
    assert "capture_opt_in" in plan.warnings
    assert len(plan.hook_events) == 5


def test_verify_install_gemini_unsupported(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    result = gemini.verify_install(workspace, project)
    assert isinstance(result, IntegrationResult)
    assert result.status == "unsupported"
    assert result.server_name == "memoryforge-gemini"


def test_plan_uninstall_gemini(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = gemini.plan_uninstall(workspace, project)
    assert plan.scope == "user"
    cmd = plan.commands[0]
    assert cmd[0] == "gemini"
    assert cmd[1] == "mcp"
    assert cmd[2] == "remove"
