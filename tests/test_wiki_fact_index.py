from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.errors import WorkspaceError
from memoryforge.linting import lint_workspace
from memoryforge.models import Sensitivity
from memoryforge.version_store import GitVersionStore
from memoryforge.workspace import (
    Workspace,
    init_workspace,
    register_git_checkout,
    search_wiki_facts,
    sync_git_checkout,
)


def test_apply_replaces_and_restores_searchable_wiki_facts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "cache.md"
    source.write_text(
        "# Cache\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    _import_and_apply(runner, workspace, source)

    first = search_wiki_facts(workspace, "sixty seconds")
    assert len(first) == 1
    assert first[0].quote == "Cache entries expire after sixty seconds."
    assert first[0].repository_id is None
    assert first[0].locator.startswith("chars:")
    assert _fact_counts(workspace) == (1, 1)
    assert lint_workspace(workspace)["status"] == "clean"

    source.write_text(
        "# Cache\n\nCache entries expire after ninety seconds.\n",
        encoding="utf-8",
    )
    _import_and_apply(runner, workspace, source)

    assert search_wiki_facts(workspace, "sixty") == ()
    updated = search_wiki_facts(workspace, "ninety seconds")
    assert len(updated) == 1
    assert updated[0].source_version > first[0].source_version
    assert _fact_counts(workspace) == (1, 1)

    opened = Workspace.open(workspace)
    previous = opened.replace_applied_page_facts({updated[0].page_path: ()})
    assert search_wiki_facts(workspace, "ninety seconds") == ()
    opened.restore_applied_page_facts(previous)
    assert len(search_wiki_facts(workspace, "ninety seconds")) == 1
    assert lint_workspace(workspace)["status"] == "clean"

    def fail_commit(self, paths, message):
        raise WorkspaceError("simulated fact apply failure")

    source.write_text(
        "# Cache\n\nCache entries expire after one hundred seconds.\n",
        encoding="utf-8",
    )
    imported = runner.invoke(app, ["import", str(source), "--workspace", str(workspace)])
    assert imported.exit_code == 0, imported.output
    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    monkeypatch.setattr(GitVersionStore, "commit_paths", fail_commit)
    failed = runner.invoke(
        app,
        [
            "apply",
            json.loads(staged.stdout)["changeset_id"],
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )

    assert failed.exit_code != 0
    assert search_wiki_facts(workspace, "hundred") == ()
    assert len(search_wiki_facts(workspace, "ninety seconds")) == 1
    assert _fact_counts(workspace) == (1, 1)
    assert not any(
        issue["code"].startswith("fact_index") for issue in lint_workspace(workspace)["issues"]
    )


def test_fact_search_enforces_repository_and_page_scopes(tmp_path: Path) -> None:
    first_checkout = _create_repository(tmp_path / "first", "blue")
    second_checkout = _create_repository(tmp_path / "second", "red")
    workspace = init_workspace(tmp_path / "workspace")
    first = register_git_checkout(
        workspace,
        first_checkout,
        sensitivity=Sensitivity.PUBLIC,
    )
    second = register_git_checkout(
        workspace,
        second_checkout,
        sensitivity=Sensitivity.PUBLIC,
    )
    sync_git_checkout(workspace, first.repository_id)
    sync_git_checkout(workspace, second.repository_id)
    runner = CliRunner()
    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    applied = runner.invoke(
        app,
        [
            "apply",
            json.loads(staged.stdout)["changeset_id"],
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert applied.exit_code == 0, applied.output

    first_results = search_wiki_facts(
        workspace,
        "scheduler",
        repository_id=first.repository_id,
    )
    second_results = search_wiki_facts(
        workspace,
        "scheduler",
        repository_id=second.repository_id,
    )

    assert {result.repository_id for result in first_results} == {first.repository_id}
    assert {result.repository_id for result in second_results} == {second.repository_id}
    assert all("blue" in result.quote for result in first_results)
    assert all("red" in result.quote for result in second_results)
    assert (
        search_wiki_facts(
            workspace,
            "scheduler",
            page_paths=[first_results[0].page_path],
        )
        == first_results
    )


def test_workspace_migration_backfills_facts_from_applied_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "policy.md"
    source.write_text("# Policy\n\nRetries stop after three attempts.\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    _import_and_apply(runner, workspace, source)

    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        connection.execute("DROP TABLE wiki_fact_fts")
        connection.execute("DROP TABLE wiki_facts")

    Workspace.open(workspace)

    results = search_wiki_facts(workspace, "three attempts")
    assert len(results) == 1
    assert results[0].quote == "Retries stop after three attempts."
    assert _fact_counts(workspace) == (1, 1)


def test_code_wiki_fact_index_round_trips_symbol_and_relation_metadata(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "facts@example.com")
    _git(checkout, "config", "user.name", "facts")
    (checkout / "src").mkdir()
    (checkout / "src" / "service.py").write_text(
        "from src.helper import target\n\n\ndef run() -> str:\n    return target()\n",
        encoding="utf-8",
    )
    (checkout / "src" / "helper.py").write_text(
        'def target() -> str:\n    return "done"\n',
        encoding="utf-8",
    )
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "add service")
    _git(checkout, "remote", "add", "origin", "https://example.com/facts/service.git")
    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(
        workspace,
        checkout,
        sensitivity=Sensitivity.PUBLIC,
    )
    sync_git_checkout(workspace, repository.repository_id)
    runner = CliRunner()
    selected = runner.invoke(
        app,
        [
            "code-add",
            repository.repository_id,
            "src",
            "--workspace",
            str(workspace),
        ],
    )
    assert selected.exit_code == 0, selected.output
    sync_git_checkout(workspace, repository.repository_id)
    staged = runner.invoke(
        app,
        ["ingest", "--code-wiki", repository.repository_id, "--workspace", str(workspace)],
    )
    assert staged.exit_code == 0, staged.output
    applied = runner.invoke(
        app,
        [
            "apply",
            json.loads(staged.stdout)["changeset_id"],
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert applied.exit_code == 0, applied.output

    symbols = search_wiki_facts(
        workspace,
        "service run",
        repository_id=repository.repository_id,
    )
    relations = search_wiki_facts(
        workspace,
        "run target calls",
        repository_id=repository.repository_id,
    )

    assert any(result.symbol == "src.service.run" for result in symbols)
    assert any(result.relation_type == "calls" for result in relations)
    assert all(result.source_version > 0 for result in (*symbols, *relations))


def _import_and_apply(runner: CliRunner, workspace: Path, source: Path) -> None:
    imported = runner.invoke(app, ["import", str(source), "--workspace", str(workspace)])
    assert imported.exit_code == 0, imported.output
    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    applied = runner.invoke(
        app,
        [
            "apply",
            json.loads(staged.stdout)["changeset_id"],
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert applied.exit_code == 0, applied.output


def _create_repository(path: Path, color: str) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", f"{color}@example.com")
    _git(path, "config", "user.name", color)
    (path / "README.md").write_text(
        f"# Scheduler\n\nThe {color} scheduler owns this repository.\n",
        encoding="utf-8",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", f"add {color} scheduler")
    _git(path, "remote", "add", "origin", f"https://example.com/{color}/service.git")
    return path


def _fact_counts(workspace: Path) -> tuple[int, int]:
    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        facts = int(connection.execute("SELECT COUNT(*) FROM wiki_facts").fetchone()[0])
        fts = int(connection.execute("SELECT COUNT(*) FROM wiki_fact_fts").fetchone()[0])
    return facts, fts


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
