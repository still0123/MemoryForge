from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import memoryforge.agent as agent_module
import memoryforge.agent_evaluation as agent_evaluation
from memoryforge.agent_evaluation import run_agent_evaluation
from memoryforge.provider import AgentStep, OpenAICompatibleProvider
from memoryforge.workspace import init_workspace


class StubAgentProvider(OpenAICompatibleProvider):
    def __init__(self, steps: list[AgentStep]) -> None:
        self.steps = iter(steps)

    def agent_step(self, _messages: object) -> AgentStep:
        return next(self.steps)


def test_agent_eval_aggregates_answered_and_unknown(tmp_path: Path, monkeypatch) -> None:
    workspace = init_workspace(tmp_path / "workspace")
    _patch_source_maps(monkeypatch)
    _patch_agent_search(monkeypatch)
    evidence_text = "Cache entries expire after sixty seconds."
    config = _write_suite(
        tmp_path / "agent-suite.json",
        [
            {
                "id": "answered",
                "category": "single_hop",
                "question": "When do cache entries expire?",
                "expected_status": "answered",
                "expected_source_paths": ["note.md"],
                "required_terms": ["sixty", "seconds"],
            },
            {
                "id": "unknown",
                "category": "unanswerable",
                "question": "How should shards rebalance?",
                "expected_status": "unknown",
            },
        ],
    )
    provider = StubAgentProvider(
        [
            AgentStep(action="search_wiki", query="When do cache entries expire?"),
            AgentStep(action="read_evidence", citation_index=0),
            AgentStep(
                action="final",
                answer=evidence_text,
                citation_indexes=(0,),
            ),
            AgentStep(action="final", answer="不知道"),
        ]
    )

    payload = run_agent_evaluation(workspace, config, provider)

    assert payload["case_count"] == 2
    assert payload["cases"][0]["correct"] is True
    assert payload["cases"][1]["correct"] is True
    assert payload["cases"][0]["cited_source_paths"] == ["note.md"]
    agent = payload["agent"]
    assert agent["answer_accuracy"] == 100.0
    assert agent["source_recall"] == 100.0
    assert agent["abstention_accuracy"] == 100.0
    assert agent["max_steps_rate"] == 0.0
    assert agent["provider_error_rate"] == 0.0
    assert agent["average_provider_calls"] == 2.0
    assert agent["average_provider_latency_ms"] >= 0.0
    assert agent["average_evidence_characters"] == len(evidence_text) / 2
    assert agent["average_tool_result_characters"] > 0
    assert agent["evidence_reuse_count"] == 0
    assert agent["tool_result_truncations"] == 0
    assert agent["final_retry_reason_counts"] == {
        "empty_answer": 0,
        "missing_citations": 0,
        "invalid_citation_indexes": 0,
        "unread_citations": 0,
        "unsupported_answer": 0,
    }


def test_agent_eval_leaves_workspace_tree_unchanged(tmp_path: Path, monkeypatch) -> None:
    workspace = init_workspace(tmp_path / "workspace")
    config = _write_suite(
        tmp_path / "agent-suite.json",
        [
            {
                "id": "unknown",
                "category": "unanswerable",
                "question": "How should shards rebalance?",
                "expected_status": "unknown",
            }
        ],
    )
    before = _tree_contents(workspace)

    run_agent_evaluation(
        workspace,
        config,
        StubAgentProvider([AgentStep(action="final", answer="不知道")]),
    )

    assert _tree_contents(workspace) == before


def test_agent_eval_aggregates_returned_agent_metrics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path / "workspace")
    config = _write_suite(
        tmp_path / "agent-metrics.json",
        [
            {
                "id": "error",
                "category": "unanswerable",
                "question": "error",
                "expected_status": "unknown",
            },
            {
                "id": "max",
                "category": "unanswerable",
                "question": "max",
                "expected_status": "unknown",
            },
            {
                "id": "answered",
                "category": "single_hop",
                "question": "answered",
                "expected_status": "answered",
                "expected_source_paths": ["note.md"],
                "required_terms": ["cache"],
            },
        ],
    )
    monkeypatch.setattr(agent_evaluation, "run_agent", _FakeAgentProvider().run)

    payload = run_agent_evaluation(
        workspace,
        config,
        StubAgentProvider([]),
    )

    agent = payload["agent"]
    assert agent["max_steps_rate"] == 33.3
    assert agent["provider_error_rate"] == 33.3
    assert agent["average_provider_calls"] == 2.67
    assert agent["average_provider_latency_ms"] == 13.33
    assert agent["average_evidence_characters"] == 33.33
    assert agent["average_tool_result_characters"] == 66.67
    assert agent["evidence_reuse_count"] == 2
    assert agent["tool_result_truncations"] == 3
    assert agent["final_retry_reason_counts"] == {
        "empty_answer": 1,
        "missing_citations": 1,
        "invalid_citation_indexes": 0,
        "unread_citations": 0,
        "unsupported_answer": 0,
    }


