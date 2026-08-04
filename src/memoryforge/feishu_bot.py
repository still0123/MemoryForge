"""Small pure functions an internal Feishu bot endpoint can call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memoryforge.agent import AgentPayload, run_agent
from memoryforge.errors import MemoryForgeError
from memoryforge.provider import OpenAICompatibleProvider
from memoryforge.query import AskPayload, answer_question
from memoryforge.sessions import SessionStore, is_valid_session_id, rewrite_query, save_turn


class FeishuBotError(MemoryForgeError):
    """Raised when an incoming Feishu event has no usable text message."""


def reply_to_feishu_text(
    workspace: Path,
    text: str,
    *,
    max_pages: int = 3,
    provider: OpenAICompatibleProvider | None = None,
    allow_local: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Answer one Feishu text message, optionally summarizing explicitly allowed evidence."""
    result: AskPayload | AgentPayload
    if provider is None:
        result = _deterministic_session_answer(
            workspace,
            text,
            max_pages=max_pages,
            allow_local=allow_local,
            session_id=session_id,
        )
    else:
        result = run_agent(
            workspace,
            text,
            provider=provider,
            max_pages=min(max_pages, 3),
            allow_local=allow_local,
            session_id=session_id,
        )
        if result["status"] == "provider_error":
            result = _deterministic_session_answer(
                workspace,
                text,
                max_pages=max_pages,
                allow_local=allow_local,
                session_id=session_id,
            )
    return _text_reply(workspace, result)


def _deterministic_session_answer(
    workspace: Path,
    text: str,
    *,
    max_pages: int,
    allow_local: bool,
    session_id: str | None,
) -> AskPayload:
    turns = SessionStore(workspace, session_id).load(allow_local=allow_local) if session_id else []
    result = answer_question(
        workspace,
        rewrite_query(text, turns),
        max_pages=max_pages,
        allow_local=allow_local,
    )
    save_turn(
        workspace,
        session_id,
        question=text,
        answer=result["answer"],
        citations=[dict(citation) for citation in result["citations"]],
        wiki_pages=result["wiki_pages"],
    )
    return result


def handle_feishu_event(
    workspace: Path,
    payload: dict[str, Any],
    *,
    max_pages: int = 3,
    provider: OpenAICompatibleProvider | None = None,
    allow_local: bool = False,
) -> dict[str, Any]:
    """Turn a Feishu URL-verification or text-message event into a reply payload.

    The hosting service owns request verification and calls Feishu's send-message API.
    Keeping this layer pure makes the Wiki behavior testable without app credentials.
    """
    challenge = payload.get("challenge")
    if isinstance(challenge, str):
        return {"challenge": challenge}
    try:
        event = payload["event"]
        message = event["message"]
        if message.get("message_type") != "text":
            raise ValueError
        content = json.loads(message["content"])
        text = content["text"].strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FeishuBotError("Feishu event must contain one non-empty text message") from exc
    if not text:
        raise FeishuBotError("Feishu event must contain one non-empty text message")
    session_id = _event_session_id(event, message)
    return {
        "message_id": message.get("message_id"),
        "session_id": session_id,
        "reply": reply_to_feishu_text(
            workspace,
            text,
            max_pages=max_pages,
            provider=provider,
            allow_local=allow_local,
            session_id=session_id,
        ),
    }


def handle_lark_cli_event(
    workspace: Path,
    event: dict[str, Any],
    *,
    max_pages: int = 3,
    provider: OpenAICompatibleProvider | None = None,
    allow_local: bool = False,
) -> dict[str, Any] | None:
    """Turn one flattened ``lark-cli event consume`` record into a reply payload."""
    if event.get("sender_type") != "user" or event.get("message_type") != "text":
        return None
    message_id = event.get("message_id")
    text = event.get("content")
    if (
        not isinstance(message_id, str)
        or not message_id
        or not isinstance(text, str)
        or not text.strip()
    ):
        raise FeishuBotError("lark-cli event must contain one non-empty text message")
    session_id = _event_session_id(event)
    return {
        "message_id": message_id,
        "session_id": session_id,
        "reply": reply_to_feishu_text(
            workspace,
            text.strip(),
            max_pages=max_pages,
            provider=provider,
            allow_local=allow_local,
            session_id=session_id,
        ),
    }


def _text_reply(workspace: Path, result: AskPayload | AgentPayload) -> dict[str, Any]:
    text = result["answer"]
    titles = [_page_title(workspace / path) for path in result["wiki_pages"]]
    if titles := [title for title in titles if title]:
        text += "\n\n来源：" + "、".join(dict.fromkeys(titles))
    return {"msg_type": "text", "content": {"text": text}}


def _event_session_id(event: dict[str, Any], message: dict[str, Any] | None = None) -> str | None:
    candidates: list[object] = []
    if message is not None:
        candidates.append(message.get("chat_id"))
    candidates.extend((event.get("chat_id"), event.get("open_id")))
    sender = event.get("sender")
    if isinstance(sender, dict):
        sender_id = sender.get("sender_id")
        if isinstance(sender_id, dict):
            candidates.append(sender_id.get("open_id"))
    for candidate in candidates:
        if isinstance(candidate, str) and is_valid_session_id(candidate):
            return candidate
    return None


def _page_title(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("title: "):
                title = json.loads(line.removeprefix("title: "))
                return title if isinstance(title, str) else None
    except (OSError, json.JSONDecodeError):
        return None
    return None
