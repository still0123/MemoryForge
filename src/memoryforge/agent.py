"""A small Wiki-backed Agent loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypedDict

from memoryforge.provider import OpenAICompatibleProvider
from memoryforge.query import (
    AskPayload,
    CitationPayload,
    EvidencePayload,
    answer_question,
)
from memoryforge.workspace import is_public_source_version, read_source_excerpt


class AgentEvent(TypedDict):
    step: int
    action: str
    result: str


class AgentPayload(TypedDict):
    status: Literal["answered", "unknown", "max_steps"]
    answer: str
    citations: list[CitationPayload]
    evidence: list[EvidencePayload]
    wiki_pages: list[str]
    events: list[AgentEvent]


def run_agent(
    workspace_root: Path,
    question: str,
    *,
    provider: OpenAICompatibleProvider,
    max_steps: int = 4,
    max_pages: int = 3,
) -> AgentPayload:
    """Run a bounded model/tool loop over public Wiki evidence."""
    _validate_limits(max_steps, max_pages)
    messages = _agent_messages(question)
    latest: AskPayload | None = None
    evidence: list[EvidencePayload] = []
    events: list[AgentEvent] = []

    for step_number in range(1, max_steps + 1):
        decision = provider.agent_step(messages)
        tool_result: object
        if decision.action == "final":
            citations = _selected_citations(latest, decision.citation_indexes)
            answer = (decision.answer or "").strip()
            if answer and answer != "不知道" and citations:
                events.append(
                    {
                        "step": step_number,
                        "action": "final",
                        "result": _event_result({"citation_indexes": decision.citation_indexes}),
                    }
                )
                return {
                    "status": "answered",
                    "answer": answer,
                    "citations": citations,
                    "evidence": evidence,
                    "wiki_pages": latest["wiki_pages"] if latest else [],
                    "events": events,
                }
            tool_result = {"error": "final answer needs at least one Wiki citation"}
        elif decision.action == "search_wiki":
            latest = _search_public_wiki(workspace_root, decision.query or question, max_pages)
            evidence = []
            tool_result = _json_tool_result(latest)
        else:
            tool_result, selected = _read_evidence(workspace_root, latest, decision.citation_index)
            if selected is not None:
                evidence.append(selected)

        events.append(
            {
                "step": step_number,
                "action": decision.action,
                "result": _event_result(tool_result),
            }
        )
        messages.extend(
            [
                {"role": "assistant", "content": decision.model_dump_json()},
                {
                    "role": "user",
                    "content": f"Tool result: {json.dumps(tool_result, ensure_ascii=False)}",
                },
            ]
        )

    return {
        "status": "max_steps",
        "answer": "未在限定步数内完成回答",
        "citations": latest["citations"] if latest else [],
        "evidence": evidence,
        "wiki_pages": latest["wiki_pages"] if latest else [],
        "events": events,
    }


def _agent_messages(question: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are MiniClaude, a small evidence-first knowledge agent. "
                "Use one action per turn and return JSON only. Actions are: "
                "search_wiki with query; read_evidence with citation_index; "
                "final with answer and citation_indexes. Start with search_wiki. "
                "Only use facts returned by tools. A final answer must cite at least one "
                "citation index from the latest search. If evidence is insufficient, "
                'return {"action":"final","answer":"不知道","citation_indexes":[]}. '
                "Do not invent tools or file paths."
            ),
        },
        {"role": "user", "content": question},
    ]


def _search_public_wiki(workspace_root: Path, query: str, max_pages: int) -> AskPayload:
    found = answer_question(workspace_root, query, debug=True, max_pages=max_pages)
    public_citations = [
        citation
        for citation in found["citations"]
        if is_public_source_version(
            workspace_root,
            source_id=citation["source_id"],
            source_version=citation["source_version"],
        )
    ]
    if not public_citations:
        return _unknown_search(found)
    first = public_citations[0]
    return {
        **found,
        "status": "answered",
        "answer": first["quote"],
        "citations": public_citations,
        "source_id": first["source_id"],
        "source_version": first["source_version"],
        "locator": first["locator"],
        "quote": first["quote"],
    }


def _unknown_search(found: AskPayload) -> AskPayload:
    return {
        "status": "unknown",
        "answer": "不知道",
        "citations": [],
        "wiki_pages": [],
        "source_id": None,
        "source_version": None,
        "locator": None,
        "quote": None,
        "trace": found.get("trace", []),
    }


def _read_evidence(
    workspace_root: Path,
    latest: AskPayload | None,
    citation_index: int | None,
) -> tuple[dict[str, object], EvidencePayload | None]:
    if latest is None or not latest["citations"]:
        return {"error": "search_wiki must run before read_evidence"}, None
    index = 0 if citation_index is None else citation_index
    if isinstance(index, bool) or not 0 <= index < len(latest["citations"]):
        return {"error": "citation_index is outside the latest search result"}, None
    citation = latest["citations"][index]
    text = read_source_excerpt(
        workspace_root,
        source_id=citation["source_id"],
        source_version=citation["source_version"],
        locator=citation["locator"],
    )
    selected: EvidencePayload = {**citation, "text": text}
    return {"citation_index": index, "evidence": selected}, selected


def _selected_citations(
    latest: AskPayload | None,
    indexes: tuple[int, ...],
) -> list[CitationPayload]:
    if latest is None:
        return []
    citations: list[CitationPayload] = []
    for index in indexes:
        if isinstance(index, bool) or not 0 <= index < len(latest["citations"]):
            continue
        citation = latest["citations"][index]
        if citation not in citations:
            citations.append(citation)
    return citations


def _json_tool_result(result: AskPayload) -> dict[str, object]:
    return {
        "status": result["status"],
        "answer": result["answer"],
        "citations": result["citations"],
        "wiki_pages": result["wiki_pages"],
        "trace": result.get("trace", []),
    }


def _event_result(result: object) -> str:
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= 240 else text[:237] + "..."


def _validate_limits(max_steps: int, max_pages: int) -> None:
    if isinstance(max_steps, bool) or not 1 <= max_steps <= 8:
        raise ValueError("max_steps must be an integer between 1 and 8")
    if isinstance(max_pages, bool) or not 1 <= max_pages <= 10:
        raise ValueError("max_pages must be an integer between 1 and 10")
