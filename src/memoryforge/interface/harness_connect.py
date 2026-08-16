"""One-shot DeepSeek Harness registration for the global MemoryForge Router."""

from __future__ import annotations

import json
import os
from pathlib import Path

from memoryforge.core.errors import MemoryForgeError
from memoryforge.interface.codex_connect import router_mcp_command

HARNESS_PATCH_BEGIN = "# BEGIN MEMORYFORGE MCP"
HARNESS_PATCH_END = "# END MEMORYFORGE MCP"
_HARNESS_ENTRY_ID = "memoryforge-mcp"
_HARNESS_SKILL_MARKER = "<!-- MemoryForge managed DeepSeek Harness skill -->"

_HARNESS_SKILL = f"""---
name: memoryforge-knowledge
description: >-
  Use MemoryForge first for facts in the registered local Workspace, including
  imported Feishu documents, prior AI conversations, history, decisions,
  architecture rationale, operations, and cross-repository context.
---
{_HARNESS_SKILL_MARKER}

# MemoryForge knowledge

1. For Workspace inventory questions such as registered repository names, counts, commits, or
   languages, call `mcp__memoryforge__memoryforge_status`.
2. For project facts, imported documents, decisions, operations, or cross-repository context,
   call `mcp__memoryforge__memoryforge_context` before answering.
3. For a question explicitly about prior AI conversation content, call
   `mcp__memoryforge__memoryforge_episodes` with the topic. If exactly one relevant Episode is
   returned, call `mcp__memoryforge__memoryforge_load_session` with its `session_refs` and the
   user's question. If it returns `no_session_evidence`, say the selected history does not
   contain matching evidence; do not substitute a loosely related Wiki answer.
4. Use `mcp__memoryforge__memoryforge_sessions` only when the user asks for an exact
   chronological session list.
5. If `evidence_status` is `grounded`, answer from `project_answer` and cite friendly page or
   source names.
6. If `evidence_status` is `partial`, state the supported facts and unsupported aspects.
7. If `evidence_status` is `no_local_evidence`, say the Wiki does not establish the requested
   fact. Do not invent an answer.
8. Treat `verification_status: unverified_history` as a useful lead, not a reviewed fact.
9. Call `mcp__memoryforge__memoryforge_read_evidence` only when exact source text is needed.
"""


class HarnessConnectConflictError(MemoryForgeError):
    """Raised when an unmanaged Harness entry conflicts with MemoryForge."""


