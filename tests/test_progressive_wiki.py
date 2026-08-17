from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from typer.testing import CliRunner

import memoryforge.compiler.compiler as compiler
from memoryforge.interface.cli import app
from memoryforge.storage.changesets import ChangeSetStore
from memoryforge.storage.workspace import Workspace
from tests.cli_helpers import review_approve_apply


def test_ingest_creates_typed_pages_with_frontmatter_and_an_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    _import(
        runner,
        workspace,
        workspace.parent / "project.md",
        "# Checkout Service\n\nThe service owns checkout requests.\n",
        "summary",
    )
    _import(
        runner,
        workspace,
        workspace.parent / "cache.md",
        "# Cache Design\n\nCache entries expire after sixty seconds.\n",
        "design",
    )
    _import(
        runner,
        workspace,
        workspace.parent / "retro.md",
        "# Checkout Postmortem\n\nThe incident was caused by a stale cache.\n",
        "postmortem",
    )

    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])

    assert ingested.exit_code == 0, ingested.output
    proposal = json.loads(ingested.stdout)
    assert "wiki/INDEX.md" in proposal["files"]
    page_files = [path for path in proposal["files"] if path != "wiki/INDEX.md"]
    assert len(page_files) == 3
    assert all(path.startswith("wiki/pages/") for path in page_files)

    reviewed = runner.invoke(
        app,
        ["review", proposal["changeset_id"], "--workspace", str(workspace)],
    )

    assert reviewed.exit_code == 0, reviewed.output
    candidates = json.loads(reviewed.stdout)["candidate_files"]
    for path, content in candidates.items():
        if path == "wiki/INDEX.md":
            continue
        assert content.startswith("---\n")
        assert "title: " in content
        assert "type: " in content
        assert "summary: " in content
        assert "tags: " in content
        assert "sources: " in content
        assert "source_version: " in content
        assert "updated: " in content
        assert "## Verified facts" in content
        assert "quote_sha256" not in content
    index = candidates["wiki/INDEX.md"]
    assert "## Entities" in index
    assert "## Concepts" in index
    assert "## Synthesis" in index
    assert "Checkout Service" in index
    assert "Cache Design" in index
    assert "Checkout Postmortem" in index


def test_conversation_page_keeps_older_search_anchors_and_prefers_recent_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    messages = []
    for index in range(6):
        answer = (
            "Historical status 0. 跳板机登录链路是本机 Kerberos、jump.byted.org、物理机。"
            if index == 0
            else f"Historical status {index}."
        )
        messages.extend(
            (
                f"## User\n\nWhat is status {index}?",
                f"## Assistant (unverified)\n\n{answer}",
            )
        )
    messages.extend(
        (
            "## User\n\nWhat is current status?",
            "## Assistant (unverified)\n\nfunc currentStatus() string\n\n"
            "Candidate 19 is accepted. Conversation memory is enabled.",
        )
    )
    imported = _import(
        runner,
        workspace,
        workspace.parent / "conversation.md",
        "# Codex conversation\n\n" + "\n\n".join(messages) + "\n",
        "notes",
        tags=("conversation", "platform:codex", "unverified"),
    )

    proposal = runner.invoke(
        app,
        ["ingest", "--source", str(imported["source_id"]), "--workspace", str(workspace)],
    )

    assert proposal.exit_code == 0, proposal.output
    reviewed = runner.invoke(
        app,
        [
            "review",
            json.loads(proposal.stdout)["changeset_id"],
            "--workspace",
            str(workspace),
        ],
    )
    assert reviewed.exit_code == 0, reviewed.output
    candidates = json.loads(reviewed.stdout)["candidate_files"]
    page = next(content for path, content in candidates.items() if path != "wiki/INDEX.md")
    index = candidates["wiki/INDEX.md"]
    assert "## Conversation notes (unverified)" in page
    assert "## Verified facts" not in page
    assert "### Assistant conclusions" in page
    assert "### User prompts (search only)" in page
    assert "User prompts are search clues only" in page
    assert page.index("Candidate 19 is accepted") < page.index("What is current status?")
    assert page.index("Candidate 19 is accepted") < page.index("func currentStatus")
    assert "Historical status 0" in page
    assert "Candidate 19 is accepted" in index

    applied = review_approve_apply(runner, json.loads(proposal.stdout)["changeset_id"], workspace)
    recalled = runner.invoke(app, ["recall", "--workspace", str(workspace)])

    assert applied.exit_code == 0, applied.output
    assert recalled.exit_code == 0, recalled.output
    memory = json.loads(recalled.stdout)
    assert memory["status"] == "recalled"
    assert memory["summary"] == "Candidate 19 is accepted. Conversation memory is enabled."
    assert memory["recent_memories"][0]["role"] == "Assistant conclusions"
    assert "Historical status 0" not in memory["startup_context"]
    assert memory["recent_memories"][0]["citation"]["wiki_page"].startswith("wiki/pages/")

    remembered = runner.invoke(
        app,
        ["ask", "跳板机是怎么连接的", "--workspace", str(workspace)],
    )
    assert remembered.exit_code == 0, remembered.output
    remembered_payload = json.loads(remembered.stdout)
    assert remembered_payload["status"] == "answered"
    assert "jump.byted.org" in remembered_payload["answer"]

    rebuilt = runner.invoke(
        app,
        ["ingest", "--source", str(imported["source_id"]), "--workspace", str(workspace)],
    )

    assert rebuilt.exit_code == 0, rebuilt.output
    assert json.loads(rebuilt.stdout)["status"] == "PROPOSED"


