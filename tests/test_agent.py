from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.agent import run_agent
from memoryforge.cli import app
from memoryforge.provider import AgentStep, OpenAICompatibleProvider


class StubAgentProvider(OpenAICompatibleProvider):
    def __init__(self, steps: list[AgentStep]) -> None:
        self.steps = iter(steps)

    def agent_step(self, _messages: object) -> AgentStep:
        return next(self.steps)


def test_agent_searches_reads_evidence_and_returns_citations(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "note.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repository)
    workspace = tmp_path / "workspace"
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    imported = runner.invoke(app, ["import", str(source), "--workspace", str(workspace)])
    assert imported.exit_code == 0
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )
    assert applied.exit_code == 0

    result = run_agent(
        workspace,
        "When do cache entries expire?",
        provider=StubAgentProvider(
            [
                AgentStep(action="search_wiki", query="When do cache entries expire?"),
                AgentStep(action="read_evidence", citation_index=0),
                AgentStep(
                    action="final",
                    answer="Cache entries expire after sixty seconds.",
                    citation_indexes=(0,),
                ),
            ]
        ),
    )

    assert result["status"] == "answered"
    assert result["answer"] == "Cache entries expire after sixty seconds."
    assert len(result["citations"]) == 1
    assert result["evidence"][0]["text"] == "Cache entries expire after sixty seconds."
    assert [event["action"] for event in result["events"]] == [
        "search_wiki",
        "read_evidence",
        "final",
    ]
