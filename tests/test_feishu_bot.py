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
from memoryforge.manifests import SourceManifestStore
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


def test_feishu_wiki_command_creates_local_reviewable_memory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    SessionStore(workspace, "oc_chat_123").append(
        "Which database did we choose?",
        "We chose SQLite for local storage.",
        [],
        model_safe=False,
    )

    reply = reply_to_feishu_text(workspace, "/wiki 收录", session_id="oc_chat_123")

    assert reply["content"]["text"].startswith("已生成本地记忆草稿")
    manifest = SourceManifestStore(workspace / ".memoryforge/manifests/sources").list_all()[0]
    assert manifest.sensitivity.value == "local_only"
    assert manifest.tags == ("conversation", "platform:feishu", "unverified")
    snapshot = (workspace / manifest.snapshot_path).read_text(encoding="utf-8")
    assert "Which database did we choose?" in snapshot
    assert "## Assistant (unverified)" in snapshot
    assert "We chose SQLite for local storage." in snapshot


def test_feishu_wiki_auto_updates_memory_after_each_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    monkeypatch.setattr(
        "memoryforge.feishu_bot.answer_question",
        lambda *_args, **_kwargs: {
            "status": "answered",
            "answer": "Use SQLite for local storage.",
            "citations": [],
            "wiki_pages": [],
            "source_id": None,
            "source_version": None,
            "locator": None,
            "quote": None,
        },
    )

    enabled = reply_to_feishu_text(workspace, "/wiki auto on", session_id="oc_chat_123")
    for question in (
        "Which database?",
        "Which cache?",
        "Which queue?",
        "Which API?",
    ):
        reply_to_feishu_text(workspace, question, session_id="oc_chat_123")

    assert enabled["content"]["text"].startswith("自动收录已开启")
    manifests = SourceManifestStore(workspace / ".memoryforge/manifests/sources").list_all()
    manifest = max(manifests, key=lambda item: item.observed_at)
    snapshot = (workspace / manifest.snapshot_path).read_text(encoding="utf-8")
    assert "Which database?" in snapshot
    assert "Which API?" in snapshot
    assert "Use SQLite for local storage." in snapshot
    assert SessionStore(workspace, "oc_chat_123").auto_memory_enabled() is True

    disabled = reply_to_feishu_text(workspace, "/wiki auto off", session_id="oc_chat_123")
    assert disabled["content"]["text"].startswith("自动收录已关闭")
    assert SessionStore(workspace, "oc_chat_123").auto_memory_enabled() is False


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


def test_feishu_does_not_reuse_an_unknown_answer_as_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)
    question = "TransformPageToOffsetLimit的作用是什么"
    SessionStore(workspace, "oc_chat").append(
        question,
        "不知道",
        [],
        model_safe=False,
    )
    captured: list[tuple[str, str]] = []

    def fake_answer(_workspace: Path, query: str, **kwargs: object) -> dict[str, object]:
        captured.append((query, str(kwargs.get("conversation_context", ""))))
        return {
            "status": "unknown",
            "answer": "不知道",
            "citations": [],
            "wiki_pages": [],
            "source_id": None,
            "source_version": None,
            "locator": None,
            "quote": None,
        }

    monkeypatch.setattr("memoryforge.feishu_bot.answer_question", fake_answer)

    reply = reply_to_feishu_text(workspace, question, session_id="oc_chat")

    assert captured == [(question, "")]
    assert reply["content"]["text"] == (
        "当前信息不足，无法可靠回答。请补充项目名、文件路径或完整函数/模块名；"
        "也可发送 /project 查看并选择项目。"
    )


def test_feishu_asks_for_project_when_a_module_name_is_ambiguous(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)
    repositories = (
        GitRepositoryRecord(
            repository_id="a" * 64,
            name="efs-mgr",
            checkout_path="/code/efs-mgr",
            registered_at=datetime.now(),
        ),
        GitRepositoryRecord(
            repository_id="b" * 64,
            name="filenas-mgr",
            checkout_path="/code/filenas-mgr",
            registered_at=datetime.now(),
        ),
    )
    monkeypatch.setattr(
        "memoryforge.feishu_bot.find_code_module_repositories",
        lambda _workspace, module_path: repositories if module_path == "sm" else (),
    )
    monkeypatch.setattr(
        "memoryforge.feishu_bot.answer_question",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must ask first")),
    )

    reply = reply_to_feishu_text(workspace, "sm文件夹什么作用", session_id="oc_chat")

    assert reply["content"]["text"] == (
        "多个项目都包含 `sm` 模块。请先选择：/project efs-mgr、/project filenas-mgr"
    )


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
