"""Tests for the one-time global Codex MCP registration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from memoryforge.interface.cli import app
from memoryforge.interface.codex_connect import connect_codex_router, router_mcp_command


def test_connect_codex_router_registers_one_global_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        if argv[2] == "get":
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        assert argv[2] == "add"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("memoryforge.codex_connect.shutil.which", lambda _name: "/fake/codex")
    monkeypatch.setattr("memoryforge.codex_connect.subprocess.run", fake_run)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    result = connect_codex_router(workspace)

    command = router_mcp_command(workspace)
    assert result["status"] == "connected"
    assert result["server"] == "memoryforge"
    assert result["routing"] == "mcp_roots"
    assert result["session_hook"] == "disabled"
    assert calls == [
        ["/fake/codex", "mcp", "get", "memoryforge", "--json"],
        ["/fake/codex", "mcp", "add", "memoryforge", "--", *command],
    ]
    assert "--router" in command
    assert "--project-root" not in command
    skill_file = Path(str(result["skill_file"]))
    assert skill_file.is_file()
    assert "memoryforge_context" in skill_file.read_text(encoding="utf-8")
    assert "no_local_evidence" in skill_file.read_text(encoding="utf-8")
    assert "inspect the current checkout first" in skill_file.read_text(encoding="utf-8")
    assert "jump hosts and bastions" in skill_file.read_text(encoding="utf-8")
    assert "project_answer" in skill_file.read_text(encoding="utf-8")
    assert "latest-session summaries" in skill_file.read_text(encoding="utf-8")
    assert "imported Feishu" in skill_file.read_text(encoding="utf-8")
    assert "before any company-wide or external knowledge search" in skill_file.read_text(
        encoding="utf-8"
    )
    assert "work machine, jump host, bastion and target host are distinct" in skill_file.read_text(
        encoding="utf-8"
    )


def test_connect_cli_without_project_uses_the_global_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: dict[str, object] = {}

    def fake_connect(
        path: Path, *, allow_local: bool = False, startup_hook: bool = False
    ) -> dict[str, object]:
        captured["workspace"] = path
        captured["allow_local"] = allow_local
        captured["startup_hook"] = startup_hook
        return {"status": "connected", "server": "memoryforge", "routing": "mcp_roots"}

    monkeypatch.setattr("memoryforge.cli.connect_codex_router", fake_connect)

    result = CliRunner().invoke(app, ["connect", "codex", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert captured == {"workspace": workspace, "allow_local": False, "startup_hook": False}
    assert '"routing": "mcp_roots"' in result.stdout


def test_router_installs_session_hook_only_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fake_run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        if argv[2] == "get":
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("memoryforge.codex_connect.shutil.which", lambda _name: "/fake/codex")
    monkeypatch.setattr("memoryforge.codex_connect.subprocess.run", fake_run)
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    result = connect_codex_router(workspace, startup_hook=True)

    assert result["session_hook"] == "enabled"
    assert (codex_home / "hooks.json").is_file()


def test_connect_cli_can_opt_in_to_startup_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: dict[str, object] = {}

    def fake_connect(
        path: Path, *, allow_local: bool = False, startup_hook: bool = False
    ) -> dict[str, object]:
        captured.update(path=path, allow_local=allow_local, startup_hook=startup_hook)
        return {"status": "connected", "server": "memoryforge"}

    monkeypatch.setattr("memoryforge.cli.connect_codex_router", fake_connect)

    result = CliRunner().invoke(
        app,
        ["connect", "codex", "--startup-hook", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert captured["startup_hook"] is True
