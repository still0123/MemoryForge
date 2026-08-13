"""Copyable MCP stdio configuration snippets for AI Hosts (Phase 5).

Only Codex has an official, verifiable CLI, so it is the only Host with an
automatic adapter (``connect codex`` in :mod:`memoryforge.codex_connect`).
Every other Host gets copyable configuration only: the standard ``mcpServers``
JSON object (Claude Code ``.mcp.json``, Claude Desktop
``claude_desktop_config.json``, VS Code ``.vscode/mcp.json``) or the Codex
``~/.codex/config.toml`` ``[mcp_servers.*]``
block (also used by the ChatGPT Desktop app). This module never parses or
edits Host configuration files.

The snippet reuses the exact argv the CLI would register with Codex
(:func:`memoryforge.codex_connect.mcp_command`) and the stable server name
(:func:`memoryforge.agent_access.server_name`), so a manually pasted JSON
block and ``connect codex`` always agree.
"""

from __future__ import annotations

import json
from pathlib import Path

from memoryforge.agent_access import server_name
from memoryforge.codex_connect import mcp_command


def mcp_servers_config(
    workspace: Path,
    project_root: Path,
    *,
    allow_local: bool = False,
) -> dict[str, object]:
    """Return the standard ``mcpServers`` JSON object for one binding.

    Paste the value (or the whole object) into a Host config that reads the
    MCP ``mcpServers`` schema: Claude Code ``.mcp.json``, Claude Desktop
    ``claude_desktop_config.json`` or VS Code ``.vscode/mcp.json``.
    """
    command = mcp_command(workspace, project_root, allow_local=allow_local)
    return {
        "mcpServers": {
            server_name(workspace, project_root): {
                "command": command[0],
                "args": command[1:],
            }
        }
    }


def codex_toml_block(
    workspace: Path,
    project_root: Path,
    *,
    allow_local: bool = False,
) -> str:
    """Return the ``[mcp_servers.<name>]`` block for ``~/.codex/config.toml``.

    The ChatGPT Desktop app and the Codex CLI/IDE extension share this
    configuration (spec §10.1). Prefer ``connect codex``, which registers the
    same block through the official CLI; this snippet is the copyable fallback.
    """
    command = mcp_command(workspace, project_root, allow_local=allow_local)
    name = server_name(workspace, project_root)
    arguments = "".join(f"    {_toml_string(part)},\n" for part in command[1:])
    return f"[mcp_servers.{name}]\ncommand = {_toml_string(command[0])}\nargs = [\n{arguments}]\n"


def _toml_string(value: str) -> str:
    """Encode one argv part as a TOML basic string (JSON escapes are a subset)."""
    return json.dumps(value)