def test_agent_eval_passes_single_repository_id_and_global_multi_repository(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path / "workspace")
    first = "a" * 64
    second = "b" * 64
    config = _write_suite(
        tmp_path / "agent-repositories.json",
        [
            {
                "id": "single",
                "category": "single_hop",
                "question": "single",
                "expected_status": "answered",
                "expected_source_paths": ["README.md"],
                "required_terms": ["x"],
                "repository_id": first,
            },
            {
                "id": "multi",
                "category": "cross_repository",
                "question": "multi",
                "expected_status": "answered",
                "expected_source_paths": ["a/README.md", "b/README.md"],
                "required_terms": ["x"],
                "repository_ids": [first, second],
            },
        ],
    )
    captured: list[str | None] = []

    def fake_run(
        _root: Path,
        _question: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        captured.append(_kwargs["repository_id"])  # type: ignore[arg-type]
        return _result("unknown", 1, {})

    monkeypatch.setattr(agent_evaluation, "run_agent", fake_run)

    run_agent_evaluation(workspace, config, StubAgentProvider([]))

    assert captured == [first, None]


class _FakeAgentProvider:
    def run(
        self,
        _workspace: Path,
        question: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        if question == "error":
            return _result("provider_error", 1, {})
        if question == "max":
            return _result("max_steps", 4, {"empty_answer": 1})
        return _result(
            "answered",
            3,
            {"missing_citations": 1},
            evidence_reuse_count=2,
            tool_result_truncations=3,
            evidence_characters=100,
            tool_result_characters=200,
            latency_ms=40,
        )


def _result(
    status: str,
    provider_calls: int,
    final_reasons: dict[str, int],
    *,
    evidence_reuse_count: int = 0,
    tool_result_truncations: int = 0,
    evidence_characters: int = 0,
    tool_result_characters: int = 0,
    latency_ms: float = 0.0,
) -> dict[str, object]:
    return {
        "status": status,
        "answer": "x",
        "citations": [],
        "wiki_pages_read": 0,
        "evidence_characters": evidence_characters,
        "tool_result_characters": tool_result_characters,
        "metrics": {
            "hit_max_steps": status == "max_steps",
            "final_retry_reasons": {
                "empty_answer": final_reasons.get("empty_answer", 0),
                "missing_citations": final_reasons.get("missing_citations", 0),
                "invalid_citation_indexes": final_reasons.get("invalid_citation_indexes", 0),
                "unread_citations": final_reasons.get("unread_citations", 0),
                "unsupported_answer": final_reasons.get("unsupported_answer", 0),
            },
            "evidence_reuse_count": evidence_reuse_count,
            "tool_result_truncations": tool_result_truncations,
            "provider_calls": provider_calls,
            "provider_latency_ms": latency_ms,
        },
    }


def _write_suite(path: Path, cases: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({"name": "agent", "cases": cases}),
        encoding="utf-8",
    )
    return path


def _patch_source_maps(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_evaluation,
        "_load_source_maps",
        lambda _root: ({"abc": "note.md"}, {}),
    )


def _patch_agent_search(monkeypatch) -> None:
    citation = {
        "source_id": "abc",
        "source_version": 1,
        "locator": "chars:0-47",
        "quote": "Cache entries expire after sixty seconds.",
    }
    evidence = {**citation, "text": citation["quote"]}
    monkeypatch.setattr(
        agent_module.Workspace,
        "open_readonly",
        classmethod(lambda _cls, _root: SimpleNamespace(prompt_context=lambda: "")),
    )
    monkeypatch.setattr(
        agent_module,
        "_search_wiki",
        lambda *_args, **_kwargs: {
            "status": "answered",
            "answer": citation["quote"],
            "citations": [citation],
            "wiki_pages": ["wiki/pages/cache.md"],
            "source_id": "abc",
            "source_version": 1,
            "locator": "chars:0-47",
            "quote": citation["quote"],
        },
    )
    monkeypatch.setattr(
        agent_module,
        "_read_evidence",
        lambda *_args, **_kwargs: (
            {"citation_index": 0, "evidence": evidence},
            evidence,
            False,
        ),
    )
    monkeypatch.setattr(
        agent_module,
        "_final_answer_is_supported",
        lambda *_args, **_kwargs: True,
    )


def _tree_contents(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }
