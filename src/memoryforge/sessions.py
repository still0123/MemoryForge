"""Small, bounded conversation state stored inside one local workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypedDict

from memoryforge.workspace import is_public_source_version

_MAX_TURNS = 3
_MAX_CONTEXT_CHARS = 3_600
_MAX_QUESTION_CHARS = 600
_MAX_ANSWER_CHARS = 1_200
_MAX_QUOTE_CHARS = 320
_MAX_REWRITE_CHARS = 1_600
_SESSION_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_FOLLOWUP_MARKERS = (
    "它",
    "这个",
    "这",
    "那",
    "上述",
    "前面",
    "该方案",
    "what about",
    "it",
    "that",
    "this",
)


class SessionCitation(TypedDict):
    source_id: str
    source_version: int
    locator: str
    quote: str


class SessionTurn(TypedDict):
    question: str
    answer: str
    citations: list[SessionCitation]
    wiki_pages: list[str]
    model_safe: bool


def is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID.fullmatch(session_id))


class SessionStore:
    """Persist at most three bounded turns under ``.memoryforge``."""

    def __init__(self, workspace_root: Path, session_id: str) -> None:
        if not is_valid_session_id(session_id):
            raise ValueError("session must contain only letters, numbers, _, ., :, @, or -")
        self.workspace_root = workspace_root
        self.session_id = session_id
        self.directory = workspace_root / ".memoryforge" / "sessions"
        self.path = self.directory / f"{session_id}.json"

    def load(self, *, allow_local: bool) -> list[SessionTurn]:
        if not self.path.is_file():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        raw_turns = data.get("turns", []) if isinstance(data, dict) else []
        turns = [_coerce_turn(item) for item in raw_turns]
        visible = [turn for turn in turns if turn is not None]
        if not allow_local:
            visible = [turn for turn in visible if turn["model_safe"]]
        return visible[-_MAX_TURNS:]

    def append(
        self,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
        *,
        wiki_pages: list[str] | tuple[str, ...] = (),
        model_safe: bool,
    ) -> None:
        turns = self.load(allow_local=True)
        turn: SessionTurn = {
            "question": question[:_MAX_QUESTION_CHARS],
            "answer": answer[:_MAX_ANSWER_CHARS],
            "citations": _session_citations(citations),
            "wiki_pages": [str(path)[:240] for path in wiki_pages[:3]],
            "model_safe": model_safe,
        }
        turns.append(turn)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path.write_text(
            json.dumps({"turns": turns[-_MAX_TURNS:]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.path.chmod(0o600)

    def clear(self) -> None:
        if self.path.is_file():
            self.path.unlink()


def render_context(turns: list[SessionTurn]) -> str:
    if not turns:
        return ""
    lines = [
        "Recent conversation is only for resolving the current follow-up. "
        "Search the Wiki again; do not cite this history directly."
    ]
    for turn in turns:
        lines.extend(
            [
                f"User: {turn['question']}",
                f"Assistant: {turn['answer']}",
            ]
        )
        for citation in turn["citations"]:
            lines.append(f"Earlier evidence: {citation['quote']}")
    return "\n".join(lines)[:_MAX_CONTEXT_CHARS]


def rewrite_query(question: str, turns: list[SessionTurn]) -> str:
    """Add the latest bounded turn only when the new question looks referential."""
    question = question.strip()
    if not turns or not _looks_like_followup(question):
        return question
    latest = turns[-1]
    parts = [latest["question"], question, latest["answer"]]
    parts.extend(citation["quote"] for citation in latest["citations"])
    return " ".join(part for part in parts if part)[:_MAX_REWRITE_CHARS]


def save_turn(
    workspace_root: Path,
    session_id: str | None,
    *,
    question: str,
    answer: str,
    citations: list[dict[str, Any]],
    wiki_pages: list[str] | tuple[str, ...],
) -> None:
    if session_id is None:
        return
    model_safe = bool(citations) and all(
        is_public_source_version(
            workspace_root,
            source_id=str(citation["source_id"]),
            source_version=int(citation["source_version"]),
        )
        for citation in citations
    )
    SessionStore(workspace_root, session_id).append(
        question,
        answer,
        citations,
        wiki_pages=wiki_pages,
        model_safe=model_safe,
    )


def _looks_like_followup(question: str) -> bool:
    lowered = question.lower()
    return any(marker in lowered for marker in _FOLLOWUP_MARKERS) or bool(
        re.match(r"^[a-z0-9_.@/-]+\s*(?:模块|文件夹)", lowered)
    )


def _session_citations(citations: list[dict[str, Any]]) -> list[SessionCitation]:
    result: list[SessionCitation] = []
    for citation in citations[:6]:
        result.append(
            {
                "source_id": str(citation["source_id"]),
                "source_version": int(citation["source_version"]),
                "locator": str(citation["locator"]),
                "quote": str(citation["quote"])[:_MAX_QUOTE_CHARS],
            }
        )
    return result


def _coerce_turn(value: object) -> SessionTurn | None:
    if not isinstance(value, dict):
        return None
    if not all(isinstance(value.get(key), str) for key in ("question", "answer")):
        return None
    citations = (
        _session_citations(value.get("citations", []))
        if isinstance(value.get("citations"), list)
        else []
    )
    pages = value.get("wiki_pages", [])
    return {
        "question": str(value["question"])[:_MAX_QUESTION_CHARS],
        "answer": str(value["answer"])[:_MAX_ANSWER_CHARS],
        "citations": citations,
        "wiki_pages": [str(page)[:240] for page in pages[:3]] if isinstance(pages, list) else [],
        "model_safe": bool(value.get("model_safe", False)),
    }
