"""Small, bounded conversation state stored inside one local workspace."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from memoryforge.importer import import_local_document
from memoryforge.models import ImportResult, LocalDocument, Sensitivity, SourceCategory
from memoryforge.workspace import is_public_source_version

_MAX_TURNS = 3
_MAX_MEMORY_MESSAGES = 100
_MAX_CONTEXT_CHARS = 3_600
_MAX_QUESTION_CHARS = 600
_MAX_ANSWER_CHARS = 1_200
_MAX_QUOTE_CHARS = 320
_MAX_REWRITE_CHARS = 1_600
_SESSION_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_REPOSITORY_ID = re.compile(r"^[a-f0-9]{64}$")
_DEFINITION_QUESTION = re.compile(
    r"^(?P<subject>.{2,40}?)(?:是什么意思|是什么|是做什么的|做什么|怎么做|如何实现)[？?]?$"
)
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
    section_path: NotRequired[str]


class SessionTurn(TypedDict):
    question: str
    answer: str
    citations: list[SessionCitation]
    wiki_pages: list[str]
    model_safe: bool


ConversationMessage = tuple[Literal["user", "assistant"], str]


def is_valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID.fullmatch(session_id))


class SessionStore:
    """Persist bounded turns and two small context pointers under ``.memoryforge``."""

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
        for path in (
            self.path,
            self.directory / f"{self.session_id}.project",
            self.directory / f"{self.session_id}.context",
            self.directory / f"{self.session_id}.auto-memory",
            self.directory / f"{self.session_id}.memory.json",
        ):
            if path.is_file():
                path.unlink()

    def project_id(self) -> str | None:
        value = _read_pointer(self.directory / f"{self.session_id}.project")
        return value if value is not None and _REPOSITORY_ID.fullmatch(value) else None

    def set_project(self, repository_id: str | None) -> None:
        if repository_id is not None and _REPOSITORY_ID.fullmatch(repository_id) is None:
            raise ValueError("repository ID is invalid")
        _write_pointer(self.directory / f"{self.session_id}.project", repository_id)

    def context_session_id(self) -> str | None:
        value = _read_pointer(self.directory / f"{self.session_id}.context")
        return value if value is not None and is_valid_session_id(value) else None

    def set_context_session(self, session_id: str | None) -> None:
        if session_id is not None and not is_valid_session_id(session_id):
            raise ValueError("session ID is invalid")
        _write_pointer(self.directory / f"{self.session_id}.context", session_id)

    def auto_memory_enabled(self) -> bool:
        return _read_pointer(self.directory / f"{self.session_id}.auto-memory") == "on"

    def set_auto_memory(self, enabled: bool) -> None:
        _write_pointer(
            self.directory / f"{self.session_id}.auto-memory",
            "on" if enabled else None,
        )


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


def _read_pointer(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def _write_pointer(path: Path, value: str | None) -> None:
    if value is None:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)


def rewrite_query(question: str, turns: list[SessionTurn]) -> str:
    """Add the latest bounded turn only when the new question looks referential."""
    question = question.strip()
    if not turns:
        return question
    latest = turns[-1]
    if not _looks_like_followup(question) and not _mentions_previous_concept(question, latest):
        return question
    parts = [question, latest["question"]]
    for citation in latest["citations"]:
        parts.extend((citation.get("section_path", ""), citation["quote"]))
    if not latest["citations"]:
        parts.append(latest["answer"])
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


def remember_session(workspace_root: Path, session_id: str) -> ImportResult | None:
    """Store the bounded Feishu session as a local-only, reviewable source draft."""
    turns = SessionStore(workspace_root, session_id).load(allow_local=True)
    messages: list[ConversationMessage] = []
    for turn in turns:
        messages.extend((("user", turn["question"]), ("assistant", turn["answer"])))
    messages = _merge_session_memory(workspace_root, session_id, messages)
    return remember_conversation(
        workspace_root,
        platform="feishu",
        conversation_id=session_id,
        messages=messages,
    )


def _merge_session_memory(
    workspace_root: Path,
    session_id: str,
    recent: list[ConversationMessage],
) -> list[ConversationMessage]:
    path = workspace_root / ".memoryforge" / "sessions" / f"{session_id}.memory.json"
    existing: list[ConversationMessage] = []
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for message in payload.get("messages", []):
                role, content = message
                if role not in {"user", "assistant"} or not isinstance(content, str):
                    raise ValueError
                existing.append((role, content))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("stored conversation memory is invalid") from exc
    overlap = 0
    for size in range(min(len(existing), len(recent)), 0, -1):
        if existing[-size:] == recent[:size]:
            overlap = size
            break
    # ponytail: 100 messages keeps one small local file; use a transcript DB when
    # longer retained conversations become a measured need.
    merged = (existing + recent[overlap:])[-_MAX_MEMORY_MESSAGES:]
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps({"messages": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return merged


def remember_conversation(
    workspace_root: Path,
    *,
    platform: Literal["feishu", "botmux", "codex"],
    conversation_id: str,
    messages: list[ConversationMessage],
    extra_tags: tuple[str, ...] = (),
    title_override: str | None = None,
) -> ImportResult | None:
    """Store normalized conversation messages as one local-only source."""
    if not messages:
        return None
    source_id = sha256(f"conversation:{platform}:{conversation_id}".encode()).hexdigest()
    first_user = next((content for role, content in messages if role == "user"), "")
    topic = " ".join(first_user.split()).strip("# ")
    if len(topic) > 72:
        topic = topic[:71].rstrip() + "…"
    supplied_title = " ".join((title_override or "").split()).strip("# ")[:120]
    title = supplied_title or (
        f"{platform.title()} conversation: {topic}" if topic else f"{platform.title()} conversation"
    )
    return import_local_document(
        workspace_root,
        LocalDocument(
            source_uri=f"mf://source/{source_id}",
            source_path=f"conversations/{platform}/{source_id[:16]}.md",
            media_type="text/markdown",
            category=SourceCategory.NOTES,
            suffix=".md",
            title=title,
            content=_render_memory_source(title, messages),
            sensitivity=Sensitivity.LOCAL_ONLY,
            tags=("conversation", f"platform:{platform}", "unverified", *extra_tags),
        ),
        source_id=source_id,
    )


def _render_memory_source(title: str, messages: list[ConversationMessage]) -> str:
    lines = [
        f"# {title}",
        "",
        "> Conversation draft. Assistant responses are unverified until review and apply.",
    ]
    for role, content in messages:
        lines.extend(
            (
                "",
                "## User" if role == "user" else "## Assistant (unverified)",
                "",
                content,
            )
        )
    return "\n".join(lines) + "\n"


def _looks_like_followup(question: str) -> bool:
    lowered = question.lower()
    return any(marker in lowered for marker in _FOLLOWUP_MARKERS) or bool(
        re.match(r"^[a-z0-9_.@/-]+\s*(?:模块|文件夹)", lowered)
    )


def _mentions_previous_concept(question: str, latest: SessionTurn) -> bool:
    """Resolve a definition follow-up only when its subject came from the last turn."""
    match = _DEFINITION_QUESTION.fullmatch(question.strip())
    if match is None:
        return False
    subject = match.group("subject").strip()
    context = " ".join(
        (
            latest["answer"],
            *(citation.get("section_path", "") for citation in latest["citations"]),
            *(citation["quote"] for citation in latest["citations"]),
        )
    )
    return subject in context


def _session_citations(citations: list[dict[str, Any]]) -> list[SessionCitation]:
    result: list[SessionCitation] = []
    for citation in citations[:6]:
        stored: SessionCitation = {
            "source_id": str(citation["source_id"]),
            "source_version": int(citation["source_version"]),
            "locator": str(citation["locator"]),
            "quote": str(citation["quote"])[:_MAX_QUOTE_CHARS],
        }
        if citation.get("section_path"):
            stored["section_path"] = str(citation["section_path"])[:240]
        result.append(stored)
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
