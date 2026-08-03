"""Run the local Feishu bot loop through lark-cli."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from memoryforge.errors import MemoryForgeError
from memoryforge.feishu_bot import FeishuBotError, handle_lark_cli_event
from memoryforge.provider import OpenAICompatibleProvider


class FeishuServiceError(MemoryForgeError):
    """Raised when the local lark-cli bot bridge cannot start or reply."""


def serve_feishu_bot(
    workspace: Path,
    *,
    max_pages: int = 3,
    provider: OpenAICompatibleProvider | None = None,
    allow_local: bool = False,
) -> None:
    """Listen for bot messages and reply with the existing Wiki answer path."""
    listener = _start_listener()
    try:
        _wait_until_ready(listener)
        assert listener.stdout is not None
        for line in listener.stdout:
            _handle_event_line(
                workspace, line, max_pages=max_pages, provider=provider, allow_local=allow_local
            )
    finally:
        if listener.poll() is None:
            listener.terminate()


def _start_listener() -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(
            ["lark-cli", "event", "consume", "im.message.receive_v1", "--as", "bot"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise FeishuServiceError("lark-cli is not available") from exc


def _wait_until_ready(listener: subprocess.Popen[str]) -> None:
    assert listener.stderr is not None
    for line in listener.stderr:
        if "[event] ready event_key=im.message.receive_v1" in line:
            return
    raise FeishuServiceError("Feishu event listener did not start")


def _handle_event_line(
    workspace: Path,
    line: str,
    *,
    max_pages: int,
    provider: OpenAICompatibleProvider | None,
    allow_local: bool,
) -> None:
    started_at = time.monotonic()
    try:
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError
        handled = handle_lark_cli_event(
            workspace,
            event,
            max_pages=max_pages,
            provider=provider,
            allow_local=allow_local,
        )
        if handled is None:
            print("Feishu event skipped", file=sys.stderr)
            return
        _send_reply(handled["message_id"], handled["reply"])
        print(f"Feishu reply sent in {time.monotonic() - started_at:.2f}s", file=sys.stderr)
    except (FeishuBotError, FeishuServiceError, ValueError, json.JSONDecodeError) as exc:
        print(f"Feishu event ignored: {exc}", file=sys.stderr)


def _send_reply(message_id: object, reply: dict[str, Any]) -> None:
    text = reply.get("content", {}).get("text")
    if not isinstance(message_id, str) or not isinstance(text, str):
        raise FeishuServiceError("Feishu reply payload is invalid")
    try:
        completed = subprocess.run(
            [
                "lark-cli",
                "im",
                "+messages-reply",
                "--message-id",
                message_id,
                "--text",
                text,
                "--as",
                "bot",
                "--idempotency-key",
                message_id,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise FeishuServiceError("lark-cli is not available") from exc
    if completed.returncode != 0:
        raise FeishuServiceError("Feishu reply failed")
