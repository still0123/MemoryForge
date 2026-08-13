"""Tests for the one-time global Codex MCP registration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.codex_connect import connect_codex_router, router_mcp_command


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

    result = connect_codex_router(workspace)

    command = router_mcp_command(workspace)
    assert result["status"] == "connected"
    assert result["server"] == "memoryforge"
    assert result["routing"] == "mcp_roots"
    assert calls == [
        ["/fake/codex", "mcp", "get", "memoryforge", "--json"],
        ["/fake/codex", "mcp", "add", "memoryforge", "--", *command],
    ]
    assert "--router" in command
    assert "--project-root" not in command


def test_connect_cli_without_project_uses_the_global_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: dict[str, object] = {}

    def fake_connect(path: Path, *, allow_local: bool = False) -> dict[str, object]:
        captured["workspace"] = path
        captured["allow_local"] = allow_local
        return {"status": "connected", "server": "memoryforge", "routing": "mcp_roots"}

    monkeypatch.setattr("memoryforge.cli.connect_codex_router", fake_connect)

    result = CliRunner().invoke(app, ["connect", "codex", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert captured == {"workspace": workspace, "allow_local": False}
    assert '"routing": "mcp_roots"' in result.stdout
