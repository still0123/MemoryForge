from __future__ import annotations

import json
from datetime import datetime
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
from memoryforge.models import GitRepositoryRecord
from memoryforge.provider import ProviderUnavailableError
from memoryforge.sessions import SessionStore


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


def test_feishu_llm_falls_back_to_wiki_when_provider_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)

    class FailingProvider:
        def answer_with_evidence(self, _messages: object) -> object:
            raise ProviderUnavailableError("provider temporarily unavailable")

    reply = reply_to_feishu_text(
        workspace,
        "When do cache entries expire?",
        provider=FailingProvider(),  # type: ignore[arg-type]
        session_id="oc_chat_123",
    )

    assert reply["content"]["text"].startswith("Cache entries expire after sixty seconds.")
    assert "来源：Cache policy" in reply["content"]["text"]


def test_feishu_llm_reply_uses_the_evidence_summarizer(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)

    class EvidenceProvider:
        def answer_with_evidence(
            self, messages: list[dict[str, str]]
        ) -> tuple[str, tuple[int, ...]]:
            facts = json.loads(messages[1]["content"])["facts"]
            assert facts == [
                {
                    "index": 0,
                    "quote": "Cache entries expire after sixty seconds.",
                    "section": "Cache policy",
                }
            ]
            return "缓存会在六十秒后过期。", (0,)

    reply = reply_to_feishu_text(
        workspace,
        "When do cache entries expire?",
        provider=EvidenceProvider(),  # type: ignore[arg-type]
    )

    assert reply["content"]["text"].startswith("缓存会在六十秒后过期。")
    assert "来源：Cache policy" in reply["content"]["text"]


def test_feishu_project_command_scopes_followup_questions(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)
    repository = GitRepositoryRecord(
        repository_id="a" * 64,
        name="efs-mgr",
        checkout_path="/code/efs-mgr",
        registered_at=datetime.now(),
    )
    monkeypatch.setattr("memoryforge.feishu_bot.list_git_checkouts", lambda _: (repository,))
    captured: list[str | None] = []

    def fake_answer(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.append(kwargs.get("repository_id"))
        return {
            "status": "answered",
            "answer": "ok",
            "citations": [],
            "wiki_pages": [],
            "source_id": None,
            "source_version": None,
            "locator": None,
            "quote": None,
        }

    monkeypatch.setattr("memoryforge.feishu_bot.answer_question", fake_answer)
    selected = reply_to_feishu_text(workspace, "/project efs-mgr", session_id="oc_chat")
    answer = reply_to_feishu_text(workspace, "这个项目是什么", session_id="oc_chat")

    assert "已切换到项目「efs-mgr」" in selected["content"]["text"]
    assert answer["content"]["text"] == "ok"
    assert captured == ["a" * 64]
    assert SessionStore(workspace, "oc_chat").project_id() == "a" * 64


def test_feishu_resume_command_adds_saved_context_to_followup(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)
    SessionStore(workspace, "oc_source").append(
        "缓存策略是什么？",
        "缓存默认保留六十秒。",
        [],
        model_safe=True,
    )
    captured: list[str] = []

    def fake_answer(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.append(str(kwargs.get("conversation_context", "")))
        return {
            "status": "answered",
            "answer": "ok",
            "citations": [],
            "wiki_pages": [],
            "source_id": None,
            "source_version": None,
            "locator": None,
            "quote": None,
        }

    monkeypatch.setattr("memoryforge.feishu_bot.answer_question", fake_answer)
    resumed = reply_to_feishu_text(workspace, "/resume oc_source", session_id="oc_chat")
    reply_to_feishu_text(workspace, "那它多久过期？", session_id="oc_chat")

    assert "已恢复会话「oc_source」" in resumed["content"]["text"]
    assert "缓存策略是什么？" in captured[0]
    assert "缓存默认保留六十秒。" in captured[0]
    assert SessionStore(workspace, "oc_chat").context_session_id() == "oc_source"


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