def default_dsh_home() -> Path:
    configured = os.environ.get("DSH_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".dsh"


def connect_harness_router(
    workspace: Path,
    *,
    allow_local: bool = False,
    dsh_home: Path | None = None,
    profile: str = "web",
) -> dict[str, object]:
    """Register the global Router and install the Harness trigger Skill."""
    root = (dsh_home or default_dsh_home()).expanduser()
    command = router_mcp_command(workspace, allow_local=allow_local)
    patch_file, updated_patch = _prepare_harness_patch(
        command,
        dsh_home=root,
        profile=profile,
    )
    skill_file, updated_skill = _prepare_harness_skill(dsh_home=root)
    if updated_skill is not None:
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(updated_skill, encoding="utf-8")
    if updated_patch is not None:
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        patch_file.write_text(updated_patch, encoding="utf-8")
    changed = updated_patch is not None or updated_skill is not None
    return {
        "status": "connected" if changed else "unchanged",
        "server": "memoryforge",
        "routing": "mcp_roots",
        "command": command,
        "profile": profile,
        "patch_file": str(patch_file),
        "skill_file": str(skill_file),
        "restart_hint": (
            "DeepSeek Harness hot-reloads the profile patch. Start a new session after the "
            "MemoryForge tools appear; restart the app only if hot reload does not complete."
        ),
    }


def install_harness_patch(
    command: list[str],
    *,
    dsh_home: Path,
    profile: str = "web",
) -> tuple[Path, bool]:
    """Install one managed MCP client entry in a Harness profile patch."""
    patch_file, updated = _prepare_harness_patch(
        command,
        dsh_home=dsh_home,
        profile=profile,
    )
    if updated is None:
        return patch_file, False
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text(updated, encoding="utf-8")
    return patch_file, True


def install_harness_skill(*, dsh_home: Path) -> tuple[Path, bool]:
    """Install the global implicit Skill used by new Harness sessions."""
    skill_file, updated = _prepare_harness_skill(dsh_home=dsh_home)
    if updated is None:
        return skill_file, False
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(updated, encoding="utf-8")
    return skill_file, True


def _prepare_harness_patch(
    command: list[str],
    *,
    dsh_home: Path,
    profile: str,
) -> tuple[Path, str | None]:
    patch_file = dsh_home / "profiles" / profile / "cordis.patch.yml"
    if patch_file.is_symlink() or (patch_file.exists() and not patch_file.is_file()):
        raise ValueError("DeepSeek Harness profile patch must be a regular file")
    existing = patch_file.read_text(encoding="utf-8") if patch_file.exists() else ""
    updated = _replace_managed_patch(existing, _harness_patch_block(command))
    return patch_file, None if updated == existing else updated


def _prepare_harness_skill(*, dsh_home: Path) -> tuple[Path, str | None]:
    skill_dir = dsh_home / "skills" / "memoryforge-knowledge"
    if skill_dir.is_symlink() or (skill_dir.exists() and not skill_dir.is_dir()):
        raise ValueError("DeepSeek Harness skill path must be a directory")
    skill_file = skill_dir / "SKILL.md"
    if skill_file.is_symlink() or (skill_file.exists() and not skill_file.is_file()):
        raise ValueError("DeepSeek Harness skill file must be a regular file")
    existing = skill_file.read_text(encoding="utf-8") if skill_file.exists() else ""
    if existing == _HARNESS_SKILL:
        return skill_file, None
    if existing and _HARNESS_SKILL_MARKER not in existing:
        raise HarnessConnectConflictError(
            "DeepSeek Harness already has an unmanaged 'memoryforge-knowledge' Skill"
        )
    return skill_file, _HARNESS_SKILL


def _harness_patch_block(command: list[str]) -> str:
    arguments = "\n".join(f"          - {json.dumps(part)}" for part in command[1:])
    return "\n".join(
        (
            HARNESS_PATCH_BEGIN,
            "- insert:",
            f"    - id: {_HARNESS_ENTRY_ID}",
            "      name: '@deepseek-ai/dsh-mcp-client'",
            "      config:",
            "        transport: stdio",
            "        serverName: memoryforge",
            f"        command: {json.dumps(command[0])}",
            "        args:",
            arguments,
            "        env:",
            f"          PYTHONPATH: {json.dumps(str(Path(__file__).resolve().parents[2]))}",
            "        failOnStartupError: true",
            HARNESS_PATCH_END,
        )
    )


def _replace_managed_patch(existing: str, block: str) -> str:
    begin_count = existing.count(HARNESS_PATCH_BEGIN)
    end_count = existing.count(HARNESS_PATCH_END)
    if begin_count != end_count or begin_count > 1:
        raise HarnessConnectConflictError("DeepSeek Harness MemoryForge patch markers are invalid")
    if begin_count == 1:
        start = existing.index(HARNESS_PATCH_BEGIN)
        stop = existing.index(HARNESS_PATCH_END, start) + len(HARNESS_PATCH_END)
        return existing[:start] + block + existing[stop:]
    if f"id: {_HARNESS_ENTRY_ID}" in existing:
        raise HarnessConnectConflictError(
            "DeepSeek Harness already has an unmanaged 'memoryforge-mcp' entry"
        )
    prefix = _remove_empty_patch_sequence(existing)
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix.strip():
        prefix += "\n"
    return prefix + block + "\n"


def _remove_empty_patch_sequence(existing: str) -> str:
    content = [
        line.strip()
        for line in existing.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if content != ["[]"]:
        return existing
    return "\n".join(line for line in existing.splitlines() if line.strip() != "[]") + "\n"
