"""DeepSeek Harness connection tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.interface.cli import app
from memoryforge.interface.harness_connect import (
    HARNESS_PATCH_BEGIN,
    HARNESS_PATCH_END,
    HarnessConnectConflictError,
    connect_harness_router,
)


def _router_args(workspace: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "memoryforge",
        "mcp",
        "--workspace",
        str(workspace.resolve(strict=False)),
        "--router",
    ]


def test_connect_harness_installs_router_and_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "wiki"
    workspace.mkdir()
    dsh_home = tmp_path / ".dsh"
    patch_file = dsh_home / "profiles" / "web" / "cordis.patch.yml"
    patch_file.parent.mkdir(parents=True)
    patch_file.write_text(
        "# user patch\n- insert:\n    - id: custom\n      name: example-plugin\n",
        encoding="utf-8",
    )

    result = connect_harness_router(workspace, dsh_home=dsh_home)

    assert result["status"] == "connected"
    assert result["server"] == "memoryforge"
    assert result["command"] == _router_args(workspace)
    patch = patch_file.read_text(encoding="utf-8")
    assert "id: custom" in patch
    assert patch.count(HARNESS_PATCH_BEGIN) == 1
    assert patch.count(HARNESS_PATCH_END) == 1
    assert "id: memoryforge-mcp" in patch
    assert "name: '@deepseek-ai/dsh-mcp-client'" in patch
    assert "serverName: memoryforge" in patch
    assert str(workspace.resolve()) in patch
    assert f'PYTHONPATH: "{(Path(__file__).parents[1] / "src").resolve()}"' in patch
    skill = (dsh_home / "skills" / "memoryforge-knowledge" / "SKILL.md").read_text(encoding="utf-8")
    assert "mcp__memoryforge__memoryforge_context" in skill
    assert "mcp__memoryforge__memoryforge_episodes" in skill
    assert "mcp__memoryforge__memoryforge_load_session" in skill
    assert "no_session_evidence" in skill


def test_connect_harness_is_idempotent_and_updates_managed_command(tmp_path: Path) -> None:
    workspace_a = tmp_path / "wiki-a"
    workspace_b = tmp_path / "wiki-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    dsh_home = tmp_path / ".dsh"

    first = connect_harness_router(workspace_a, dsh_home=dsh_home)
    second = connect_harness_router(workspace_a, dsh_home=dsh_home)
    third = connect_harness_router(workspace_b, dsh_home=dsh_home)

    assert first["status"] == "connected"
    assert second["status"] == "unchanged"
    assert third["status"] == "connected"
    patch = Path(str(third["patch_file"])).read_text(encoding="utf-8")
    assert patch.count(HARNESS_PATCH_BEGIN) == 1
    assert str(workspace_a.resolve()) not in patch
    assert str(workspace_b.resolve()) in patch


def test_connect_harness_allow_local_is_fixed_in_command(tmp_path: Path) -> None:
    workspace = tmp_path / "wiki"
    workspace.mkdir()
    result = connect_harness_router(
        workspace,
        allow_local=True,
        dsh_home=tmp_path / ".dsh",
    )

    assert result["command"] == [*_router_args(workspace), "--allow-local-llm"]
    patch = Path(str(result["patch_file"])).read_text(encoding="utf-8")
    assert '          - "--allow-local-llm"' in patch


def test_connect_harness_rejects_unmanaged_conflicts(tmp_path: Path) -> None:
    dsh_home = tmp_path / ".dsh"
    patch_file = dsh_home / "profiles" / "web" / "cordis.patch.yml"
    patch_file.parent.mkdir(parents=True)
    patch_file.write_text(
        "- insert:\n    - id: memoryforge-mcp\n      name: something-else\n",
        encoding="utf-8",
    )

    with pytest.raises(HarnessConnectConflictError, match="unmanaged"):
        connect_harness_router(tmp_path / "wiki", dsh_home=dsh_home)

    skill_file = dsh_home / "skills" / "memoryforge-knowledge" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("user owned\n", encoding="utf-8")
    patch_file.write_text("[]\n", encoding="utf-8")
    with pytest.raises(HarnessConnectConflictError, match="unmanaged"):
        connect_harness_router(tmp_path / "wiki", dsh_home=dsh_home)
    assert patch_file.read_text(encoding="utf-8") == "[]\n"


def test_connect_cli_supports_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "wiki"
    workspace.mkdir()
    captured: dict[str, object] = {}

    def fake_connect(
        value: Path,
        *,
        allow_local: bool = False,
    ) -> dict[str, object]:
        captured.update(workspace=value, allow_local=allow_local)
        return {"status": "connected", "server": "memoryforge"}

    monkeypatch.setattr("memoryforge.interface.cli.connect_harness_router", fake_connect)
    result = CliRunner().invoke(
        app,
        ["connect", "harness", "--allow-local-llm", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0
    assert captured == {"workspace": workspace, "allow_local": True}
    assert '"status": "connected"' in result.stdout


def test_connect_cli_rejects_harness_project_and_startup_hook(tmp_path: Path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()

    project_result = runner.invoke(app, ["connect", "harness", str(project)])
    hook_result = runner.invoke(app, ["connect", "harness", "--startup-hook"])

    assert project_result.exit_code == 1
    assert "global Router" in project_result.stderr
    assert hook_result.exit_code == 1
    assert "does not support --startup-hook" in hook_result.stderr
