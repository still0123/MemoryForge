"""Capture inbox, handoff, and session bootstrap CLI commands."""

from __future__ import annotations

import json
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from memoryforge.storage.database import connect, connect_readonly

capture_app = typer.Typer(no_args_is_help=True, help="Capture inbox and handoff utilities.")


def register_capture_commands(app: typer.Typer) -> None:
    app.add_typer(capture_app, name="capture")


def _json_out(payload: object) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@capture_app.command("drain")
def capture_drain(
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("."),
) -> None:
    """薄命令 → capture_inbox.drain_capture_spool。"""
    try:
        from memoryforge.storage.capture_inbox import drain_capture_spool
        from memoryforge.storage.workspace import workspace_database

        database = workspace_database(workspace)
        with connect(database) as connection:
            result = drain_capture_spool(workspace, connection)
        _json_out(
            {
                "status": "ok",
                "result": {
                    "processed": result.processed,
                    "failed": result.failed,
                    "skipped_duplicates": result.skipped_duplicates,
                    "errors": list(result.errors),
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        _json_out({"status": "error", "error": str(exc)})


@capture_app.command("handoff")
def capture_handoff(
    repo_id: Annotated[str, typer.Option("--repo-id")],
    before: Annotated[str | None, typer.Option("--before")] = None,
    max_chars: Annotated[int, typer.Option("--max-chars")] = 20000,
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("."),
) -> None:
    """薄命令 → handoff.build_handoff。"""
    try:
        from memoryforge.storage.capture_inbox import drain_capture_spool
        from memoryforge.storage.handoff import build_handoff
        from memoryforge.storage.workspace import workspace_database

        with connect(workspace_database(workspace)) as connection:
            drain_capture_spool(workspace, connection)
            result = build_handoff(
                connection,
                repository_id=repo_id,
                before=(datetime.fromisoformat(before) if before else datetime.now(UTC)),
                max_characters=max_chars,
            )
        _json_out({"status": "ok", "handoff": result.model_dump(mode="json")})
    except Exception as exc:  # noqa: BLE001
        _json_out({"status": "error", "error": str(exc)})


@capture_app.command("sessions")
def capture_sessions(
    limit: Annotated[int, typer.Option("--limit")] = 20,
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("."),
) -> None:
    """List applied AI conversation memories available for startup loading."""
    try:
        from memoryforge.storage.session_bootstrap import list_conversation_sessions
        from memoryforge.storage.workspace import workspace_database

        with connect_readonly(workspace_database(workspace)) as connection:
            sessions = list_conversation_sessions(connection, limit=limit)
        _json_out(
            {
                "status": "ok",
                "sessions": [
                    {
                        "source_id": session.source_id,
                        "source_version": session.source_version,
                        "title": session.title,
                        "observed_at": session.observed_at,
                        "summary": session.summary,
                    }
                    for session in sessions
                ],
            }
        )
    except Exception as exc:  # noqa: BLE001
        _json_out({"status": "error", "error": str(exc)})


@capture_app.command("queue")
def capture_queue(
    sources: Annotated[list[str], typer.Option("--source")],
    host: Annotated[str, typer.Option("--host", help="claude|codex")],
    project: Annotated[Path, typer.Option("--project", "-p")],
    max_chars: Annotated[int, typer.Option("--max-chars")] = 6000,
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("."),
) -> None:
    """Queue selected AI conversations for one new client session."""
    try:
        from memoryforge.storage.session_bootstrap import queue_startup_capsule
        from memoryforge.storage.workspace import workspace_database

        if host not in {"claude", "codex"}:
            raise ValueError("host must be claude or codex")
        project = project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError("project must be an existing directory")
        with connect(workspace_database(workspace)) as connection:
            capsule = queue_startup_capsule(
                connection,
                source_ids=sources,
                host=host,  # type: ignore[arg-type]
                project_root=project,
                max_characters=max_chars,
            )
        _json_out(
            {
                "status": "queued",
                "capsule_id": capsule.capsule_id,
                "host": capsule.host,
                "project_root": capsule.project_root,
                "source_count": len(capsule.source_ids),
                "character_count": capsule.character_count,
            }
        )
    except Exception as exc:  # noqa: BLE001
        _json_out({"status": "error", "error": str(exc)})


@capture_app.command("startup")
def capture_startup(
    host: Annotated[str, typer.Option("--host", help="claude|codex")],
    project: Annotated[Path | None, typer.Option("--project", "-p")] = None,
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("."),
) -> None:
    """Consume one queued capsule and emit SessionStart hook JSON."""
    try:
        from memoryforge.storage.session_bootstrap import consume_startup_capsule
        from memoryforge.storage.workspace import workspace_database

        if host not in {"claude", "codex"}:
            raise ValueError("host must be claude or codex")
        if project is None:
            hook_input = json.load(sys.stdin)
            project = Path(str(hook_input["cwd"]))
        project = project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError("project must be an existing directory")
        with connect(workspace_database(workspace)) as connection:
            capsule = consume_startup_capsule(
                connection,
                host=host,  # type: ignore[arg-type]
                project_root=project,
            )
        if capsule is not None:
            _json_out(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": capsule.content,
                    }
                }
            )
    except Exception as exc:  # noqa: BLE001
        _json_out({"systemMessage": f"MemoryForge startup skipped: {exc}"})


@capture_app.command("hook-config")
def capture_hook_config(
    host: Annotated[str, typer.Option("--host", help="claude|codex")],
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("."),
) -> None:
    """Print a SessionStart hook snippet to merge into the client config."""
    try:
        if host not in {"claude", "codex"}:
            raise ValueError("host must be claude or codex")
        command = shlex.join(
            [
                sys.executable,
                "-m",
                "memoryforge.storage.session_bootstrap",
                "--host",
                host,
                "--workspace",
                str(workspace.resolve(strict=False)),
            ]
        )
        target = "~/.claude/settings.json" if host == "claude" else "~/.codex/hooks.json"
        _json_out(
            {
                "status": "ok",
                "merge_into": target,
                "config": {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": command,
                                    }
                                ]
                            }
                        ]
                    }
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        _json_out({"status": "error", "error": str(exc)})


@capture_app.command("proposal")
def capture_proposal(
    repo_id: Annotated[str, typer.Option("--repo-id")],
    session: Annotated[str, typer.Option("--session")],
    print_output: Annotated[bool, typer.Option("--print")] = False,
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("."),
) -> None:
    """薄命令 → handoff.show_session_proposal。"""
    try:
        from memoryforge.storage.capture_inbox import build_capture_proposal
        from memoryforge.storage.workspace import workspace_database

        with connect(workspace_database(workspace)) as connection:
            result = build_capture_proposal(connection, repository_id=repo_id, session_id=session)
        if print_output:
            _json_out(result.__dict__)
        else:
            _json_out({"status": "ok", "proposal": result.__dict__})
    except Exception as exc:  # noqa: BLE001
        _json_out({"status": "error", "error": str(exc)})
