from __future__ import annotations

from pathlib import Path

from memoryforge.client_integrations import codex
from memoryforge.client_integrations.common import IntegrationPlan, IntegrationResult


def test_plan_install_dry_run_commands_correct(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = codex.plan_install(workspace, project)
    assert isinstance(plan, IntegrationPlan)
    assert plan.agent == "codex"
    assert plan.scope == "project"
    assert plan.server_name == "memoryforge-codex"
    assert len(plan.commands) == 1
    cmd = plan.commands[0]
    assert cmd[0] == "codex"
    assert cmd[1] == "mcp"
    assert cmd[2] == "add"
    assert cmd[3] == "memoryforge-codex"
    assert cmd[4] == "--"
    assert cmd[5:].count("memoryforge") >= 1 or any("memoryforge" in part for part in cmd[5:])


def test_plan_install_with_capture_adds_hooks_and_warnings(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = codex.plan_install(workspace, project, capture=True)
    assert "SessionStart" in plan.hook_events
    assert "UserPromptSubmit" in plan.hook_events
    assert "PostToolUse" in plan.hook_events
    assert "PreCompact" in plan.hook_events
    assert "SessionEnd" in plan.hook_events
    assert "capture_opt_in" in plan.warnings
    assert "hook_trust_review_required" in plan.warnings


def test_plan_install_without_capture_no_hooks(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = codex.plan_install(workspace, project, capture=False)
    assert plan.hook_events == ()
    assert plan.warnings == ()


def test_verify_install_unsupported_dry_run(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    result = codex.verify_install(workspace, project)
    assert isinstance(result, IntegrationResult)
    assert result.status == "unsupported"
    assert result.server_name == "memoryforge-codex"
    assert len(result.next_steps) >= 1


def test_plan_uninstall_command(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = codex.plan_uninstall(workspace, project)
    assert isinstance(plan, IntegrationPlan)
    assert len(plan.commands) == 1
    cmd = plan.commands[0]
    assert cmd[0] == "codex"
    assert cmd[1] == "mcp"
    assert cmd[2] == "remove"
    assert cmd[3] == "memoryforge-codex"
