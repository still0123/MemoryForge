from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from memoryforge.interface.cli import app
from memoryforge.interface.codex_connect import (
    _install_session_hook,
    _remove_session_hook,
    connect_claude_router,
    install_claude_skill,
    router_mcp_command,
)


def test_connect_claude_registers_user_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        if argv[1:3] == ["mcp", "get"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("memoryforge.codex_connect.shutil.which", lambda _name: "/fake/claude")
    monkeypatch.setattr("memoryforge.codex_connect.subprocess.run", fake_run)
    skill = tmp_path / "SKILL.md"
    monkeypatch.setattr("memoryforge.codex_connect.install_claude_skill", lambda: skill)
    monkeypatch.setattr(
        "memoryforge.codex_connect.configure_claude_session_hook",
        lambda _workspace, *, enabled: {"session_hook": "enabled" if enabled else "disabled"},
    )

    result = connect_claude_router(workspace, allow_local=True)

    command = router_mcp_command(workspace, allow_local=True)
    assert result["status"] == "connected"
    assert result["scope"] == "user"
    assert result["session_hook"] == "disabled"
    assert calls == [
        ["/fake/claude", "mcp", "get", "memoryforge"],
        [
            "/fake/claude",
            "mcp",
            "add",
            "--transport",
            "stdio",
            "--scope",
            "user",
            "memoryforge",
            "--",
            *command,
        ],
    ]


def test_claude_skill_requires_detailed_load_confirmation(tmp_path: Path) -> None:
    skill = install_claude_skill(tmp_path / ".claude")

    content = skill.read_text(encoding="utf-8")
    assert "memoryforge_load_session" in content
    assert "never reply only that it was loaded" in content


def test_claude_hook_is_opt_in_and_preserves_other_hooks(tmp_path: Path) -> None:
    workspace = tmp_path / "wiki"
    settings = tmp_path / ".claude" / "settings.json"
    _install_session_hook(workspace, host="claude", config_file=settings)
    payload = settings.read_text(encoding="utf-8")
    assert "memoryforge.storage.session_bootstrap" in payload
    assert "--host claude" in payload

    removed = _remove_session_hook(workspace, host="claude", config_file=settings)

    assert removed is True
    assert "memoryforge.storage.session_bootstrap" not in settings.read_text(encoding="utf-8")


def test_connect_cli_routes_claude_without_project(
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
        return {"status": "connected", "server": "memoryforge", "scope": "user"}

    monkeypatch.setattr("memoryforge.cli.connect_claude_router", fake_connect)

    result = CliRunner().invoke(
        app,
        [
            "connect",
            "claude",
            "--allow-local-llm",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"path": workspace, "allow_local": True, "startup_hook": False}
