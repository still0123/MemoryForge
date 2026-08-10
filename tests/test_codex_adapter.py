from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.manifests import SourceManifestStore
from memoryforge.workspace import Workspace


def test_codex_import_keeps_only_conversation_text(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    rollout = tmp_path / "rollout.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "thread-123"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "private developer prompt"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<environment_context>hidden</environment_context>",
                    },
                    {
                        "type": "input_text",
                        "text": '<image name="x" path="/var/folders/private.png">',
                    },
                    {"type": "input_text", "text": "<turn_aborted>noise</turn_aborted>"},
                    {"type": "input_text", "text": "Which database did we choose?"},
                ],
            },
        },
        {"type": "event_msg", "payload": {"type": "agent_reasoning", "text": "secret reasoning"}},
        {
            "type": "response_item",
            "payload": {"type": "function_call_output", "output": "private tool output"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "We chose SQLite. See file:///home/alice/repo/db.py#L1.",
                    }
                ],
            },
        },
    ]
    rollout.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    first = runner.invoke(app, ["codex-import", str(rollout), "-w", str(workspace)])
    second = runner.invoke(app, ["codex-import", str(rollout), "-w", str(workspace)])

    assert first.exit_code == 0, first.stdout
    assert json.loads(first.stdout)["status"] == "created"
    assert json.loads(second.stdout)["status"] == "unchanged"
    manifest = SourceManifestStore(Workspace.open(workspace).manifest_dir).list_all()[0]
    assert manifest.sensitivity.value == "local_only"
    assert manifest.tags == (
        "conversation",
        "platform:codex",
        "unverified",
        "thread:thread-123",
    )
    snapshot = (workspace / manifest.snapshot_path).read_text(encoding="utf-8")
    assert "Which database did we choose?" in snapshot
    assert "We chose SQLite." in snapshot
    assert "file:///home/alice" not in snapshot
    assert "<local-path>" in snapshot
    assert "environment_context" not in snapshot
    assert "/var/folders" not in snapshot
    assert "turn_aborted" not in snapshot
    assert "developer prompt" not in snapshot
    assert "secret reasoning" not in snapshot
    assert "tool output" not in snapshot


def test_codex_import_skips_oversized_tool_record(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "thread-large"}})
        + "\n"
        + '{"type":"response_item","payload":{"type":"function_call_output","output":"'
        + ("x" * (5 * 1024 * 1024))
        + '"}}\n'
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Keep this."}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["codex-import", str(rollout), "-w", str(workspace)])

    assert result.exit_code == 0, result.stdout
    manifest = SourceManifestStore(Workspace.open(workspace).manifest_dir).list_all()[0]
    snapshot = (workspace / manifest.snapshot_path).read_text(encoding="utf-8")
    assert "Keep this." in snapshot
    assert "xxxxx" not in snapshot
