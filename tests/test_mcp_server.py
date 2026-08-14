"""Phase 2 tests: the read-only MCP stdio server.

Covers tool/resource discovery contracts (fixed names, read-only annotations,
no model-supplied scope parameters), the L2/L3 behavior over a real temporary
Workspace (answered / unknown / evidence gates / repository isolation /
character budgets), the fail-closed startup binding, and a subprocess stdio
smoke test proving stdout carries only the protocol.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.core.errors import UnmappedProjectError
from memoryforge.interface.cli import app
from memoryforge.interface.mcp_server import build_server
from memoryforge.query.agent_access import server_name
from memoryforge.storage.workspace import WorkspaceError
from tests.cli_helpers import review_approve_apply
from tests.test_agent_access import (
    CACHE_POLICY,
    RETRY_POLICY,
    _bound_workspace,
    _conversation_workspace,
    _make_checkout,
)

_INSTRUCTIONS_LIMIT = 1200
_CONTEXT_LIMIT = 8000

TOOL_NAMES = {
    "memoryforge_context",
    "memoryforge_read_evidence",
    "memoryforge_recall",
    "memoryforge_sessions",
    "memoryforge_load_session",
    "memoryforge_status",
    "memoryforge_propose_update",
    "memoryforge_list_changesets",
    "memoryforge_review_changeset",
}


def _run(scenario) -> object:
    return asyncio.run(scenario())


async def _call(client, tool: str, arguments: dict[str, object]) -> dict[str, object]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result
    if result.structured_content is not None:
        return dict(result.structured_content)
    assert result.content, result
    return dict(json.loads(result.content[0].text))


def test_server_binding_fails_closed_for_unmapped_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    unregistered = tmp_path / "unregistered"
    unregistered.mkdir()

    with pytest.raises(UnmappedProjectError):
        build_server(workspace, unregistered)


def test_server_binding_fails_closed_for_uninitialized_workspace(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(WorkspaceError):
        build_server(tmp_path, project)


def test_server_name_is_stable_and_unique(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    assert server_name(first, first / "proj") == server_name(first, first / "proj")
    assert server_name(first, first / "proj") != server_name(first, second / "proj")
    assert server_name(first, first / "proj") != server_name(second, first / "proj")


def test_tool_discovery_contract_is_fixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}, public=True
    )

    async def scenario() -> None:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == TOOL_NAMES
            by_name = {tool.name: tool for tool in tools}
            for tool in tools:
                assert tool.annotations is not None
                assert tool.annotations.destructive_hint is False
                assert tool.annotations.open_world_hint is False
            for name in TOOL_NAMES - {"memoryforge_propose_update"}:
                assert by_name[name].annotations.read_only_hint is True
                assert by_name[name].annotations.idempotent_hint is True
            write_annotations = by_name["memoryforge_propose_update"].annotations
            assert write_annotations.read_only_hint is False
            assert write_annotations.idempotent_hint is False
            for name in TOOL_NAMES:
                properties = by_name[name].input_schema.get("properties", {})
                assert "project_path" not in properties
                assert "repository_id" not in properties
                assert "verify" not in properties
                assert "debug" not in properties
                assert "allow_local" not in properties
                assert "llm" not in properties
            context_properties = by_name["memoryforge_context"].input_schema["properties"]
            assert set(context_properties) == {"question", "max_pages", "max_citations"}
            assert by_name["memoryforge_context"].input_schema["required"] == ["question"]
            recall_properties = by_name["memoryforge_recall"].input_schema["properties"]
            assert set(recall_properties) == {"limit"}
            sessions_properties = by_name["memoryforge_sessions"].input_schema["properties"]
            assert set(sessions_properties) == {"limit"}
            load_properties = by_name["memoryforge_load_session"].input_schema["properties"]
            assert set(load_properties) == {"session_refs", "max_characters", "question"}

    _run(scenario)


def test_analysis_profile_does_not_advertise_unimplemented_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}, public=True
    )

    async def scenario() -> None:
        from mcp.client import Client

        server = build_server(workspace, checkout, profile="analysis")
        async with Client(server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            mode = tools["memoryforge_impact_analysis"].input_schema["properties"]["mode"]
            assert mode["enum"] == ["impact", "call_paths", "why_changed"]

    _run(scenario)


def test_instructions_stay_within_the_l0_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    server = build_server(workspace, checkout)
    assert len(server.instructions or "") <= _INSTRUCTIONS_LIMIT


def test_context_answers_bounded_context_from_bound_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}, public=True
    )

    async def scenario() -> dict[str, object]:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            return await _call(
                client, "memoryforge_context", {"question": "When do cache entries expire?"}
            )

    payload = _run(scenario)
    assert payload["status"] == "ok"
    assert payload["evidence_status"] == "grounded"
    assert payload["repository"] == {
        "repository_id": repository_id,
        "name": "repository",
    }
    assert "Cache entries expire after sixty seconds." in str(payload["answer_hint"])
    assert len(payload["wiki_pages"]) <= 3
    assert len(payload["citations"]) <= 6
    budget = payload["budget"]
    assert int(budget["output_characters"]) <= _CONTEXT_LIMIT
    assert len(json.dumps(payload, ensure_ascii=False)) <= _CONTEXT_LIMIT
    citation = payload["citations"][0]
    assert str(citation["wiki_page"]).startswith("wiki/pages/")
    assert citation["display_source"]  # friendly label, never a raw 64-char hash


def test_context_returns_unknown_when_evidence_is_insufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}, public=True
    )

    async def scenario() -> dict[str, object]:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            return await _call(
                client,
                "memoryforge_context",
                {"question": "What is the airspeed velocity of an unladen swallow?"},
            )

    payload = _run(scenario)
    assert payload["status"] == "ok"
    assert payload["evidence_status"] == "no_local_evidence"
    assert payload["answer_hint"] == ""


def test_context_rejects_an_empty_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )

    async def scenario() -> bool:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            result = await client.call_tool("memoryforge_context", {"question": "   "})
            return bool(result.is_error)

    assert _run(scenario) is True


def test_evidence_is_denied_without_local_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        from mcp.client import Client

        allowed_server = build_server(workspace, checkout, allow_local=True)
        async with Client(allowed_server) as client:
            context = await _call(
                client, "memoryforge_context", {"question": "When do cache entries expire?"}
            )
            citation = context["citations"][0]
            allowed = await _call(
                client,
                "memoryforge_read_evidence",
                {
                    "source_id": citation["source_id"],
                    "source_version": citation["source_version"],
                    "locator": citation["locator"],
                },
            )
        denied_server = build_server(workspace, checkout, allow_local=False)
        async with Client(denied_server) as client:
            denied = await _call(
                client,
                "memoryforge_read_evidence",
                {
                    "source_id": citation["source_id"],
                    "source_version": citation["source_version"],
                    "locator": citation["locator"],
                },
            )
        return denied, allowed

    denied, allowed = _run(scenario)
    assert denied["status"] == "local_scope_denied"
    assert allowed["status"] == "read"
    assert int(allowed["characters"]) <= 2000
    assert allowed["display_source"]


def test_evidence_returns_citation_not_found_for_unknown_citation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}, public=True
    )

    async def scenario() -> dict[str, object]:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            return await _call(
                client,
                "memoryforge_read_evidence",
                {
                    "source_id": "f" * 64,
                    "source_version": 1,
                    "locator": "chars:0-10",
                },
            )

    payload = _run(scenario)
    assert payload["status"] == "citation_not_found"


def test_context_isolates_repositories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout_a = _make_checkout(tmp_path, "repo-a", {"README.md": CACHE_POLICY})
    checkout_b = _make_checkout(tmp_path, "repo-b", {"README.md": RETRY_POLICY})
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    for checkout in (checkout_a, checkout_b):
        registered = runner.invoke(
            app,
            ["git-add", str(checkout), "--public", "--workspace", str(workspace)],
        )
        assert registered.exit_code == 0, registered.output
        repository_id = json.loads(registered.stdout)["repository_id"]
        assert (
            runner.invoke(
                app,
                ["git-sync", repository_id, "--workspace", str(workspace)],
            ).exit_code
            == 0
        )
        proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
        assert proposal.exit_code == 0, proposal.output
        applied = review_approve_apply(
            runner,
            json.loads(proposal.stdout)["changeset_id"],
            workspace,
        )
        assert applied.exit_code == 0, applied.output

    async def scenario() -> tuple[str, str]:
        from mcp.client import Client

        server_a = build_server(workspace, checkout_a)
        async with Client(server_a) as client:
            about_retries = await _call(
                client, "memoryforge_context", {"question": "When do retries stop?"}
            )
            about_cache = await _call(
                client, "memoryforge_context", {"question": "When do cache entries expire?"}
            )
        return (
            str(about_retries["evidence_status"]),
            str(about_cache["evidence_status"]),
        )

    about_retries, about_cache = _run(scenario)
    assert about_retries == "no_local_evidence"
    assert about_cache == "grounded"


def test_recall_filters_to_bound_repository_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _source_ids = _conversation_workspace(
        tmp_path,
        monkeypatch,
        [("Cleanup investigation", "Cleanup requires the finalizer.", True)],
    )
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git_repo(checkout, {"README.md": "# Service\n"})
    runner = CliRunner()
    assert (
        runner.invoke(
            app,
            ["git-add", str(checkout), "--public", "--workspace", str(workspace)],
        ).exit_code
        == 0
    )

    async def scenario() -> dict[str, object]:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            return await _call(client, "memoryforge_recall", {})

    payload = _run(scenario)
    # Conversation memory without repository scope must not leak into the
    # bound project (§9.2 memoryforge_recall rule).
    assert payload["status"] == "empty"


def test_recall_returns_scoped_memory_when_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    workspace, _source_ids = _conversation_workspace(
        tmp_path,
        monkeypatch,
        [("Cleanup investigation", "Cleanup requires the finalizer.", True)],
    )
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git_repo(checkout, {"README.md": "# Service\n"})
    runner = CliRunner()
    registered = runner.invoke(
        app,
        ["git-add", str(checkout), "--public", "--workspace", str(workspace)],
    )
    assert registered.exit_code == 0, registered.output
    repository_id = json.loads(registered.stdout)["repository_id"]
    with sqlite3.connect(workspace / ".memoryforge" / "index.sqlite") as connection:
        connection.execute(
            "UPDATE wiki_facts SET repository_id = ? WHERE repository_id IS NULL",
            (repository_id,),
        )

    async def scenario() -> dict[str, object]:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            return await _call(client, "memoryforge_recall", {"limit": 2})

    payload = _run(scenario)
    assert payload["status"] == "recalled"
    assert "Cleanup requires the finalizer." in str(payload["summary"])
    assert len(payload["startup_context"]) <= 4000


def test_status_tool_and_resource_are_consistent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}, public=True
    )

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        from mcp.client import Client

        server = build_server(workspace, checkout, allow_local=True)
        async with Client(server) as client:
            via_tool = await _call(client, "memoryforge_status", {})
            resource = await client.read_resource("memoryforge://status")
            assert resource is not None
            via_resource = json.loads(resource.contents[0].text)
        return via_tool, via_resource

    via_tool, via_resource = _run(scenario)
    assert via_tool == via_resource
    assert via_tool["status"] == "ok"
    assert via_tool["version"]
    assert via_tool["server"] == server_name(workspace, checkout)
    assert via_tool["project"] == "repository"
    assert via_tool["repository_id"] == repository_id
    assert len(str(via_tool["workspace_commit"])) == 40
    assert int(via_tool["applied_pages"]) >= 1
    assert int(via_tool["applied_sources"]) >= 1
    assert int(via_tool["pending_changesets"]) == 0
    assert via_tool["allow_local"] is True


def test_cli_mcp_subprocess_stdout_carries_only_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path, monkeypatch, {"README.md": CACHE_POLICY}
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "memoryforge",
            "mcp",
            "--workspace",
            str(workspace),
            "--project-root",
            str(checkout),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    def send(message: dict[str, object]) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"},
            },
        }
    )
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    responses: list[dict[str, object]] = []
    for _line in range(6):
        line = process.stdout.readline()
        if not line:
            break
        responses.append(json.loads(line))
        if any(response.get("id") == 2 for response in responses):
            break
    process.terminate()
    stderr = process.communicate()[1]

    ids = [response.get("id") for response in responses]
    assert 1 in ids and 2 in ids
    tools_list = next(response for response in responses if response.get("id") == 2)
    tool_names = {tool["name"] for tool in tools_list["result"]["tools"]}
    assert tool_names == TOOL_NAMES
    # stderr may carry SDK logging, but stdout must never carry non-protocol text.
    assert "stdout" not in stderr


def test_mcp_propose_update_stages_lists_and_previews_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )

    async def scenario() -> None:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            context = await _call(
                client,
                "memoryforge_context",
                {"question": "When do cache entries expire?"},
            )
            assert context["status"] == "ok"
            citation = dict(context["citations"][0])
            target_page = str(citation["wiki_page"])

            proposal = await _call(
                client,
                "memoryforge_propose_update",
                {
                    "question": "When do cache entries expire?",
                    "conclusion": str(citation["quote"]),
                    "citations": [
                        {
                            "source_id": str(citation["source_id"]),
                            "source_version": int(citation["source_version"]),
                            "locator": str(citation["locator"]),
                        }
                    ],
                    "target_page": target_page,
                },
            )
            assert proposal["status"] == "proposed"
            changeset_id = str(proposal["changeset_id"])
            assert proposal["risk"] == "high"
            assert proposal["next_action"] == "review"

            listed = await _call(client, "memoryforge_list_changesets", {})
            assert listed["status"] == "ok"
            assert any(
                entry["changeset_id"] == changeset_id and entry["status"] == "PROPOSED"
                for entry in listed["changesets"]
            )

            preview = await _call(
                client,
                "memoryforge_review_changeset",
                {"changeset_id": changeset_id},
            )
            assert preview["status"] == "ok"
            assert preview["reviewed_by_mcp"] is False
            assert any(
                entry["path"] == target_page and entry["action"] == "updated"
                for entry in preview["pages"]
            )
            # The MCP preview must never record a human review receipt.
            proposed_dir = workspace / ".memoryforge" / "staging" / "proposed"
            assert not list(proposed_dir.rglob("review.json"))

    _run(scenario)


def test_mcp_propose_update_rejects_unsupported_and_fabricated_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, checkout, _repository_id = _bound_workspace(
        tmp_path,
        monkeypatch,
        {"README.md": CACHE_POLICY},
        public=True,
    )

    async def scenario() -> None:
        from mcp.client import Client

        server = build_server(workspace, checkout)
        async with Client(server) as client:
            context = await _call(
                client,
                "memoryforge_context",
                {"question": "When do cache entries expire?"},
            )
            citation = dict(context["citations"][0])
            target_page = str(citation["wiki_page"])
            grounded = {
                "question": "When do cache entries expire?",
                "conclusion": str(citation["quote"]),
                "citations": [
                    {
                        "source_id": str(citation["source_id"]),
                        "source_version": int(citation["source_version"]),
                        "locator": str(citation["locator"]),
                    }
                ],
                "target_page": target_page,
            }

            unsupported = await _call(
                client,
                "memoryforge_propose_update",
                {**grounded, "conclusion": "The moon is made of cheese."},
            )
            assert unsupported["status"] == "insufficient_evidence"

            fabricated = await _call(
                client,
                "memoryforge_propose_update",
                {
                    **grounded,
                    "citations": [
                        {
                            "source_id": "f" * 64,
                            "source_version": 1,
                            "locator": "chars:0-5",
                        }
                    ],
                },
            )
            assert fabricated["status"] == "citation_not_found"

            assert not (workspace / ".memoryforge" / "staging" / "proposed").exists()

    _run(scenario)


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
