"""Import the useful text from one local Codex rollout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from memoryforge.errors import MemoryForgeError
from memoryforge.importer import MAX_SOURCE_BYTES
from memoryforge.models import ImportResult
from memoryforge.sessions import ConversationMessage, is_valid_session_id, remember_conversation

_MAX_MESSAGES = 100
_MAX_CONTENT_CHARS = 40_000
_IGNORED_USER_PREFIXES = (
    "<recommended_plugins>",
    "# AGENTS.md instructions",
    "<environment_context>",
    "<permissions instructions>",
    "<collaboration_mode>",
    "<apps_instructions>",
    "<plugins_instructions>",
    "<skills_instructions>",
    "<turn_aborted>",
    "<image ",
    "</image>",
    "The user has the in-app browser open.",
)


class CodexImportError(MemoryForgeError):
    """Raised when a Codex rollout cannot become conversation memory."""


def import_codex_rollout(
    workspace: Path, path: Path, *, title: str | None = None
) -> ImportResult:
    """Import user and assistant text, excluding system and tool records."""
    path = path.expanduser()
    if path.is_symlink() or not path.is_file():
        raise CodexImportError("Codex rollout must be a regular, non-symlink file")

    session_id = ""
    messages: list[ConversationMessage] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if len(line.encode("utf-8")) > MAX_SOURCE_BYTES:
                # Tool output can be much larger than a useful chat message. It is
                # excluded from memory, so skip the whole record without parsing it.
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexImportError(f"Codex rollout line {line_number} is invalid JSON") from exc
            if not isinstance(record, dict):
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record.get("type") == "session_meta" and not session_id:
                candidate = payload.get("id") or payload.get("session_id")
                if isinstance(candidate, str):
                    session_id = candidate
                continue
            message = _conversation_message(record, payload)
            if message is not None:
                _append_message(messages, message)

    if not is_valid_session_id(session_id):
        raise CodexImportError("Codex rollout is missing a valid session identity")
    if not messages:
        raise CodexImportError("Codex rollout contains no user or assistant text")
    result = remember_conversation(
        workspace,
        platform="codex",
        conversation_id=session_id,
        messages=messages[-_MAX_MESSAGES:],
        extra_tags=(f"thread:{session_id}",),
        title_override=title,
    )
    if result is None:  # Defensive: messages is non-empty above.
        raise CodexImportError("Codex rollout contains no importable conversation")
    return result


def _conversation_message(
    record: dict[str, Any], payload: dict[str, Any]
) -> ConversationMessage | None:
    if record.get("type") != "response_item" or payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role not in {"user", "assistant"}:
        return None
    expected_type = "input_text" if role == "user" else "output_text"
    parts: list[str] = []
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if not isinstance(item, dict) or item.get("type") != expected_type:
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        text = text.strip()
        if role == "user" and text.startswith(_IGNORED_USER_PREFIXES):
            continue
        if role == "user":
            text = _strip_codex_ui_metadata(text)
        if text:
            parts.append(text)
    if not parts:
        return None
    normalized_role: Literal["user", "assistant"] = role
    return normalized_role, "\n\n".join(parts)[:_MAX_CONTENT_CHARS]


def _strip_codex_ui_metadata(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            stripped in {"# Files mentioned by the user:", "## My request:"}
            or stripped.startswith(("<image ", "</image>", "<turn_aborted>"))
            or (
                "codex-clipboard-" in stripped
                and ("/var/folders/" in stripped or "/private/var/" in stripped)
            )
        ):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _append_message(
    messages: list[ConversationMessage], message: ConversationMessage
) -> None:
    role, content = message
    if messages and messages[-1][0] == role:
        previous = messages[-1][1]
        messages[-1] = (role, f"{previous}\n\n{content}"[:_MAX_CONTENT_CHARS])
    else:
        messages.append(message)
    if len(messages) > _MAX_MESSAGES:
        del messages[: len(messages) - _MAX_MESSAGES]
