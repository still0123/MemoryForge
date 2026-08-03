from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import memoryforge.evaluation as evaluation_module
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
                        "category": "single_hop",
                        "question": "Cache entries expire",
                        "expected_status": "answered",
                        "expected_source_paths": ["note.md"],
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
        "source_recall_at_3": 100.0,
        "citation_grounding_accuracy": 100.0,
        "multi_source_coverage": 0.0,
        "abstention_accuracy": 0.0,
        "average_wiki_pages_read": 1.0,
        "average_raw_sources_read": 0.0,
        "average_evidence_characters": 41.0,
    }
    assert payload["raw_fts_baseline"]["expected_source_recall_at_3"] == 100.0


def test_eval_citation_accuracy_drops_when_a_case_routes_to_the_wrong_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {
            "cache.md": "# Cache policy\n\nCache entries expire after sixty seconds.\n",
            "deploy.md": "# Deployment\n\nDeployment runs every Friday.\n",
        },
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "mixed",
                "cases": [
                    {
                        "id": "cache-expiry",
                        "category": "single_hop",
                        "question": "When do cache entries expire?",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md"],
                        "required_terms": ["sixty", "seconds"],
                    },
                    {
                        "id": "deploy-day-wrong-source",
                        "category": "single_hop",
                        "question": "When does deployment run every week?",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md"],
                        "required_terms": ["Friday"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"]["answer_accuracy"] == 100.0
    assert payload["memoryforge"]["citation_grounding_accuracy"] == 100.0
    assert payload["memoryforge"]["source_recall_at_3"] == 50.0


def test_eval_uses_daily_debug_query_and_separates_raw_audit_reads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {"cache.md": "# Cache policy\n\nCache entries expire after sixty seconds.\n"},
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "cache",
                "cases": [
                    {
                        "id": "cache-expiry",
                        "category": "single_hop",
                        "question": "When do cache entries expire?",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md"],
                        "required_terms": ["sixty", "seconds"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    debug_flags: list[bool] = []
    real_answer = evaluation_module.answer_question

    def spy_answer_question(workspace_root: Path, question: str, **kwargs: object):
        debug_flags.append(bool(kwargs.get("debug", False)))
        return real_answer(workspace_root, question, **kwargs)

    monkeypatch.setattr(evaluation_module, "answer_question", spy_answer_question)

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert debug_flags == [True]
    assert payload["memoryforge"]["average_raw_sources_read"] == 0.0
    assert payload["memoryforge"]["average_evidence_characters"] > 0.0


def test_eval_cites_all_expected_sources_for_multi_source_case(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {
            "cache.md": "# Cache\n\nCache expires after sixty seconds.\n",
            "deploy.md": "# Deploy\n\nDeployment runs every Friday.\n",
        },
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "multi-source",
                "cases": [
                    {
                        "id": "cache-and-deploy",
                        "category": "multi_source",
                        "question": "Cache expires sixty seconds and deployment Friday",
                        "expected_status": "answered",
                        "expected_source_paths": ["cache.md", "deploy.md"],
                        "required_terms": ["sixty", "Friday"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"]["multi_source_coverage"] == 100.0
    assert payload["cases"][0]["memoryforge"]["cited_source_paths"] == [
        "cache.md",
        "deploy.md",
    ]


def test_eval_accepts_and_scores_unanswerable_case(tmp_path: Path, monkeypatch) -> None:
    runner, workspace = _build_applied_workspace(
        tmp_path,
        monkeypatch,
        {"cache.md": "# Cache\n\nCache expires after sixty seconds.\n"},
    )
    config = tmp_path / "suite.json"
    config.write_text(
        json.dumps(
            {
                "name": "abstention",
                "cases": [
                    {
                        "id": "unknown-database-sharding",
                        "category": "unanswerable",
                        "question": "How should database shards rebalance?",
                        "expected_status": "unknown",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", str(config), "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["memoryforge"]["abstention_accuracy"] == 100.0
    assert payload["cases"][0]["memoryforge"]["citation_grounded"] is True


def _build_applied_workspace(
    tmp_path: Path,
    monkeypatch,
    sources: dict[str, str],
) -> tuple[CliRunner, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    for name, text in sources.items():
        source = repository / name
        source.write_text(text, encoding="utf-8")
        imported = runner.invoke(app, ["import", str(source), "--workspace", str(workspace)])
        assert imported.exit_code == 0, imported.output
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0, applied.output
    return runner, workspace
