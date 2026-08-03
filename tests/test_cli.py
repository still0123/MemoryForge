from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.cli import app


def test_cli_init_import_and_search_local_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "public-note.md"
    source.write_text("# Public note\n\nA searchable local knowledge fixture.", encoding="utf-8")
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init", str(workspace)])
    import_result = runner.invoke(
        app,
        ["import", str(source), "--workspace", str(workspace), "--category", "notes"],
    )
    search_result = runner.invoke(
        app,
        ["search", "searchable", "--workspace", str(workspace)],
    )

    assert init_result.exit_code == 0
    assert import_result.exit_code == 0
    imported = json.loads(import_result.stdout)
    assert imported["status"] == "created"
    assert imported["source_uri"].startswith("mf://source/")
    assert imported["snapshot_uri"].startswith("mf://blob/")
    assert str(tmp_path) not in import_result.stdout
    assert search_result.exit_code == 0
    searched = json.loads(search_result.stdout)[0]
    assert searched["title"] == "Public note"
    assert searched["source_id"] == imported["source_id"]
    assert searched["source_uri"] == imported["source_uri"]
    assert searched["source_path"] == "public-note.md"
    assert searched["snapshot_uri"] == imported["snapshot_uri"]
    assert str(tmp_path) not in search_result.stdout


def test_cli_reports_integrity_failure_without_traceback_or_private_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "public-note.md"
    source.write_text("# Public note\n\nsearchable evidence", encoding="utf-8")
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    imported = runner.invoke(
        app,
        ["import", str(source), "--workspace", str(workspace)],
    )
    snapshot_path = workspace / json.loads(imported.stdout)["snapshot_path"]
    snapshot_path.write_text("tampered", encoding="utf-8")

    result = runner.invoke(app, ["search", "searchable", "--workspace", str(workspace)])
    output = result.stdout + result.stderr

    assert result.exit_code == 1
    assert "integrity" in output.lower()
    assert "Traceback" not in output
    assert str(tmp_path) not in output


def test_cli_help_describes_source_version_category() -> None:
    result = CliRunner().invoke(app, ["import", "--help"])

    assert result.exit_code == 0
    assert "SourceVersion category" in result.stdout


def test_query_commands_expose_repository_scope() -> None:
    runner = CliRunner()

    assert "--repository" in runner.invoke(app, ["search", "--help"]).stdout
    assert "--repository" in runner.invoke(app, ["ask", "--help"]).stdout
    assert "--repository" in runner.invoke(app, ["agent", "--help"]).stdout


def test_cli_registers_lists_and_syncs_existing_git_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# Service\n\nCLI fixture", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "Add documentation")
    workspace = tmp_path / "workspace"
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    add_result = runner.invoke(
        app,
        ["git-add", str(checkout), "--workspace", str(workspace)],
    )
    repository = json.loads(add_result.stdout)
    list_result = runner.invoke(app, ["git-list", "--workspace", str(workspace)])
    sync_result = runner.invoke(
        app,
        ["git-sync", repository["repository_id"], "--workspace", str(workspace)],
    )
    unknown_result = runner.invoke(
        app,
        ["git-sync", "0" * 64, "--workspace", str(workspace)],
    )

    assert add_result.exit_code == 0
    assert repository["sensitivity"] == "local_only"
    assert list_result.exit_code == 0
    assert [item["repository_id"] for item in json.loads(list_result.stdout)] == [
        repository["repository_id"]
    ]
    assert sync_result.exit_code == 0
    assert json.loads(sync_result.stdout)["created"] == 1
    assert unknown_result.exit_code == 1
    assert "unknown Git repository" in unknown_result.stderr
    assert "Traceback" not in unknown_result.stderr


def test_cli_refreshes_all_registered_git_checkouts(tmp_path: Path) -> None:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# Service\n\nRefresh fixture", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "Add documentation")
    workspace = tmp_path / "workspace"
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    registered = runner.invoke(
        app,
        ["git-add", str(checkout), "--workspace", str(workspace)],
    )
    assert registered.exit_code == 0, registered.output

    refreshed = runner.invoke(app, ["refresh", "--workspace", str(workspace)])
    unchanged = runner.invoke(app, ["refresh", "--workspace", str(workspace)])

    assert refreshed.exit_code == 0, refreshed.output
    assert json.loads(refreshed.stdout)["status"] == "changed"
    assert json.loads(refreshed.stdout)["git"][0]["created"] == 1
    assert "documents" not in json.loads(refreshed.stdout)["git"][0]
    assert json.loads(refreshed.stdout)["feishu"] == []
    assert unchanged.exit_code == 0, unchanged.output
    assert json.loads(unchanged.stdout)["status"] == "unchanged"


def _git(checkout: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=checkout, check=True, capture_output=True, text=True)
