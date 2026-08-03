from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.agent as agent_module
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
    assert [event["call_id"] for event in result["events"]] == [
        "call-1",
        "call-2",
        "call-3",
    ]
    assert result["wiki_pages_read"] == 1
    assert result["evidence_characters"] == len(result["evidence"][0]["text"])
    assert result["tool_result_characters"] > 0


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


def test_agent_reports_provider_error_as_terminal_status(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)

    class FailingProvider(StubAgentProvider):
        def agent_step(self, _messages: object) -> AgentStep:
            raise ValueError("provider request failed")

    result = run_agent(workspace, "What is the database schema?", provider=FailingProvider([]))

    assert result["status"] == "provider_error"
    assert result["answer"] == "模型请求失败"
    assert result["events"][0]["action"] == "provider_error"
    assert "provider request failed" in result["events"][0]["result"]


def test_agent_returns_unknown_tool_observation(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)
    unknown = AgentStep.model_construct(action="summarize", query=None)

    result = run_agent(
        workspace,
        "What is the database schema?",
        provider=StubAgentProvider([unknown]),
        max_steps=1,
    )

    assert result["status"] == "max_steps"
    assert "unknown action: summarize" in result["events"][0]["result"]


def test_agent_rejects_read_evidence_without_citation_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)

    result = run_agent(
        workspace,
        "When do cache entries expire?",
        provider=StubAgentProvider(
            [
                AgentStep(action="search_wiki", query="When do cache entries expire?"),
                AgentStep(action="read_evidence"),
            ]
        ),
        max_steps=2,
    )

    assert result["status"] == "max_steps"
    assert "citation_index is required" in result["events"][-1]["result"]


def test_agent_reads_multiple_citations_within_budget(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)
    citations = [
        {
            "source_id": "a" * 64,
            "source_version": 1,
            "locator": "chars:0-8",
            "quote": "first fact",
        },
        {
            "source_id": "b" * 64,
            "source_version": 1,
            "locator": "chars:0-9",
            "quote": "second fact",
        },
    ]
    captured: dict[str, object] = {}

    def fake_answer_question(*_args: object, **kwargs: object) -> dict[str, object]:
        max_citations = int(kwargs.get("max_citations", 1))
        captured.update(kwargs)
        selected = citations[:max_citations]
        return {
            "status": "answered",
            "answer": " ".join(item["quote"] for item in selected),
            "citations": selected,
            "wiki_pages": ["wiki/pages/first.md", "wiki/pages/second.md"][:max_citations],
            "source_id": selected[0]["source_id"],
            "source_version": 1,
            "locator": selected[0]["locator"],
            "quote": selected[0]["quote"],
        }

    monkeypatch.setattr(agent_module, "answer_question", fake_answer_question)
    monkeypatch.setattr(agent_module, "is_public_source_version", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        agent_module,
        "read_source_excerpt",
        lambda *_args, **kwargs: "evidence-" + str(kwargs["locator"]),
    )

    result = run_agent(
        workspace,
        "What are the two facts?",
        provider=StubAgentProvider(
            [
                AgentStep(action="search_wiki", query="What are the two facts?"),
                AgentStep(action="read_evidence", citation_index=0),
                AgentStep(action="read_evidence", citation_index=1),
                AgentStep(
                    action="final",
                    answer="first fact and second fact",
                    citation_indexes=(0, 1),
                ),
            ]
        ),
    )

    assert result["status"] == "answered"
    assert len(result["citations"]) == 2
    assert len(result["evidence"]) == 2
    assert captured["max_citations"] == 6


def test_agent_enforces_three_page_limit(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="max_pages"):
        run_agent(
            workspace,
            "What is the database schema?",
            provider=StubAgentProvider([]),
            max_pages=4,
        )


def test_agent_truncates_long_evidence_excerpt(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_module, "read_source_excerpt", lambda *_args, **_kwargs: "x" * 3000)

    result = run_agent(
        workspace,
        "When do cache entries expire?",
        provider=StubAgentProvider(
            [
                AgentStep(action="search_wiki", query="When do cache entries expire?"),
                AgentStep(action="read_evidence", citation_index=0),
            ]
        ),
        max_steps=2,
    )

    assert result["evidence"][0]["text"] == "x" * 2000
    assert result["evidence_characters"] == 2000


def test_agent_bounds_tool_result_context(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_public_workspace(tmp_path, monkeypatch)
    citations = [
        {
            "source_id": f"{index:x}" * 64,
            "source_version": 1,
            "locator": "chars:0-2000",
            "quote": "x" * 2000,
        }
        for index in range(1, 7)
    ]
    monkeypatch.setattr(
        agent_module,
        "answer_question",
        lambda *_args, **_kwargs: {
            "status": "answered",
            "answer": "x",
            "citations": citations,
            "wiki_pages": [f"wiki/pages/{index}.md" for index in range(6)],
            "source_id": citations[0]["source_id"],
            "source_version": 1,
            "locator": citations[0]["locator"],
            "quote": citations[0]["quote"],
        },
    )
    monkeypatch.setattr(agent_module, "is_public_source_version", lambda *_args, **_kwargs: True)
    provider = CapturingAgentProvider(
        [
            AgentStep(action="search_wiki", query="long evidence"),
            AgentStep(action="final", answer="不知道"),
        ]
    )

    result = run_agent(workspace, "long evidence", provider=provider, max_steps=2)

    assert result["status"] == "unknown"
    assert result["tool_result_characters"] <= 8000
    assert "truncated" in result["events"][0]["result"]


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
