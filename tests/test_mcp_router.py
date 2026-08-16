"""One global MCP server routes only through the Host's current Root."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from memoryforge.interface.mcp_server import (
    _router_project_from_context,
    _router_status_payload,
    _RouterBindings,
    _select_router_project_root,
    build_router_server,
)
from memoryforge.storage.workspace import Workspace
from tests.test_agent_access import _make_checkout


def _registered_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    from typer.testing import CliRunner

    from memoryforge.interface.cli import app

    checkout = _make_checkout(tmp_path, "repository", {"README.md": "# Router fixture\n"})
    workspace = tmp_path / "workspace"
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    result = runner.invoke(
        app,
        ["git-add", str(checkout), "--public", "--workspace", str(workspace)],
    )
    assert result.exit_code == 0, result.output
    return workspace, checkout


def _register_second_checkout(
    workspace: Path,
    checkout: Path,
) -> None:
    from typer.testing import CliRunner

    from memoryforge.interface.cli import app

    result = CliRunner().invoke(
        app,
        ["git-add", str(checkout), "--public", "--workspace", str(workspace)],
    )
    assert result.exit_code == 0, result.output


def test_router_selects_the_registered_checkout_for_one_file_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout_a = _registered_workspace(tmp_path, monkeypatch)
    checkout_b = _make_checkout(tmp_path, "repo-b", {"README.md": "# Repo B\n"})
    _register_second_checkout(workspace, checkout_b)
    nested = checkout_b / "src"
    nested.mkdir()

    selected = _select_router_project_root(workspace, [nested.resolve().as_uri()])

    assert selected == nested.resolve()
    assert _select_router_project_root(workspace, [checkout_a.resolve().as_uri()]) == checkout_a


def test_router_uses_no_preference_for_multiple_registered_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout_a = _registered_workspace(tmp_path, monkeypatch)
    checkout_b = _make_checkout(tmp_path, "repo-b", {"README.md": "# Repo B\n"})
    _register_second_checkout(workspace, checkout_b)

    assert (
        _select_router_project_root(
            workspace,
            [checkout_a.resolve().as_uri(), checkout_b.resolve().as_uri()],
        )
        is None
    )


def test_router_returns_unavailable_for_unregistered_or_missing_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _checkout = _registered_workspace(tmp_path, monkeypatch)
    unregistered = tmp_path / "unregistered"
    unregistered.mkdir()

    assert _select_router_project_root(workspace, []) is None
    assert _select_router_project_root(workspace, [unregistered.resolve().as_uri()]) is None


def test_router_context_uses_the_host_root_as_preference_and_falls_back_to_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)

    class RootsSession:
        client_capabilities = SimpleNamespace(roots=True)

        async def list_roots(self) -> SimpleNamespace:
            return SimpleNamespace(roots=(SimpleNamespace(uri=checkout.resolve().as_uri()),))

    class MissingRootsSession:
        client_capabilities = SimpleNamespace(roots=True)

        async def list_roots(self) -> SimpleNamespace:
            raise RuntimeError("roots unsupported")

    async def resolve(session: object) -> Path | None:
        context = SimpleNamespace(request_context=SimpleNamespace(session=session))
        return await _router_project_from_context(workspace, context)

    selected = asyncio.run(resolve(RootsSession()))
    unavailable = asyncio.run(resolve(MissingRootsSession()))

    assert selected == checkout
    assert unavailable is None


def test_router_status_filters_private_repositories_and_lists_languages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from memoryforge.interface.cli import app

    public_repo = _make_checkout(tmp_path, "public-go", {"main.go": "package main\n"})
    private_repo = _make_checkout(tmp_path, "private-py", {"app.py": "print('ok')\n"})
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    repository_ids: dict[str, str] = {}
    for checkout, public in ((public_repo, True), (private_repo, False)):
        arguments = ["git-add", str(checkout)]
        if public:
            arguments.append("--public")
        arguments.extend(["--workspace", str(workspace)])
        registered = runner.invoke(app, arguments)
        assert registered.exit_code == 0, registered.output
        repository_ids[checkout.name] = json.loads(registered.stdout)["repository_id"]

    index_path = Workspace.open_readonly(workspace).index_path
    with sqlite3.connect(index_path) as connection:
        for version_id, (name, path, sensitivity) in enumerate(
            (
                ("public-go", "main.go", "public"),
                ("private-py", "app.py", "local_only"),
            ),
            start=1,
        ):
            source_id = str(version_id) * 64
            content_hash = chr(96 + version_id) * 64
            connection.execute(
                "INSERT INTO blobs VALUES (?, ?, ?, 1, '2026-08-14T00:00:00+00:00')",
                (version_id, content_hash, f"raw/blobs/{version_id}.blob"),
            )
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, NULL, 'local', ?)",
                (
                    version_id,
                    source_id,
                    f"mf://source/{source_id}",
                    path,
                    "2026-08-14T00:00:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO source_versions VALUES "
                "(?, ?, ?, NULL, ?, 'refs', ?, ?, ?, ?, NULL, 1)",
                (
                    version_id,
                    version_id,
                    version_id,
                    "text/x-go" if path.endswith(".go") else "text/x-python",
                    path,
                    "2026-08-14T00:00:00+00:00",
                    sensitivity,
                    '["code"]',
                ),
            )
            commit = str(version_id) * 40
            connection.execute(
                "UPDATE git_repositories SET last_synced_commit = ? WHERE repository_id = ?",
                (commit, repository_ids[name]),
            )
            connection.execute(
                "INSERT INTO git_source_revisions VALUES (?, ?, ?, ?)",
                (version_id, repository_ids[name], path, commit),
            )
            connection.execute(
                "INSERT INTO applied_source_versions VALUES (?, ?)",
                (source_id, version_id),
            )
        connection.commit()

    public_status = _router_status_payload(_RouterBindings(workspace=workspace, allow_local=False))
    local_status = _router_status_payload(_RouterBindings(workspace=workspace, allow_local=True))

    assert public_status["registered_repositories"] == 1
    public = public_status["repositories"][0]
    assert public["name"] == "public-go"
    assert public["languages"] == ["Go"]
    assert public["source_count"] == 1
    assert {item["name"] for item in local_status["repositories"]} == {
        "public-go",
        "private-py",
    }
    private = next(item for item in local_status["repositories"] if item["name"] == "private-py")
    assert private["languages"] == ["Python"]
    assert private["source_count"] == 1


def test_global_router_exposes_workspace_read_tools_without_a_current_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout = _registered_workspace(tmp_path, monkeypatch)

    async def scenario() -> None:
        from mcp import types
        from mcp.client import Client

        async def list_roots(_context: object) -> types.ListRootsResult:
            return types.ListRootsResult(roots=[types.Root(uri=checkout.resolve().as_uri())])

        async with Client(
            build_router_server(workspace), list_roots_callback=list_roots, mode="legacy"
        ) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "memoryforge_context",
                "memoryforge_read_evidence",
                "memoryforge_recall",
                "memoryforge_episodes",
                "memoryforge_sessions",
                "memoryforge_load_session",
                "memoryforge_status",
            }
            for tool in tools:
                assert tool.annotations is not None
                assert tool.annotations.read_only_hint is True
                assert "repository_id" not in tool.input_schema.get("properties", {})
            result = await client.call_tool("memoryforge_context", {"question": "What is this?"})
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["status"] == "ok"
            assert result.structured_content["evidence_status"] == "no_local_evidence"
            scope = result.structured_content["scope"]
            assert scope["mode"] == "workspace"
            preferred = scope["preferred_repository"]
            assert preferred["name"] == "repository"
            assert preferred["repository_id"]

        async with Client(build_router_server(workspace)) as client:
            result = await client.call_tool(
                "memoryforge_context",
                {"question": "What is this?", "project_root": str(checkout)},
            )
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["status"] == "ok"
            assert result.structured_content["scope"]["preferred_repository"] is not None

        async with Client(build_router_server(workspace)) as client:
            result = await client.call_tool("memoryforge_context", {"question": "What is this?"})
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["status"] == "ok"
            assert result.structured_content["scope"]["preferred_repository"] is None

    asyncio.run(scenario())
