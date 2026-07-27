from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from memoryforge.cli import app


def test_ask_answers_from_applied_wiki_with_verifiable_citation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, imported = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "When do cache entries expire?",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "answered"
    assert payload["answer"] == "Cache entries expire after sixty seconds."
    assert payload["source_id"] == imported["source_id"]
    assert payload["snapshot_uri"] == imported["snapshot_uri"]
    assert payload["quote"] == payload["answer"]
    start_text, end_text = payload["locator"].removeprefix("chars:").split("-")
    source_text = "# Cache policy\n\nCache entries expire after sixty seconds.\n"
    assert source_text[int(start_text) : int(end_text)] == payload["quote"]


def test_ask_does_not_use_imported_but_unapplied_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Launch notes\n\nThe launch code is amber.\n",
    )

    result = runner.invoke(
        app,
        ["ask", "What is the launch code?", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "unknown"
    assert payload["answer"] == "不知道"


def test_ask_returns_unknown_when_stable_wiki_has_no_matching_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Deployment\n\nDeployment runs every Friday.\n",
    )
    _apply_pending_source(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "What is the deployment region?",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "unknown"
    assert payload["answer"] == "不知道"


def test_ask_restores_multiline_quote_from_immutable_blob(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_text = "# Release schedule\n\nDeployment runs every\nFriday morning.\n"
    runner, workspace, imported = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        source_text,
    )
    _apply_pending_source(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "When does deployment run every week?",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "answered"
    start_text, end_text = payload["locator"].removeprefix("chars:").split("-")
    blob = (workspace / imported["snapshot_path"]).read_text(encoding="utf-8")
    assert payload["quote"] == "Deployment runs every\nFriday morning."
    assert blob[int(start_text) : int(end_text)] == payload["quote"]
    assert payload["citations"][0]["quote"] == payload["quote"]


def test_ask_rejects_unsupported_as_of_instead_of_using_current_wiki(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "When do cache entries expire?",
            "--as-of",
            "2026-01-01",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 2
    assert "--as-of is not supported" in result.output


def _workspace_with_imported_source(
    tmp_path: Path,
    monkeypatch,
    source_text: str,
) -> tuple[CliRunner, Path, dict[str, Any]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "note.md"
    source.write_text(source_text, encoding="utf-8")
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    initialized = runner.invoke(app, ["init", str(workspace)])
    imported = runner.invoke(
        app,
        ["import", str(source), "--workspace", str(workspace)],
    )

    assert initialized.exit_code == 0
    assert imported.exit_code == 0
    return runner, workspace, json.loads(imported.stdout)


def _apply_pending_source(runner: CliRunner, workspace: Path) -> None:
    ingested = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace)],
    )
    assert ingested.exit_code == 0
    changeset_id = json.loads(ingested.stdout)["changeset_id"]

    applied = runner.invoke(
        app,
        [
            "apply",
            changeset_id,
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert applied.exit_code == 0
