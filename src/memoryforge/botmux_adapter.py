"""Import Botmux conversation hooks as local-only memory drafts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, TypedDict

from memoryforge.errors import MemoryForgeError
from memoryforge.platform_lock import exclusive_file_lock
from memoryforge.sessions import remember_conversation
from memoryforge.workspace import Workspace

_MESSAGE_EVENTS = frozenset({"topic.new", "thread.reply", "outbound.send", "outbound.reply"})
_SUPPORTED_EVENTS = _MESSAGE_EVENTS | {"session.exit"}
_MAX_MESSAGES = 100
_MAX_CONTENT_CHARS = 50_000


class BotmuxHookError(MemoryForgeError):
    """Raised when one Botmux hook payload cannot become conversation memory."""


class BotmuxHookResult(TypedDict):
    event: str
    status: Literal["ignored", "recorded", "created", "updated", "unchanged"]


class _StoredMessage(TypedDict):
    id: str
    role: Literal["user", "assistant"]
    content: str


def handle_botmux_hook(workspace: Path, payload: object) -> BotmuxHookResult:
    """Record one Botmux hook and refresh memory after assistant output or exit."""
    if not isinstance(payload, dict):
        raise BotmuxHookError("Botmux hook payload must be a JSON object")
    event = payload.get("event")
    if not isinstance(event, str) or event not in _SUPPORTED_EVENTS:
        raise BotmuxHookError("Botmux hook event is unsupported")
    identity = _conversation_identity(payload)
    store = _BotmuxConversationStore(Workspace.open(workspace), identity)

    if event in _MESSAGE_EVENTS:
        if payload.get("msgType") != "text":
            return {"event": event, "status": "ignored"}
        if payload.get("contentTruncated") is True:
            raise BotmuxHookError("Botmux hook content is truncated; enable fullContentEvents")
        content = payload.get("content")
        message_id = payload.get("replyId") or payload.get("messageId")
        if not isinstance(content, str) or not content.strip() or not isinstance(message_id, str):
            raise BotmuxHookError("Botmux text hook is missing content or message identity")
        role: Literal["user", "assistant"] = (
            "assistant" if event.startswith("outbound.") else "user"
        )
        messages = store.append(message_id, role, content.strip())
        if role == "user":
            return {"event": event, "status": "recorded"}
    else:
        messages = store.load()

    result = remember_conversation(
        store.workspace.root,
        platform="botmux",
        conversation_id=identity,
        messages=[(message["role"], message["content"]) for message in messages],
    )
    return {
        "event": event,
        "status": result.status if result is not None else "ignored",
    }


def _conversation_identity(payload: dict[str, Any]) -> str:
    app_id = payload.get("larkAppId")
    anchor = payload.get("anchor")
    if not isinstance(anchor, str) or not anchor:
        if payload.get("scope") == "chat":
            anchor = payload.get("chatId")
        else:
            anchor = payload.get("rootMessageId") or payload.get("sessionId")
    if not isinstance(app_id, str) or not app_id or not isinstance(anchor, str) or not anchor:
        raise BotmuxHookError("Botmux hook is missing conversation identity")
    return hashlib.sha256(f"{app_id}:{anchor}".encode()).hexdigest()


class _BotmuxConversationStore:
    def __init__(self, workspace: Workspace, identity: str) -> None:
        self.workspace = workspace
        self.directory = workspace.internal_dir / "botmux-hooks"
        self.path = self.directory / f"{identity}.json"
        self.lock_path = self.directory / f"{identity}.lock"

    def append(
        self,
        message_id: str,
        role: Literal["user", "assistant"],
        content: str,
    ) -> list[_StoredMessage]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        with exclusive_file_lock(self.lock_path):
            messages = self._load_unlocked()
            if not any(message["id"] == message_id for message in messages):
                messages.append(
                    {
                        "id": message_id,
                        "role": role,
                        "content": content[:_MAX_CONTENT_CHARS],
                    }
                )
                # ponytail: bounded JSON suffices; use a transcript DB after 100-message
                # sessions become a measured need.
                messages = messages[-_MAX_MESSAGES:]
                self.path.write_text(
                    json.dumps({"messages": messages}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.path.chmod(0o600)
            return messages

    def load(self) -> list[_StoredMessage]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        with exclusive_file_lock(self.lock_path):
            return self._load_unlocked()

    def _load_unlocked(self) -> list[_StoredMessage]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            messages = payload["messages"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BotmuxHookError("stored Botmux conversation is invalid") from exc
        if not isinstance(messages, list):
            raise BotmuxHookError("stored Botmux conversation is invalid")
        normalized: list[_StoredMessage] = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or not isinstance(message.get("id"), str)
                or message.get("role") not in {"user", "assistant"}
                or not isinstance(message.get("content"), str)
            ):
                raise BotmuxHookError("stored Botmux conversation is invalid")
            normalized.append(
                {
                    "id": message["id"],
                    "role": message["role"],
                    "content": message["content"],
                }
            )
        return normalized
