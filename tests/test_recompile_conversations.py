from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.core.platform_lock as platform_lock
import memoryforge.interface.cli as cli
from memoryforge.compiler.compiler import (
    _candidate_file_identities,
    _load_current_sources,
    _read_source_text,
    compile_pending_sources,
    current_conversation_source_ids,
)
from memoryforge.core.models import ChangeOperationType, PageChange
from memoryforge.query.sessions import remember_conversation
from memoryforge.storage.changesets import ChangeSetStore
from memoryforge.storage.workspace import Workspace
from tests.cli_helpers import review_approve_apply


def test_recompile_conversations_returns_no_sources(tmp_path: Path, monkeypatch) -> None:
    runner, workspace = _workspace(tmp_path, monkeypatch)

    result = runner.invoke(
        cli.app,
        ["recompile", "conversations", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "status": "no_sources",
        "target": "conversations",
        "source_count": 0,
        "compiler": "deterministic",
        "model": None,
        "reasoning_effort": None,
    }


def test_recompile_rejects_invalid_target(tmp_path: Path, monkeypatch) -> None:
    runner, workspace = _workspace(tmp_path, monkeypatch)

    result = runner.invoke(
        cli.app,
        ["recompile", "documents", "--workspace", str(workspace)],
    )

    assert result.exit_code != 0
    assert "recompile target must be conversations" in result.output


def test_recompile_stages_applied_conversation_as_proposed(tmp_path: Path, monkeypatch) -> None:
    runner, workspace = _workspace(tmp_path, monkeypatch)
    source_id = _remember(workspace, 1)
    initial = runner.invoke(cli.app, ["ingest", "--workspace", str(workspace)])
    assert initial.exit_code == 0, initial.output
    applied = review_approve_apply(
        runner,
        json.loads(initial.stdout)["changeset_id"],
        workspace,
    )
    assert applied.exit_code == 0, applied.output

    result = runner.invoke(
        cli.app,
        ["recompile", "conversations", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "PROPOSED"
    assert payload["target"] == "conversations"
    assert payload["source_count"] == 1
    assert payload["compiler"] == "deterministic"
    assert payload["model"] is None
    assert payload["reasoning_effort"] is None
    assert "title" not in payload
    stored = ChangeSetStore(Workspace.open(workspace)).get(payload["changeset_id"])
    assert stored.changeset.status.value == "PROPOSED"
    assert stored.changeset.source_ids == (source_id,)


def test_recompile_selects_all_current_conversations(tmp_path: Path, monkeypatch) -> None:
    runner, workspace = _workspace(tmp_path, monkeypatch)
    expected = {_remember(workspace, index) for index in range(101)}
    captured: dict[str, tuple[str, ...]] = {}

    def fake_compile(
        opened,
        *,
        source_ids=(),
        provider=None,
        allow_local=False,
        reorganize_existing=False,
    ):
        del opened, provider, allow_local, reorganize_existing
        captured["source_ids"] = source_ids
        return None

    monkeypatch.setattr(cli, "compile_pending_sources", fake_compile)
    result = runner.invoke(
        cli.app,
        ["recompile", "conversations", "--workspace", str(workspace)],
    )

    assert result.exit_code != 0
    assert set(captured["source_ids"]) == expected
    assert len(captured["source_ids"]) == 101


def test_trae_privacy_gate_fails_before_provider_construction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _workspace(tmp_path, monkeypatch)
    _remember(workspace, 1)

    def fail_provider():
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(cli, "TraeCliProvider", fail_provider)
    result = runner.invoke(
        cli.app,
        ["recompile", "conversations", "--trae", "--workspace", str(workspace)],
    )

    assert result.exit_code != 0
    assert "--trae requires --allow-local-llm" in result.output


def test_authorized_trae_path_uses_fixed_model_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path = _workspace(tmp_path, monkeypatch)
    _remember(workspace_path, 1)
    workspace = Workspace.open(workspace_path)
    source_ids = current_conversation_source_ids(workspace)
    compilation = compile_pending_sources(workspace, source_ids=source_ids)
    assert compilation is not None
    provider = object()
    monkeypatch.setattr(cli, "TraeCliProvider", lambda: provider)

    def fake_compile(
        opened,
        *,
        source_ids=(),
        provider=None,
        allow_local=False,
        reorganize_existing=False,
    ):
        del opened
        assert source_ids == current_conversation_source_ids(workspace)
        assert provider is provider_marker
        assert allow_local is True
        assert reorganize_existing is True
        return compilation

    provider_marker = provider
    monkeypatch.setattr(cli, "compile_pending_sources", fake_compile)

    result = runner.invoke(
        cli.app,
        [
            "recompile",
            "conversations",
            "--trae",
            "--allow-local-llm",
            "--workspace",
            str(workspace_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "PROPOSED"
    assert payload["compiler"] == "trae"
    assert payload["model"] == "gpt-5.6-sol__max"
    assert payload["reasoning_effort"] == "xhigh"


def test_explicit_reorganization_archives_replaced_source_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path = _workspace(tmp_path, monkeypatch)
    source_ids = (_remember(workspace_path, 1), _remember(workspace_path, 2))
    initial = runner.invoke(cli.app, ["ingest", "--workspace", str(workspace_path)])
    assert initial.exit_code == 0, initial.output
    applied = review_approve_apply(
        runner,
        json.loads(initial.stdout)["changeset_id"],
        workspace_path,
    )
    assert applied.exit_code == 0, applied.output

    workspace = Workspace.open(workspace_path)
    sources = {
        source.source_id: source for source in _load_current_sources(workspace, set(source_ids))
    }
    citations = []
    for index, source_id in enumerate(source_ids, start=1):
        content = _read_source_text(workspace, sources[source_id])
        sentence = f"Synthetic answer {index}."
        start = content.index(sentence)
        citations.append(
            {
                "source_id": source_id,
                "locator": f"chars:{start}-{start + len(sentence)}",
            }
        )
    change = PageChange(
        path="wiki/pages/conversation-topic.md",
        title="Synthetic conversation topic",
        page_type="synthesis",
        summary="Two synthetic conversations cover one topic.",
        body="The conversations are grouped into one reviewable topic.",
        source_ids=source_ids,
        citations=tuple(citations),
    )

    class Provider:
        def compile_pages(self, messages):
            del messages
            return (change,)

    compilation = compile_pending_sources(
        workspace,
        source_ids=source_ids,
        provider=Provider(),
        allow_local=True,
        reorganize_existing=True,
    )

    assert compilation is not None
    archived = {
        operation.path
        for operation in compilation.changeset.operations
        if operation.type is ChangeOperationType.ARCHIVE_PAGE
    }
    assert archived == {f"wiki/pages/{source_id}.md" for source_id in source_ids}
    merged_pages = set(compilation.candidate_files) - {"wiki/INDEX.md"}
    assert len(merged_pages) == 1
    assert not archived & merged_pages

    stored = ChangeSetStore(workspace).create(
        compilation.changeset,
        compilation.candidate_files,
    )
    applied = review_approve_apply(
        runner,
        stored.changeset.changeset_id,
        workspace_path,
    )
    assert applied.exit_code == 0, applied.output

    rebuilt = compile_pending_sources(
        Workspace.open(workspace_path),
        source_ids=source_ids,
    )
    assert rebuilt is not None
    rebuilt_page = rebuilt.candidate_files[next(iter(merged_pages))]
    assert "The conversations are grouped into one reviewable topic." in rebuilt_page
    assert "## Conversation notes (unverified)" in rebuilt_page
    assert "Synthetic answer 1." in rebuilt_page
    assert "Synthetic answer 2." in rebuilt_page


def test_reorganization_rejects_conversation_disclaimer_citations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace_path = _workspace(tmp_path, monkeypatch)
    source_ids = (_remember(workspace_path, 1), _remember(workspace_path, 2))
    initial = runner.invoke(cli.app, ["ingest", "--workspace", str(workspace_path)])
    assert initial.exit_code == 0, initial.output
    applied = review_approve_apply(
        runner,
        json.loads(initial.stdout)["changeset_id"],
        workspace_path,
    )
    assert applied.exit_code == 0, applied.output

    workspace = Workspace.open(workspace_path)
    sources = {
        source.source_id: source for source in _load_current_sources(workspace, set(source_ids))
    }
    citations = []
    disclaimer = "Conversation draft. Assistant responses are unverified until review and apply."
    for source_id in source_ids:
        content = _read_source_text(workspace, sources[source_id])
        start = content.index(disclaimer)
        citations.append(
            {
                "source_id": source_id,
                "locator": f"chars:{start}-{start + len(disclaimer)}",
            }
        )
    change = PageChange(
        path="wiki/pages/conversation-topic.md",
        title="Synthetic conversation topic",
        page_type="synthesis",
        summary="Two synthetic conversations cover one topic.",
        body="The conversations are grouped into one reviewable topic.",
        source_ids=source_ids,
        citations=tuple(citations),
    )

    class Provider:
        def compile_pages(self, messages):
            del messages
            return (change,)

    with pytest.raises(ValueError, match="substantive Assistant/Codex content"):
        compile_pending_sources(
            workspace,
            source_ids=source_ids,
            provider=Provider(),
            allow_local=True,
            reorganize_existing=True,
        )


def test_candidate_content_identity_is_stable_and_changes_with_output() -> None:
    first = {"wiki/pages/conversation.md": "first\n"}
    same = {"wiki/pages/conversation.md": "first\n"}
    changed = {"wiki/pages/conversation.md": "second\n"}

    assert _candidate_file_identities(first) == _candidate_file_identities(same)
    assert _candidate_file_identities(first) != _candidate_file_identities(changed)


def test_deterministic_recompile_identity_tracks_rendered_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runner, workspace_path = _workspace(tmp_path, monkeypatch)
    _remember(workspace_path, 1)
    workspace = Workspace.open(workspace_path)
    source_ids = current_conversation_source_ids(workspace)

    first = compile_pending_sources(workspace, source_ids=source_ids)
    repeated = compile_pending_sources(workspace, source_ids=source_ids)
    assert first is not None
    assert repeated is not None
    assert first.changeset.changeset_id == repeated.changeset.changeset_id

    import memoryforge.compiler.compiler as compiler

    original = compiler._render_conversation_page

    def changed_render(source, content):
        return original(source, content) + "\nChanged compiler output.\n"

    monkeypatch.setattr(compiler, "_render_conversation_page", changed_render)
    changed = compile_pending_sources(workspace, source_ids=source_ids)

    assert changed is not None
    assert changed.changeset.base_commit == first.changeset.base_commit
    assert changed.changeset.source_versions == first.changeset.source_versions
    assert changed.changeset.changeset_id != first.changeset.changeset_id


def _workspace(tmp_path: Path, monkeypatch) -> tuple[CliRunner, Path]:
    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o700)
    monkeypatch.setattr(
        platform_lock,
        "inspect_posix_namespace_lock_root",
        lambda *, create=False: lock_root,
    )
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    initialized = runner.invoke(cli.app, ["init", str(workspace)])
    assert initialized.exit_code == 0, initialized.output
    return runner, workspace


def _remember(workspace: Path, index: int) -> str:
    result = remember_conversation(
        workspace,
        platform="codex",
        conversation_id=f"synthetic-{index}",
        messages=[
            ("user", f"Question {index}?"),
            ("assistant", f"Synthetic answer {index}."),
        ],
    )
    assert result is not None
    return result.source_id
