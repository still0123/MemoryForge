from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.cli import app


def test_history_and_rollback_restore_wiki_and_query_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    workspace = tmp_path / "wiki"
    source = tmp_path / "policy.md"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    first_commit, page = _import_and_apply(
        runner,
        workspace,
        source,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    second_commit, _ = _import_and_apply(
        runner,
        workspace,
        source,
        "# Cache policy\n\nCache entries expire after ninety seconds.\n",
    )

    history = runner.invoke(
        app,
        ["history", "--page", page, "--workspace", str(workspace)],
    )
    rollback = runner.invoke(
        app,
        ["rollback", first_commit, "--workspace", str(workspace)],
    )
    answer = runner.invoke(
        app,
        ["ask", "When do cache entries expire?", "--workspace", str(workspace)],
    )

    assert history.exit_code == 0, history.output
    assert [item["commit"] for item in json.loads(history.stdout)][:2] == [
        second_commit,
        first_commit,
    ]
    assert rollback.exit_code == 0, rollback.output
    rollback_payload = json.loads(rollback.stdout)
    assert rollback_payload["status"] == "ROLLED_BACK"
    assert rollback_payload["previous_commit"] == second_commit
    assert rollback_payload["target_commit"] == first_commit
    assert rollback_payload["commit"] not in {first_commit, second_commit}
    assert answer.exit_code == 0, answer.output
    answer_payload = json.loads(answer.stdout)
    assert answer_payload["status"] == "answered"
    assert "sixty seconds" in answer_payload["answer"]
    assert "ninety seconds" not in answer_payload["answer"]
    assert answer_payload["source_version"] == 1


def _import_and_apply(
    runner: CliRunner,
    workspace: Path,
    source: Path,
    content: str,
) -> tuple[str, str]:
    source.write_text(content, encoding="utf-8")
    imported = runner.invoke(
        app,
        ["import", str(source), "--category", "design", "--workspace", str(workspace)],
    )
    assert imported.exit_code == 0, imported.output
    proposal = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert proposal.exit_code == 0, proposal.output
    applied = runner.invoke(
        app,
        [
            "apply",
            json.loads(proposal.stdout)["changeset_id"],
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert applied.exit_code == 0, applied.output
    payload = json.loads(applied.stdout)
    page = next(path for path in payload["files"] if path.startswith("wiki/pages/"))
    return payload["commit"], page
