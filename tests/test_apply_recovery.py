from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.apply_journal import ApplyJournalStore
from memoryforge.changesets import ChangeSetStore
from memoryforge.cli import app
from memoryforge.linting import lint_workspace
from memoryforge.workspace import Workspace


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
