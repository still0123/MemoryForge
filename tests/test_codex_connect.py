"""Phase 3 tests: one-shot Codex connection and managed AGENTS.md blocks.

The sandbox has no Codex CLI, so a stub subprocess stands in for ``codex
mcp get/add/list`` and records every call. The tests pin the stable server
name, the exact registered argv, idempotent re-runs, coexistence of two
projects and two workspaces, fail-closed conflicts, the 3,000-character
managed block budget, and the legacy ``codex-setup`` compatibility rules
(one shared installer, never two blocks at once).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from memoryforge.query.agent_access import server_name
from memoryforge.interface.cli import app
from memoryforge.interface.codex_connect import (
    AGENTS_MCP_BEGIN,
    AGENTS_MCP_END,
    AGENTS_RECALL_BEGIN,
    AGENTS_RECALL_END,
    CodexConnectConflictError,
    _parse_server_json,
    connect_codex,
    install_agents_block,
)
from memoryforge.core.errors import UnmappedProjectError
from tests.test_agent_access import CACHE_POLICY, _bound_workspace

_AGENTS_BLOCK_LIMIT = 3000
_FAKE_CODEX = "/usr/local/bin/codex"
_REAL_RUN = subprocess.run


def test_parse_server_json_accepts_current_codex_transport_shape() -> None:
    payload = json.dumps(
        {
            "name": "memoryforge",
            "transport": {
                "type": "stdio",
                "command": "/usr/bin/python3",
                "args": ["-m", "memoryforge", "mcp"],
            },
        }
    )

    assert _parse_server_json(payload) == [
        "/usr/bin/python3",
        "-m",
        "memoryforge",
        "mcp",
    ]


class _FakeCodex:
    """A stub of the Codex CLI's MCP subcommands, recording every invocation.

    ``subprocess.run`` is patched at the module attribute on the shared
    ``subprocess`` module, so anything that is not the Codex CLI is delegated
    to the real implementation.
    """

    def __init__(self) -> None:
        self.servers: dict[str, list[str]] = {}
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
        if argv[0] != _FAKE_CODEX:
            return _REAL_RUN(argv, **kwargs)
        self.calls.append(argv)
        assert argv[1] == "mcp"
        subcommand = argv[2]
        if subcommand == "get":
            name = argv[3]
            if name not in self.servers:
                return SimpleNamespace(returncode=1, stdout="", stderr=f"unknown server: {name}")
            command = self.servers[name]
            payload = {
                "name": name,
                "type": "stdio",
                "command": command[0],
                "args": command[1:],
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if subcommand == "add":
            name = argv[3]
            separator = argv.index("--")
            self.servers[name] = argv[separator + 1 :]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if subcommand == "list":
            entries = [
                {"name": name, "command": command[0], "args": command[1:]}
                for name, command in self.servers.items()
            ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(entries), stderr="")
        raise AssertionError(f"unexpected argv: {argv}")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "memoryforge.codex_connect.shutil.which",
            lambda name: _FAKE_CODEX if name == "codex" else None,
        )
        monkeypatch.setattr("memoryforge.codex_connect.subprocess.run", self.run)


def _connect_args(workspace: Path, checkout: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "memoryforge",
        "mcp",
        "--workspace",
        str(workspace.resolve(strict=False)),
        "--project-root",
        str(checkout.resolve(strict=False)),
    ]


def test_connect_registers_stable_server_and_fixed_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    name = server_name(workspace, checkout)

    result = connect_codex(workspace, checkout)

    assert result["status"] == "connected"
    assert result["server"] == name
    assert result["project"] == checkout.name
    assert fake.servers[name] == _connect_args(workspace, checkout)
    assert [call[1:] for call in fake.calls] == [
        ["mcp", "get", name, "--json"],
        ["mcp", "add", name, "--", *_connect_args(workspace, checkout)],
    ]
    agents = (checkout / "AGENTS.md").read_text(encoding="utf-8")
    assert AGENTS_MCP_BEGIN in agents and AGENTS_MCP_END in agents
    assert name in agents
    assert checkout.name in agents
    # The block binds the project but never leaks workspace absolute paths.
    assert str(workspace) not in agents
    assert AGENTS_RECALL_BEGIN not in agents
    start = agents.index(AGENTS_MCP_BEGIN)
    stop = agents.index(AGENTS_MCP_END) + len(AGENTS_MCP_END)
    assert len(agents[start:stop]) <= _AGENTS_BLOCK_LIMIT


def test_connect_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)

    first = connect_codex(workspace, checkout)
    second = connect_codex(workspace, checkout)

    assert first["status"] == "connected"
    assert second["status"] == "unchanged"
    assert second["server"] == first["server"]
    assert [call[1:] for call in fake.calls if call[1:3] == ["mcp", "add"]] == [
        ["mcp", "add", first["server"], "--", *_connect_args(workspace, checkout)]
    ]
    agents = (checkout / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count(AGENTS_MCP_BEGIN) == 1
    assert agents.count(AGENTS_MCP_END) == 1


def test_connect_without_codex_cli_returns_copyable_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    monkeypatch.setattr("memoryforge.codex_connect.shutil.which", lambda name: None)

    result = connect_codex(workspace, checkout)

    assert result["status"] == "codex_not_found"
    assert result["server"] == server_name(workspace, checkout)
    install_command = str(result["install_command"])
    assert install_command.startswith("codex mcp add ")
    assert "memoryforge mcp" in install_command
    assert "--allow-local-llm" not in install_command
    # No user configuration or project file may be touched.
    assert fake.calls == []
    assert not (checkout / "AGENTS.md").exists()


def test_connect_does_not_accept_unparseable_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    name = server_name(workspace, checkout)
    fake.servers[name] = _connect_args(workspace, checkout)

    def broken_get(argv: list[str], **kwargs: object) -> SimpleNamespace:
        if argv[1:3] == ["mcp", "get"]:
            return SimpleNamespace(returncode=0, stdout="not json at all", stderr="")
        return fake.run(argv, **kwargs)

    monkeypatch.setattr("memoryforge.codex_connect.subprocess.run", broken_get)

    with pytest.raises(CodexConnectConflictError):
        connect_codex(workspace, checkout)
    assert [call for call in fake.calls if call[1:3] == ["mcp", "add"]] == []


def test_connect_conflicts_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    name = server_name(workspace, checkout)
    fake.servers[name] = ["/usr/bin/python3", "-m", "other_server", "--flag"]

    with pytest.raises(CodexConnectConflictError) as caught:
        connect_codex(workspace, checkout)
    message = str(caught.value)
    assert "different command" in message
    assert "Refusing to overwrite" in message
    assert [call for call in fake.calls if call[1:3] == ["mcp", "add"]] == []
    # The AGENTS.md must not be rewritten after a failed connection.
    assert not (checkout / "AGENTS.md").exists()


def test_connect_allow_local_is_fixed_in_the_server_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    name = server_name(workspace, checkout)

    result = connect_codex(workspace, checkout, allow_local=True)

    assert result["status"] == "connected"
    assert fake.servers[name] == [*_connect_args(workspace, checkout), "--allow-local-llm"]
    assert fake.servers[name][-1] == "--allow-local-llm"


def test_connect_two_projects_in_one_workspace_coexist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout_a, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    checkout_b = tmp_path / "repo-b"
    checkout_b.mkdir()
    _git_repo(checkout_b, {"README.md": "# Repo B\n"})
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert (
        runner.invoke(
            app,
            ["git-add", str(checkout_b), "--public", "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)

    result_a = connect_codex(workspace, checkout_a)
    result_b = connect_codex(workspace, checkout_b)

    assert result_a["server"] != result_b["server"]
    assert set(fake.servers) == {result_a["server"], result_b["server"]}
    assert (checkout_a / "AGENTS.md").read_text(encoding="utf-8").count(AGENTS_MCP_BEGIN) == 1
    assert (checkout_b / "AGENTS.md").read_text(encoding="utf-8").count(AGENTS_MCP_BEGIN) == 1


def test_connect_two_workspaces_same_project_do_not_collide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_a, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    workspace_b = tmp_path / "workspace-b"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace_b)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["git-add", str(checkout), "--public", "--workspace", str(workspace_b)],
        ).exit_code
        == 0
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)

    result_a = connect_codex(workspace_a, checkout)
    result_b = connect_codex(workspace_b, checkout)

    assert result_a["server"] != result_b["server"]
    assert set(fake.servers) == {result_a["server"], result_b["server"]}


def test_agents_block_preserves_unrelated_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    agents_path = checkout / "AGENTS.md"
    agents_path.write_text("# My project\n\nCustom instructions for the agent.\n", encoding="utf-8")
    fake = _FakeCodex()
    fake.install(monkeypatch)

    connect_codex(workspace, checkout)

    text = agents_path.read_text(encoding="utf-8")
    assert text.startswith("# My project\n\nCustom instructions for the agent.")
    assert text.count(AGENTS_MCP_BEGIN) == 1
    assert text.index(AGENTS_MCP_BEGIN) > text.index("# My project")


def test_connect_replaces_the_legacy_recall_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    agents_path = checkout / "AGENTS.md"
    install_agents_block(
        agents_path,
        kind="recall",
        recall_command=[sys.executable, "-m", "memoryforge", "recall"],
    )
    assert AGENTS_RECALL_BEGIN in agents_path.read_text(encoding="utf-8")
    fake = _FakeCodex()
    fake.install(monkeypatch)

    connect_codex(workspace, checkout)

    text = agents_path.read_text(encoding="utf-8")
    assert AGENTS_MCP_BEGIN in text
    assert AGENTS_RECALL_BEGIN not in text
    assert AGENTS_RECALL_END not in text


def test_codex_setup_removes_the_mcp_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    connect_codex(workspace, checkout)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["codex-setup", str(checkout), "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    agents = (checkout / "AGENTS.md").read_text(encoding="utf-8")
    assert AGENTS_RECALL_BEGIN in agents
    assert AGENTS_MCP_BEGIN not in agents
    payload = json.loads(result.stdout)
    assert payload["status"] == "configured"


def test_install_agents_block_rejects_incomplete_markers(tmp_path: Path) -> None:
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text(f"some text\n{AGENTS_MCP_BEGIN}\nno end marker here\n", encoding="utf-8")

    with pytest.raises(ValueError, match="incomplete MemoryForge mcp block"):
        install_agents_block(agents_path, kind="mcp", server_name="s", project_name="p")


def test_connect_cli_outputs_structured_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["connect", "codex", str(checkout), "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "connected"
    assert payload["server"] == server_name(workspace, checkout)
    assert payload["repository_id"]
    assert str(payload["command"]) == str(_connect_args(workspace, checkout))


def test_doctor_reports_codex_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCodex()
    fake.servers["memoryforge-demo-12345678"] = ["/usr/bin/python3", "-m", "x"]
    monkeypatch.setattr(
        "memoryforge.interface.doctor.shutil.which",
        lambda name: _FAKE_CODEX if name == "codex" else None,
    )
    monkeypatch.setattr("memoryforge.interface.doctor.subprocess.run", fake.run)
    runner = CliRunner()

    result = runner.invoke(app, ["doctor", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    codex_check = next(check for check in report["checks"] if check["name"] == "codex")
    assert codex_check["status"] == "ok"
    assert "1 MemoryForge MCP server(s) registered" in codex_check["message"]


def test_doctor_reports_missing_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("memoryforge.interface.doctor.shutil.which", lambda name: None)
    runner = CliRunner()

    result = runner.invoke(app, ["doctor", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    codex_check = next(check for check in report["checks"] if check["name"] == "codex")
    assert codex_check["status"] == "not_configured"
    assert "Install the Codex CLI" in codex_check["remediation"]


def test_connect_rejects_unsupported_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["connect", "claude", str(checkout), "--workspace", str(workspace)],
    )

    assert result.exit_code != 0
    assert "unsupported AI Host 'claude'" in result.output
    assert fake.calls == []


def test_doctor_reports_managed_agents_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    fake = _FakeCodex()
    fake.install(monkeypatch)
    connect_codex(workspace, checkout)
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    block_check = next(check for check in report["checks"] if check["name"] == "agents_block")
    assert block_check["status"] == "ok"
    assert "1 registered project(s) carry a managed MemoryForge block" in block_check["message"]


def test_doctor_reports_missing_managed_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    block_check = next(check for check in report["checks"] if check["name"] == "agents_block")
    assert block_check["status"] == "not_configured"
    assert "memoryforge connect codex" in block_check["remediation"]


def test_connect_fails_closed_for_unmapped_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    unregistered = tmp_path / "unregistered"
    unregistered.mkdir()
    fake = _FakeCodex()
    fake.install(monkeypatch)

    with pytest.raises(UnmappedProjectError):
        connect_codex(workspace, unregistered)
    assert fake.calls == []


def _git_repo(root: Path, files: dict[str, str]) -> None:
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    for path, content in files.items():
        (root / path).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "Add documentation"],
        check=True,
        capture_output=True,
    )
