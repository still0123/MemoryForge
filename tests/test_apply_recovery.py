from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.compiler.lifecycle as lifecycle_module
import memoryforge.storage.workspace as workspace_module
from memoryforge.compiler.lifecycle import apply_changeset
from memoryforge.compiler.linting import lint_workspace
from memoryforge.compiler.wiki_facts import parse_page_facts
from memoryforge.core.errors import WorkspaceError
from memoryforge.interface.cli import app
from memoryforge.storage.apply_journal import ApplyJournalStore
from memoryforge.storage.changesets import ChangeSetStore
from memoryforge.storage.workspace import Workspace, candidate_page_sources


def test_apply_journal_supports_large_changesets(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    paths = tuple(
        f"wiki/pages/code/repository/module-{index:04d}/page.md" for index in range(2_000)
    )
    store = ApplyJournalStore(workspace)

    journal = store.prepare("chg_large", workspace.current_commit(), paths)

    assert store.path.stat().st_size > 64 * 1024
    assert store.load() == journal


def test_prepared_apply_recovers_worktree_and_projection(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    opened = Workspace.open(workspace)
    stored = ChangeSetStore(opened).get(changeset_id)
    paths = tuple(sorted(stored.candidate_files))
    ApplyJournalStore(opened).prepare(changeset_id, stored.changeset.base_commit, paths)
    for path, content in stored.candidate_files.items():
        destination = workspace / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    recovered = Workspace.open(workspace)

    assert recovered.current_commit() == stored.changeset.base_commit
    assert all(not (workspace / path).exists() for path in paths if path != "wiki/INDEX.md")
    assert ApplyJournalStore(recovered).load() is None
    assert ChangeSetStore(recovered).get(changeset_id).changeset == stored.changeset
    assert recovered.page_paths_for_source(stored.changeset.source_ids[0]) == ()


def test_committed_apply_recovers_projection_and_archive(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    assert (
        runner.invoke(
            app,
            ["review", changeset_id, "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            ["approve", changeset_id, "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    opened = Workspace.open(workspace)
    stored = ChangeSetStore(opened).get(changeset_id)
    paths = tuple(sorted(stored.candidate_files))
    journal_store = ApplyJournalStore(opened)
    journal = journal_store.prepare(changeset_id, stored.changeset.base_commit, paths)
    for path, content in stored.candidate_files.items():
        destination = workspace / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    commit = opened.version_store.commit_paths(paths, f"knowledge: apply {changeset_id}")
    journal_store.mark_committed(journal, commit)

    recovered = Workspace.open(workspace)

    assert recovered.current_commit() == commit
    assert ApplyJournalStore(recovered).load() is None
    archived = workspace / ".memoryforge/staging/applied" / changeset_id
    assert json.loads((archived / "receipt.json").read_text(encoding="utf-8"))["commit"] == commit
    assert recovered.page_paths_for_source(stored.changeset.source_ids[0])
    assert lint_workspace(workspace)["status"] == "clean"


def test_candidate_lint_failure_does_not_mutate_stable_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    assert (
        runner.invoke(app, ["review", changeset_id, "--workspace", str(workspace)]).exit_code == 0
    )
    assert (
        runner.invoke(app, ["approve", changeset_id, "--workspace", str(workspace)]).exit_code == 0
    )
    before = Workspace.open_readonly(workspace).current_commit()
    monkeypatch.setattr(
        lifecycle_module,
        "lint_workspace",
        lambda *_args, **_kwargs: {"status": "issues", "checked_pages": 0, "issues": []},
    )

    with pytest.raises(WorkspaceError, match="stable Workspace was not changed"):
        apply_changeset(workspace, changeset_id)

    assert Workspace.open_readonly(workspace).current_commit() == before
    assert not list((workspace / "wiki" / "pages").glob("*.md"))


def test_committed_apply_reuses_matching_projection(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, changeset_id = _staged_changeset(tmp_path, monkeypatch)
    assert (
        runner.invoke(
            app,
            ["review", changeset_id, "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            ["approve", changeset_id, "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    opened = Workspace.open(workspace)
    stored = ChangeSetStore(opened).get(changeset_id)
    paths = tuple(sorted(stored.candidate_files))
    journal_store = ApplyJournalStore(opened)
    journal = journal_store.prepare(changeset_id, stored.changeset.base_commit, paths)
    opened.record_applied_source_versions(stored.changeset.source_versions)
    opened.replace_applied_page_sources(candidate_page_sources(stored.candidate_files))
    opened.replace_applied_page_facts(
        {
            path: parse_page_facts(path, content)
            for path, content in stored.candidate_files.items()
            if path.startswith("wiki/pages/")
        }
    )
    for path, content in stored.candidate_files.items():
        destination = workspace / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    commit = opened.version_store.commit_paths(paths, f"knowledge: apply {changeset_id}")
    journal_store.mark_committed(journal, commit)

    def fail_rebuild(_workspace: Workspace) -> None:
        raise AssertionError("matching projection must not be rebuilt")

    monkeypatch.setattr(workspace_module, "rebuild_applied_projection", fail_rebuild)
    recovered = Workspace.open(workspace)

    assert recovered.current_commit() == commit
    assert ApplyJournalStore(recovered).load() is None
    assert (workspace / ".memoryforge/staging/applied" / changeset_id).is_dir()


def _staged_changeset(
    tmp_path: Path,
    monkeypatch,
) -> tuple[CliRunner, Path, str]:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    source = tmp_path / "cache.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["import", str(source), "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    ingested = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace)],
    )
    assert ingested.exit_code == 0, ingested.output
    return runner, workspace, json.loads(ingested.stdout)["changeset_id"]
