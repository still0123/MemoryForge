from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.cli import app


def test_eval_compares_wiki_answers_with_raw_fts(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "note.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    workspace = repository / "workspace"
    config = repository / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "cache",
                "cases": [
                    {
                        "id": "cache-expiry",
                        "question": "Cache entries expire",
                        "expected_source_path": "note.md",
                        "required_terms": ["sixty", "seconds"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repository)
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert runner.invoke(app, ["import", str(source), "--workspace", str(workspace)]).exit_code == 0
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    assert runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    ).exit_code == 0

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"] == {
        "answer_accuracy": 100.0,
        "citation_accuracy": 100.0,
        "average_wiki_pages_read": 1.0,
        "average_raw_sources_read": 0.0,
        "average_citation_audit_characters": 41.0,
    }
    assert payload["raw_fts_baseline"]["expected_source_recall_at_3"] == 100.0
