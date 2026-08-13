from __future__ import annotations

import sys
from pathlib import Path

import pytest

from memoryforge.client_integrations import get_adapter
from memoryforge.client_integrations.common import (
    IntegrationPlan,
    IntegrationResult,
    ManagedFileChange,
    host_id,
    mcp_command,
)


def test_host_id_stable_same_inputs(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    first = host_id("codex", workspace, project)
    second = host_id("codex", workspace, project)
    assert first == second
    assert first.startswith("codex:")
    suffix = first.split(":", 1)[1]
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def test_host_id_differs_by_agent(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()

    codex_id = host_id("codex", workspace, project)
    claude_id = host_id("claude", workspace, project)
    assert codex_id != claude_id
    assert codex_id.startswith("codex:")
    assert claude_id.startswith("claude:")


def test_host_id_differs_by_project(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    project_a = tmp_path / "prj-a"
    project_b = tmp_path / "prj-b"
    workspace.mkdir()
    project_a.mkdir()
    project_b.mkdir()

    id_a = host_id("codex", workspace, project_a)
    id_b = host_id("codex", workspace, project_b)
    assert id_a != id_b


def test_cursor_adapter_is_not_supported() -> None:
    with pytest.raises(ValueError, match="unknown client adapter: cursor"):
        get_adapter("cursor")


def test_mcp_command_contains_absolute_paths_and_profile(tmp_path: Path) -> None:
    python = Path(sys.executable)
    workspace = tmp_path / "ws"
    project = tmp_path / "prj"
    workspace.mkdir()
    project.mkdir()
    cmd = mcp_command(python, workspace, project, "micro")

    assert cmd[0] == str(python.resolve())
    assert "-m" in cmd
    assert "memoryforge" in cmd
    assert "mcp" in cmd
    assert "--workspace" in cmd
    assert "--project-root" in cmd
    assert "--project" not in cmd
    assert "--host-id" not in cmd
    assert "--profile" in cmd

    profile_idx = cmd.index("--profile") + 1
    assert cmd[profile_idx] == "micro"

    workspace_idx = cmd.index("--workspace") + 1
    assert cmd[workspace_idx] == str(workspace.resolve())
    assert Path(cmd[workspace_idx]).is_absolute()

    project_idx = cmd.index("--project-root") + 1
    assert cmd[project_idx] == str(project.resolve())
    assert Path(cmd[project_idx]).is_absolute()


def test_managed_file_change_frozen() -> None:
    change = ManagedFileChange(
        path=Path("/tmp/x"),
        content_hash="abc123",
        managed=True,
        mode=0o600,
    )
    assert change.path == Path("/tmp/x")
    assert change.managed is True


def test_integration_plan_frozen() -> None:
    plan = IntegrationPlan(
        agent="codex",
        scope="project",
        server_name="memoryforge-codex",
        commands=(("codex", "mcp", "add"),),
    )
    assert plan.agent == "codex"
    assert plan.scope == "project"


def test_integration_result_frozen() -> None:
    result = IntegrationResult(
        status="unsupported",
        server_name="memoryforge-codex",
        next_steps=("manual install",),
    )
    assert result.status == "unsupported"
