"""One global MCP server routes only through the Host's current Root."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from memoryforge.mcp_server import (
    RouterProjectAmbiguousError,
    _router_project_from_context,
    _select_router_project_root,
    build_router_server,
)
from tests.test_agent_access import _make_checkout


def _registered_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    from typer.testing import CliRunner

    from memoryforge.cli import app

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

    from memoryforge.cli import app

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


def test_router_rejects_multiple_registered_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout_a = _registered_workspace(tmp_path, monkeypatch)
    checkout_b = _make_checkout(tmp_path, "repo-b", {"README.md": "# Repo B\n"})
    _register_second_checkout(workspace, checkout_b)

    with pytest.raises(RouterProjectAmbiguousError):
        _select_router_project_root(
            workspace,
            [checkout_a.resolve().as_uri(), checkout_b.resolve().as_uri()],
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


def test_router_context_uses_the_host_root_and_fails_closed_without_one(
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

    async def resolve(session: object) -> tuple[Path | None, str]:
        context = SimpleNamespace(request_context=SimpleNamespace(session=session))
        return await _router_project_from_context(workspace, context)

    selected, status = asyncio.run(resolve(RootsSession()))
    unavailable, unavailable_status = asyncio.run(resolve(MissingRootsSession()))

    assert selected == checkout
    assert status == "ok"
    assert unavailable is None
    assert unavailable_status == "active_project_unavailable"


def test_global_router_exposes_only_root_scoped_read_tools(
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
                "memoryforge_status",
            }
            for tool in tools:
                assert tool.annotations is not None
                assert tool.annotations.read_only_hint is True
                assert "repository_id" not in tool.input_schema.get("properties", {})
            result = await client.call_tool("memoryforge_context", {"question": "What is this?"})
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["status"] == "unknown"
            repository = result.structured_content["repository"]
            assert repository["name"] == "repository"
            assert repository["repository_id"]

        async with Client(build_router_server(workspace)) as client:
            result = await client.call_tool(
                "memoryforge_context",
                {"question": "What is this?", "project_root": str(checkout)},
            )
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["status"] == "unknown"

    asyncio.run(scenario())
