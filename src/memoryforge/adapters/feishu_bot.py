"""Small pure functions an internal Feishu bot endpoint can call."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

from memoryforge.adapters.importer import SourceValidationError
from memoryforge.core.errors import MemoryForgeError
from memoryforge.query.provider import OpenAICompatibleProvider
from memoryforge.query.query import AskPayload, answer_question
from memoryforge.query.sessions import (
    SessionStore,
    SessionTurn,
    is_valid_session_id,
    remember_session,
    render_context,
    rewrite_query,
    save_turn,
)
from memoryforge.storage.workspace import find_code_module_repositories, list_git_checkouts

_MODULE_SCOPE_QUESTION = re.compile(
    r"(?<![A-Za-z0-9_./-])(?P<path>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)\s*(?:文件夹|模块)"
)


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
    control_reply = _control_reply(workspace, text, session_id)
    if control_reply is not None:
        return control_reply
    result = _session_answer(
        workspace,
        text,
        max_pages=max_pages,
        provider=provider,
        allow_local=allow_local,
        session_id=session_id,
    )
    return _text_reply(workspace, result)


def _session_answer(
    workspace: Path,
    text: str,
    *,
    max_pages: int,
    provider: OpenAICompatibleProvider | None,
    allow_local: bool,
    session_id: str | None,
) -> AskPayload:
    current_store = SessionStore(workspace, session_id) if session_id else None
    context_store = current_store
    repository_id = current_store.project_id() if current_store else None
    turns: list[SessionTurn] = []
    if current_store is not None:
        resumed_id = current_store.context_session_id()
        if resumed_id is not None and resumed_id != session_id:
            context_store = SessionStore(workspace, resumed_id)
            repository_id = repository_id or context_store.project_id()
            turns.extend(context_store.load(allow_local=allow_local))
        turns.extend(current_store.load(allow_local=allow_local))
        turns = [turn for turn in turns if turn["model_safe"] or turn["citations"]]
        turns = turns[-3:]
    result = _module_scope_answer(workspace, text, repository_id)
    if result is None:
        result = answer_question(
            workspace,
            rewrite_query(text, turns),
            max_pages=max_pages,
            provider=provider,
            allow_local=allow_local,
            repository_id=repository_id,
            conversation_context=render_context(turns),
        )
    save_turn(
        workspace,
        session_id,
        question=text,
        answer=result["answer"],
        citations=[dict(citation) for citation in result["citations"]],
        wiki_pages=result["wiki_pages"],
    )
    if current_store is not None and current_store.auto_memory_enabled():
        with suppress(MemoryForgeError, SourceValidationError, OSError):
            remember_session(workspace, current_store.session_id)
    return result


def _module_scope_answer(
    workspace: Path,
    text: str,
    repository_id: str | None,
) -> AskPayload | None:
    if repository_id is not None or (match := _MODULE_SCOPE_QUESTION.search(text)) is None:
        return None
    module_path = match.group("path")
    repositories = find_code_module_repositories(workspace, module_path)
    if len(repositories) < 2:
        return None
    choices = "、".join(f"/project {repository.name}" for repository in repositories)
    return {
        "status": "unknown",
        "answer": f"多个项目都包含 `{module_path}` 模块。请先选择：{choices}",
        "citations": [],
        "wiki_pages": [],
        "source_id": None,
        "source_version": None,
        "locator": None,
        "quote": None,
    }


def _control_reply(
    workspace: Path,
    text: str,
    session_id: str | None,
) -> dict[str, Any] | None:
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0].lower() not in {"/project", "/resume", "/wiki"}:
        return None
    if session_id is None:
        return _plain_reply("当前消息没有可用的会话 ID，无法保存会话状态。")

    store = SessionStore(workspace, session_id)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) == 2 else ""
    if command == "/wiki":
        normalized = argument.lower()
        if normalized in {"auto on", "自动开启"}:
            store.set_auto_memory(True)
            try:
                remember_session(workspace, session_id)
            except (MemoryForgeError, SourceValidationError, OSError):
                return _plain_reply("自动收录已开启；当前历史含疑似密钥或无法保存，暂未生成草稿。")
            return _plain_reply("自动收录已开启。后续回复会更新本地记忆草稿。")
        if normalized in {"auto off", "自动关闭"}:
            store.set_auto_memory(False)
            return _plain_reply("自动收录已关闭；已有本地草稿保留。")
        if normalized in {"status", "状态"}:
            status = "开启" if store.auto_memory_enabled() else "关闭"
            return _plain_reply(f"自动收录：{status}。")
        if normalized not in {"收录", "remember", "save"}:
            return _plain_reply("用法：/wiki 收录 | /wiki auto on | /wiki auto off")
        try:
            result = remember_session(workspace, session_id)
        except (MemoryForgeError, SourceValidationError, OSError):
            return _plain_reply("当前对话含疑似密钥或无法保存，未生成记忆草稿。")
        if result is None:
            return _plain_reply("当前会话还没有可收录内容。")
        return _plain_reply(
            "已生成本地记忆草稿。请在终端执行 ingest、review、approve、apply 后写入 Wiki。"
        )
    if command == "/project":
        if argument.lower() in {"clear", "none", "off"}:
            store.set_project(None)
            return _plain_reply("已清除当前项目范围，后续问题会检索全部 Wiki。")
        repositories = list_git_checkouts(workspace)
        if not argument:
            if not repositories:
                return _plain_reply("还没有注册代码仓库。请先执行 git-add 和 code-add。")
            names = "、".join(repository.name for repository in repositories)
            return _plain_reply(f"可用项目：{names}\n用法：/project <项目名>")
        matches = tuple(
            repository
            for repository in repositories
            if argument
            in {
                repository.name,
                repository.repository_id,
                repository.checkout_path,
            }
        )
        if len(matches) != 1:
            available = "、".join(repository.name for repository in repositories)
            return _plain_reply(f"找不到唯一项目「{argument}」。可用项目：{available}")
        repository = matches[0]
        store.set_project(repository.repository_id)
        return _plain_reply(f"已切换到项目「{repository.name}」，后续问题只检索这个项目的 Wiki。")

    if not argument:
        return _plain_reply("用法：/resume <session-id>")
    if argument.lower() in {"clear", "none", "off"}:
        store.set_context_session(None)
        return _plain_reply("已退出恢复会话，后续只使用当前聊天上下文。")
    resumed = SessionStore(workspace, argument)
    if not resumed.path.is_file():
        return _plain_reply(f"找不到本地会话「{argument}」。")
    store.set_context_session(argument)
    store.set_project(resumed.project_id())
    return _plain_reply(f"已恢复会话「{argument}」，后续问题会结合该会话的最近上下文。")


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


def _text_reply(workspace: Path, result: AskPayload) -> dict[str, Any]:
    text = result["answer"]
    if result["status"] == "unknown" and text == "不知道":
        text = (
            "当前信息不足，无法可靠回答。请补充项目名、文件路径或完整函数/模块名；"
            "也可发送 /project 查看并选择项目。"
        )
    titles = [_page_title(workspace / path) for path in result["wiki_pages"]]
    if titles := [title for title in titles if title]:
        text += "\n\n来源：" + "、".join(dict.fromkeys(titles))
    return {"msg_type": "text", "content": {"text": text}}


def _plain_reply(text: str) -> dict[str, Any]:
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
