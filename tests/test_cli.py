from __future__ import annotations

import json
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
