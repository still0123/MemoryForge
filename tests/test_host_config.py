"""Phase 5 tests: copyable MCP config snippets for AI Hosts.

``memoryforge mcp-config`` prints the standard ``mcpServers`` JSON object or
the Codex ``[mcp_servers.*]`` TOML block for one binding. Only Codex has an
official CLI adapter (``connect codex``); every other Host gets configuration
text only — the command must never create or edit Host files (spec Phase 5).
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.query.agent_access import server_name
from memoryforge.interface.cli import app
from memoryforge.interface.codex_connect import mcp_command
from memoryforge.core.host_config import codex_toml_block, mcp_servers_config


def _make_checkout(root: Path, name: str) -> Path:
    checkout = root / name
    checkout.mkdir()
    subprocess.run(["git", "-C", str(checkout), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    (checkout / "README.md").write_text("# Service\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(checkout), "add", "."],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-m", "Add documentation"],
        check=True,
        capture_output=True,
    )
    return checkout


def _registered_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Register one checkout in an initialized Workspace; no sync required."""
    checkout = _make_checkout(tmp_path, "repository")
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    registered = runner.invoke(
        app,
        ["git-add", str(checkout), "--workspace", str(workspace)],
    )
    assert registered.exit_code == 0, registered.output
    return workspace, checkout


def test_mcp_servers_json_uses_the_registered_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)

    config = mcp_servers_config(workspace, checkout)
    name = server_name(workspace, checkout)
    assert list(config) == ["mcpServers"]
    assert list(config["mcpServers"]) == [name]
    entry = config["mcpServers"][name]
    assert entry["command"] == mcp_command(workspace, checkout)[0]
    assert entry["args"] == mcp_command(workspace, checkout)[1:]
    arguments = [str(part) for part in mcp_command(workspace, checkout)]
    assert arguments[1:4] == ["-m", "memoryforge", "mcp"]
    assert arguments[arguments.index("--workspace") + 1] == str(workspace.resolve())
    assert arguments[arguments.index("--project-root") + 1] == str(checkout.resolve())


def test_mcp_servers_json_allow_local_appends_the_fixed_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)

    config = mcp_servers_config(workspace, checkout, allow_local=True)
    entry = config["mcpServers"][server_name(workspace, checkout)]
    assert entry["args"][-1] == "--allow-local-llm"
    assert (
        entry["args"][:-1]
        == mcp_servers_config(workspace, checkout)["mcpServers"][server_name(workspace, checkout)][
            "args"
        ]
    )


def test_codex_toml_block_round_trips_to_the_same_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)

    parsed = tomllib.loads(codex_toml_block(workspace, checkout))
    name = server_name(workspace, checkout)
    assert list(parsed) == ["mcp_servers"]
    assert list(parsed["mcp_servers"]) == [name]
    registered = parsed["mcp_servers"][name]
    assert registered["command"] == mcp_command(workspace, checkout)[0]
    assert registered["args"] == mcp_command(workspace, checkout)[1:]


def test_mcp_config_cli_prints_the_json_snippet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["mcp-config", "--project-root", str(checkout), "--workspace", str(workspace)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == mcp_servers_config(workspace, checkout)


def test_mcp_config_cli_prints_the_toml_snippet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--project-root",
            str(checkout),
            "--workspace",
            str(workspace),
            "--format",
            "toml",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == codex_toml_block(workspace, checkout) + "\n"


def test_mcp_config_fails_closed_for_unmapped_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = _registered_workspace(tmp_path, monkeypatch)
    runner = CliRunner()

    unregistered = tmp_path / "unregistered"
    unregistered.mkdir()
    result = runner.invoke(
        app,
        ["mcp-config", "--project-root", str(unregistered), "--workspace", str(workspace)],
    )
    assert result.exit_code != 0
    assert "not inside any registered Git checkout" in result.output


def test_mcp_config_rejects_an_unknown_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "mcp-config",
            "--project-root",
            str(checkout),
            "--workspace",
            str(workspace),
            "--format",
            "yaml",
        ],
    )
    assert result.exit_code != 0
    assert "unsupported --format" in result.output


def test_mcp_config_never_edits_host_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    runner = CliRunner()

    for config_format in ("json", "toml"):
        result = runner.invoke(
            app,
            [
                "mcp-config",
                "--project-root",
                str(checkout),
                "--workspace",
                str(workspace),
                "--format",
                config_format,
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.stdout.strip()
    assert list(home.rglob("*")) == []