def test_recall_returns_one_assistant_memory_per_conversation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    for title, answer in (
        ("Cleanup investigation", "Cleanup requires the finalizer."),
        ("DataFlow concurrency", "CreateDataFlow uses an idempotency key."),
        ("CreateDataFlow review", "CreateDataFlow serializes duplicate names."),
    ):
        _import(
            runner,
            workspace,
            workspace.parent / f"{title}.md",
            f"# {title}\n\n## User\n\nWhat happened?\n\n## Assistant (unverified)\n\n{answer}\n",
            "notes",
            tags=("conversation", "platform:codex", "unverified"),
        )
        _apply_pending(runner, workspace)

    recalled = runner.invoke(app, ["recall", "--workspace", str(workspace)])

    assert recalled.exit_code == 0, recalled.output
    memories = json.loads(recalled.stdout)["recent_memories"]
    assert [memory["title"] for memory in memories] == [
        "CreateDataFlow review",
        "Cleanup investigation",
    ]
    assert len({memory["citation"]["wiki_page"] for memory in memories}) == 2
    assert all(memory["role"] == "Assistant conclusions" for memory in memories)
    assert "What happened?" not in recalled.stdout


def test_conversation_route_beats_a_generic_topic_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    _import(
        runner,
        workspace,
        workspace.parent / "generic.md",
        "# DataFlow\n\nDataFlow provides storage tiering.\n",
        "notes",
    )
    _import(
        runner,
        workspace,
        workspace.parent / "conversation.md",
        "# CreateDataFlow 并发\n\n## User\n\n如何避免重复创建？\n\n"
        "## Assistant (unverified)\n\nCreateDataFlow 并发创建使用 uid + name 锁，"
        "避免同名重复策略并把写入串行化。\n",
        "notes",
        tags=("conversation", "platform:codex", "unverified"),
    )
    _apply_pending(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "CreateDataFlow 并发创建重复策略如何解决？",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    answer = json.loads(result.stdout)
    assert answer["status"] == "answered"
    assert answer["answer"].startswith("CreateDataFlow 并发创建使用 uid + name 锁")


def test_ask_uses_index_to_select_a_page_then_returns_its_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    _import(
        runner,
        workspace,
        workspace.parent / "cache.md",
        "# Cache Design\n\nCache entries expire after sixty seconds.\n",
        "design",
    )
    _import(
        runner,
        workspace,
        workspace.parent / "deploy.md",
        "# Deployment\n\nDeployment runs every Friday.\n",
        "notes",
    )
    proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert proposal.exit_code == 0, proposal.output
    applied = review_approve_apply(runner, json.loads(proposal.stdout)["changeset_id"], workspace)
    assert applied.exit_code == 0, applied.output

    result = runner.invoke(
        app,
        ["ask", "When do cache entries expire?", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "answered"
    assert payload["answer"] == "Cache entries expire after sixty seconds."
    assert payload["wiki_pages"]
    assert payload["wiki_pages"][0].startswith("wiki/pages/")
    assert payload["source_id"]
    assert payload["source_version"] == 1


def test_source_metadata_changes_update_the_existing_wiki_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    source = workspace.parent / "cache.md"
    first = _import(
        runner,
        workspace,
        source,
        "# Cache Design\n\nCache entries expire after sixty seconds.\n",
        "design",
        tags=("cache",),
    )
    _apply_pending(runner, workspace)
    pages = sorted(path for path in (workspace / "wiki").glob("**/*.md") if path.name != "INDEX.md")
    assert len(pages) == 1
    original_page = pages[0]

    second = _import(
        runner,
        workspace,
        source,
        "# Cache Postmortem\n\nThe cache expired too late during the incident.\n",
        "postmortem",
        tags=("incident",),
    )
    assert second["source_id"] == first["source_id"]

    proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert proposal.exit_code == 0, proposal.output
    payload = json.loads(proposal.stdout)
    assert [path for path in payload["files"] if path != "wiki/INDEX.md"] == [
        str(original_page.relative_to(workspace))
    ]
    reviewed = runner.invoke(
        app,
        ["review", payload["changeset_id"], "--workspace", str(workspace)],
    )
    candidate_files = json.loads(reviewed.stdout)["candidate_files"]
    candidate = candidate_files[str(original_page.relative_to(workspace))]
    assert 'title: "Cache Postmortem"' in candidate
    assert "type: synthesis" in candidate
    assert 'tags: ["postmortem", "incident"]' in candidate

    _apply_pending(runner, workspace, payload["changeset_id"])
    pages = sorted(path for path in (workspace / "wiki").glob("**/*.md") if path.name != "INDEX.md")
    assert pages == [original_page]
    index = (workspace / "wiki/INDEX.md").read_text(encoding="utf-8")
    assert "Cache Postmortem" in index
    assert "Cache Design" not in index


def test_index_and_query_support_titles_with_brackets(tmp_path: Path, monkeypatch) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    _import(
        runner,
        workspace,
        workspace.parent / "abi.md",
        "# C++ [ABI]\n\nABI layout keeps the plugin compatible.\n",
        "design",
    )
    _apply_pending(runner, workspace)

    index = (workspace / "wiki/INDEX.md").read_text(encoding="utf-8")
    assert "[C++ \\[ABI\\]]" in index
    result = runner.invoke(
        app,
        ["ask", "What keeps the ABI compatible?", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["answer"] == "ABI layout keeps the plugin compatible."


def test_apply_records_versions_and_later_compile_reads_no_page_or_blob(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    imported = _import(
        runner,
        workspace,
        workspace.parent / "cache.md",
        "# Cache Design\n\nCache entries expire after sixty seconds.\n",
        "design",
    )
    proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert proposal.exit_code == 0, proposal.output
    changeset_id = json.loads(proposal.stdout)["changeset_id"]
    stored = ChangeSetStore(Workspace.open(workspace)).get(changeset_id)
    assert stored.changeset.source_versions == {imported["source_id"]: 1}

    _apply_pending(runner, workspace, changeset_id)

    with (
        closing(sqlite3.connect(workspace / ".memoryforge/index.sqlite")) as connection,
        connection,
    ):
        applied = connection.execute(
            "SELECT source_version_id FROM applied_source_versions WHERE source_id = ?",
            (imported["source_id"],),
        ).fetchone()
    assert applied == (1,)

    opened = Workspace.open(workspace)
    original_read_text = Path.read_text

    def reject_page_read(path: Path, *args: object, **kwargs: object) -> str:
        if path.is_relative_to(workspace / "wiki/pages"):
            raise AssertionError("unchanged Wiki pages must not be read")
        return original_read_text(path, *args, **kwargs)

    def reject_blob_read(_workspace: Workspace, _source: object) -> str:
        raise AssertionError("unchanged source blobs must not be read")

    monkeypatch.setattr(Path, "read_text", reject_page_read)
    monkeypatch.setattr(compiler, "_read_source_text", reject_blob_read)
    assert compiler.compile_pending_sources(opened) is None


def test_compile_pending_reads_only_changed_source_blob(tmp_path: Path, monkeypatch) -> None:
    runner, workspace = _initialized_workspace(tmp_path, monkeypatch)
    cache = workspace.parent / "cache.md"
    deployment = workspace.parent / "deployment.md"
    cache_source = _import(
        runner,
        workspace,
        cache,
        "# Cache Design\n\nCache entries expire after sixty seconds.\n",
        "design",
    )
    _import(
        runner,
        workspace,
        deployment,
        "# Deployment\n\nDeployment runs every Friday.\n",
        "notes",
    )
    _apply_pending(runner, workspace)
    _import(
        runner,
        workspace,
        cache,
        "# Cache Design\n\nCache entries expire after ninety seconds.\n",
        "design",
    )

    opened = Workspace.open(workspace)
    original_read_source = compiler._read_source_text
    read_source_ids: list[str] = []

    def track_blob_read(
        compilation_workspace: Workspace,
        source: compiler.CurrentSource,
    ) -> str:
        read_source_ids.append(source.source_id)
        return original_read_source(compilation_workspace, source)

    original_read_text = Path.read_text

    def reject_page_read(path: Path, *args: object, **kwargs: object) -> str:
        if path.is_relative_to(workspace / "wiki/pages"):
            raise AssertionError("incremental compilation must not read page frontmatter")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(compiler, "_read_source_text", track_blob_read)
    monkeypatch.setattr(Path, "read_text", reject_page_read)
    compilation = compiler.compile_pending_sources(opened)

    assert compilation is not None
    assert compilation.changeset.source_versions == {cache_source["source_id"]: 3}
    assert read_source_ids == [cache_source["source_id"]]


def _initialized_workspace(tmp_path: Path, monkeypatch) -> tuple[CliRunner, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()
    initialized = runner.invoke(app, ["init", str(workspace)])
    assert initialized.exit_code == 0, initialized.output
    return runner, workspace


def _import(
    runner: CliRunner,
    workspace: Path,
    path: Path,
    content: str,
    category: str,
    *,
    tags: tuple[str, ...] = (),
) -> dict[str, object]:
    path.write_text(content, encoding="utf-8")
    command = ["import", str(path), "--category", category, "--workspace", str(workspace)]
    for tag in tags:
        command.extend(["--tag", tag])
    result = runner.invoke(
        app,
        command,
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _apply_pending(runner: CliRunner, workspace: Path, changeset_id: str | None = None) -> None:
    if changeset_id is None:
        proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
        assert proposal.exit_code == 0, proposal.output
        changeset_id = json.loads(proposal.stdout)["changeset_id"]
    applied = review_approve_apply(runner, changeset_id, workspace)
    assert applied.exit_code == 0, applied.output
