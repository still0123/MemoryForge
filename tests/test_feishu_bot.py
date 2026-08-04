from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.feishu_bot import (
    FeishuBotError,
    handle_feishu_event,
    handle_lark_cli_event,
    reply_to_feishu_text,
)
from memoryforge.feishu_service import _send_reply


def test_feishu_event_returns_a_citable_wiki_reply(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)

    result = handle_feishu_event(
        workspace,
        {
            "event": {
                "message": {
                    "message_id": "om_123",
                    "message_type": "text",
                    "content": json.dumps({"text": "When do cache entries expire?"}),
                }
            }
        },
    )

    assert result["message_id"] == "om_123"
    assert result["reply"]["content"]["text"].startswith(
        "Cache entries expire after sixty seconds."
    )
    assert "来源：Cache policy" in result["reply"]["content"]["text"]
    assert "wiki/pages/" not in result["reply"]["content"]["text"]


def test_feishu_event_handles_url_verification_and_rejects_non_text(tmp_path: Path) -> None:
    assert handle_feishu_event(tmp_path, {"challenge": "challenge-token"}) == {
        "challenge": "challenge-token"
    }
    with pytest.raises(FeishuBotError, match="text message"):
        handle_feishu_event(tmp_path, {"event": {"message": {"message_type": "image"}}})


def test_lark_cli_event_uses_flattened_text_and_skips_bot_messages(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)

    result = handle_lark_cli_event(
        workspace,
        {
            "message_id": "om_123",
            "message_type": "text",
            "content": "When do cache entries expire?",
            "sender_type": "user",
        },
    )

    assert result is not None
    assert result["message_id"] == "om_123"
    assert result["reply"]["content"]["text"].startswith(
        "Cache entries expire after sixty seconds."
    )
    assert (
        handle_lark_cli_event(
            workspace,
            {
                "message_id": "om_bot",
                "message_type": "text",
                "content": "I am a bot reply",
                "sender_type": "bot",
            },
        )
        is None
    )


def test_lark_cli_event_passes_chat_id_as_session_and_missing_id_is_single_turn(
    tmp_path: Path, monkeypatch
) -> None:
    captured: list[object] = []

    def fake_reply(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.append(kwargs.get("session_id"))
        return {"msg_type": "text", "content": {"text": "ok"}}

    monkeypatch.setattr("memoryforge.feishu_bot.reply_to_feishu_text", fake_reply)

    result = handle_lark_cli_event(
        tmp_path,
        {
            "message_id": "om_123",
            "chat_id": "oc_chat_123",
            "message_type": "text",
            "content": "那数据面呢",
            "sender_type": "user",
        },
    )
    assert result is not None
    assert result["session_id"] == "oc_chat_123"

    handle_lark_cli_event(
        tmp_path,
        {
            "message_id": "om_124",
            "message_type": "text",
            "content": "单轮问题",
            "sender_type": "user",
        },
    )
    assert captured == ["oc_chat_123", None]


def test_feishu_agent_falls_back_to_wiki_when_provider_fails(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)

    class FailingProvider:
        def agent_step(self, _messages: object) -> object:
            raise ValueError("provider temporarily unavailable")

    reply = reply_to_feishu_text(
        workspace,
        "When do cache entries expire?",
        provider=FailingProvider(),  # type: ignore[arg-type]
        session_id="oc_chat_123",
    )

    assert reply["content"]["text"].startswith("Cache entries expire after sixty seconds.")
    assert "来源：Cache policy" in reply["content"]["text"]


def test_feishu_service_replies_as_bot_with_message_idempotency(monkeypatch) -> None:
    captured: list[list[str]] = []

    class Completed:
        returncode = 0

    def fake_run(command: list[str], **_: object) -> Completed:
        captured.append(command)
        return Completed()

    monkeypatch.setattr("memoryforge.feishu_service.subprocess.run", fake_run)

    _send_reply("om_123", {"msg_type": "text", "content": {"text": "answer"}})

    assert captured == [
        [
            "lark-cli",
            "im",
            "+messages-reply",
            "--message-id",
            "om_123",
            "--text",
            "answer",
            "--as",
            "bot",
            "--idempotency-key",
            "om_123",
        ]
    ]


def test_feishu_reply_cli_prints_send_message_payload(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        app,
        ["feishu-reply", "When do cache entries expire?", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["msg_type"] == "text"


def _applied_workspace(tmp_path: Path, monkeypatch) -> Path:
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
    assert (
        runner.invoke(
            app,
            ["import", str(source), "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    assert (
        runner.invoke(
            app,
            ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    return workspace
