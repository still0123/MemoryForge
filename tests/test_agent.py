from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.agent import run_agent
from memoryforge.changesets import ChangeSetStore
from memoryforge.cli import app
from memoryforge.models import PageChange
from memoryforge.provider import AgentStep, OpenAICompatibleProvider
from memoryforge.workspace import Workspace


class StubAgentProvider(OpenAICompatibleProvider):
    def __init__(self, steps: list[AgentStep]) -> None:
        self.steps = iter(steps)

    def agent_step(self, _messages: object) -> AgentStep:
        return next(self.steps)


class CapturingAgentProvider(StubAgentProvider):
    def __init__(self, steps: list[AgentStep]) -> None:
        super().__init__(steps)
        self.messages: list[object] = []

    def agent_step(self, messages: object) -> AgentStep:
        self.messages.append(messages)
        return super().agent_step(messages)


class UpdateAgentProvider(StubAgentProvider):
    def __init__(self, steps: list[AgentStep], change: PageChange | None) -> None:
        super().__init__(steps)
        self.change = change
        self.update_messages: object | None = None

    def propose_update(self, messages: object) -> PageChange | None:
        self.update_messages = messages
        return self.change


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


def test_agent_returns_unknown_without_citation(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)

    result = run_agent(
        workspace,
        "What is the database schema?",
        provider=StubAgentProvider([AgentStep(action="final", answer="不知道")]),
    )

    assert result["status"] == "unknown"
    assert result["citations"] == []
    assert [event["action"] for event in result["events"]] == ["final"]


def test_agent_includes_workspace_contract_in_prompt(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)
    (workspace / "AGENTS.md").write_text("Answer using the glossary.\n", encoding="utf-8")
    provider = CapturingAgentProvider([AgentStep(action="final", answer="不知道")])

    run_agent(workspace, "What is the database schema?", provider=provider)

    assert provider.messages
    assert "Answer using the glossary." in json.dumps(provider.messages[0], ensure_ascii=False)


def test_agent_rejects_final_without_reading_its_citation(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)

    result = run_agent(
        workspace,
        "When do cache entries expire?",
        provider=StubAgentProvider(
            [
                AgentStep(action="search_wiki", query="When do cache entries expire?"),
                AgentStep(
                    action="final",
                    answer="Cache entries expire after sixty seconds.",
                    citation_indexes=(0,),
                ),
            ]
        ),
        max_steps=2,
    )

    assert result["status"] == "max_steps"
    assert result["events"][-1]["action"] == "final"
    assert "read_evidence" in result["events"][-1]["result"]


def test_agent_can_use_local_evidence_when_explicitly_trusted(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch, local_only=True)

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
        allow_local=True,
    )

    assert result["status"] == "answered"
    assert result["evidence"][0]["text"] == "Cache entries expire after sixty seconds."


def test_agent_proposes_update_as_reviewable_changeset(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)
    page = next((workspace / "wiki/pages").glob("*.md"))
    page_content = page.read_text(encoding="utf-8")
    source_id = json.loads(
        next(line for line in page_content.splitlines() if line.startswith("sources:"))
        .split(":", 1)[1]
    )[0]
    source_text = "# Cache policy\n\nCache entries expire after sixty seconds.\n"
    start = source_text.index("Cache entries")
    end = start + len("Cache entries expire after sixty seconds.")
    change = PageChange(
        path=page.relative_to(workspace).as_posix(),
        title="Cache policy",
        page_type="concept",
        summary="Cache entries expire after sixty seconds.",
        body="The cache policy is worth retaining for future work.",
        source_ids=(source_id,),
        citations=({"source_id": source_id, "locator": f"chars:{start}-{end}"},),
    )
    provider = UpdateAgentProvider(
        [
            AgentStep(action="search_wiki", query="When do cache entries expire?"),
            AgentStep(action="read_evidence", citation_index=0),
            AgentStep(
                action="final",
                answer="Cache entries expire after sixty seconds.",
                citation_indexes=(0,),
            ),
        ],
        change,
    )

    result = run_agent(
        workspace,
        "When do cache entries expire?",
        provider=provider,
        propose_update=True,
    )

    changeset_id = result["changeset_id"]
    assert result["status"] == "answered"
    assert changeset_id is not None
    stored = ChangeSetStore(Workspace.open(workspace)).get(changeset_id)
    assert stored.changeset.status.value == "PROPOSED"
    assert stored.changeset.operations[0].details["origin"] == "agent"
    assert page.read_text(encoding="utf-8") == page_content
    assert provider.update_messages is not None
    assert "Cache entries expire after sixty seconds." in json.dumps(
        provider.update_messages,
        ensure_ascii=False,
    )


def test_agent_does_not_create_update_when_provider_declines(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)
    provider = UpdateAgentProvider(
        [
            AgentStep(action="search_wiki", query="When do cache entries expire?"),
            AgentStep(action="read_evidence", citation_index=0),
            AgentStep(
                action="final",
                answer="Cache entries expire after sixty seconds.",
                citation_indexes=(0,),
            ),
        ],
        None,
    )

    result = run_agent(
        workspace,
        "When do cache entries expire?",
        provider=provider,
        propose_update=True,
    )

    assert result["status"] == "answered"
    assert result["changeset_id"] is None
    assert not list((workspace / ".memoryforge/staging/proposed").glob("chg_*"))


def test_cli_agent_help_exposes_update_proposal_flag() -> None:
    result = CliRunner().invoke(app, ["agent", "--help"])

    assert result.exit_code == 0
    assert "--propose-update" in result.stdout


def _applied_public_workspace(tmp_path: Path, monkeypatch, *, local_only: bool = False) -> Path:
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
    import_args = ["import", str(source), "--workspace", str(workspace)]
    if local_only:
        import_args.append("--local-only")
    assert runner.invoke(app, import_args).exit_code == 0
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    assert runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    ).exit_code == 0
    return workspace
