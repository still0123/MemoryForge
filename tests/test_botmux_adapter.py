from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.interface.cli import app
from memoryforge.core.manifests import SourceManifestStore
from memoryforge.storage.workspace import Workspace


def test_botmux_hooks_capture_codex_conversation_as_local_memory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    common = {
        "larkAppId": "cli_test",
        "chatId": "oc_chat",
        "scope": "thread",
        "anchor": "om_anchor",
        "msgType": "text",
        "contentTruncated": False,
    }

    card = runner.invoke(
        app,
        ["botmux-hook", "--workspace", str(workspace)],
        input=json.dumps(
            {
                **common,
                "event": "outbound.send",
                "messageId": "om_card",
                "msgType": "interactive",
                "content": '{"elements":[{"tag":"markdown"}]}',
            }
        ),
    )

    inbound = runner.invoke(
        app,
        ["botmux-hook", "--workspace", str(workspace)],
        input=json.dumps(
            {
                **common,
                "event": "topic.new",
                "messageId": "om_user",
                "content": "Which database did we choose?",
            }
        ),
    )
    outbound = runner.invoke(
        app,
        ["botmux-hook", "--workspace", str(workspace)],
        input=json.dumps(
            {
                **common,
                "event": "outbound.reply",
                "messageId": "om_user",
                "replyId": "om_assistant",
                "sessionId": "codex-session-1",
                "content": "We chose SQLite for local storage.",
            }
        ),
    )
    exited = runner.invoke(
        app,
        ["botmux-hook", "--workspace", str(workspace)],
        input=json.dumps(
            {
                **common,
                "event": "session.exit",
                "sessionId": "codex-session-1",
                "reason": "dashboard_close",
            }
        ),
    )

    assert json.loads(card.stdout)["status"] == "ignored"
    assert json.loads(inbound.stdout)["status"] == "recorded"
    assert json.loads(outbound.stdout)["status"] == "created"
    assert json.loads(exited.stdout)["status"] == "unchanged"
    manifest = SourceManifestStore(Workspace.open(workspace).manifest_dir).list_all()[0]
    assert manifest.sensitivity.value == "local_only"
    assert manifest.tags == ("conversation", "platform:botmux", "unverified")
    snapshot = (workspace / manifest.snapshot_path).read_text(encoding="utf-8")
    assert "Which database did we choose?" in snapshot
    assert "## Assistant (unverified)" in snapshot
    assert "We chose SQLite for local storage." in snapshot
