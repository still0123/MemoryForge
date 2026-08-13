from __future__ import annotations

from pathlib import Path

from memoryforge.client_integrations import cursor
from memoryforge.client_integrations.common import IntegrationPlan, IntegrationResult


def test_cursor_plan_no_commands_and_no_capture(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = cursor.plan_install(workspace, project, capture=False)
    assert isinstance(plan, IntegrationPlan)
    assert plan.agent == "cursor"
    assert plan.scope == "user"
    assert plan.commands == ()
    assert plan.hook_events == ()
    assert "cursor_mcp_manual" in plan.warnings


def test_cursor_plan_explicit_no_capture_support_warning(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = cursor.plan_install(workspace, project, capture=True)
    assert "capture_not_supported" in plan.warnings
    assert plan.hook_events == ()


def test_cursor_verify_unsupported(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    result = cursor.verify_install(workspace, project)
    assert isinstance(result, IntegrationResult)
    assert result.status == "unsupported"
    combined = " ".join(result.next_steps).lower()
    assert "manual" in combined or "json" in combined or "settings" in combined


def test_cursor_uninstall_no_subprocess(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    plan = cursor.plan_uninstall(workspace, project)
    assert plan.commands == ()
    assert any("cursor" in w.lower() or "remove" in w.lower() or "mcp" in w.lower() for w in plan.warnings)
