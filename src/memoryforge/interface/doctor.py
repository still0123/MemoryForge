"""Read-only environment and workspace diagnostics."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from memoryforge.compiler.wiki_facts import parse_page_facts
from memoryforge.core.platform_lock import inspect_posix_namespace_lock_root
from memoryforge.interface.codex_connect import (
    AGENTS_MCP_BEGIN,
    AGENTS_MCP_END,
    AGENTS_RECALL_BEGIN,
    AGENTS_RECALL_END,
)
from memoryforge.query.provider import ProviderConfig
from memoryforge.storage.database import connect_readonly
from memoryforge.storage.errors import WorkspaceIntegrityError
from memoryforge.storage.projection import candidate_page_sources
from memoryforge.storage.workspace import Workspace, list_git_checkouts


def doctor_report(
    workspace: Path,
    *,
    lock_root_resolver: Callable[[], Path] | None = None,
) -> dict[str, object]:
    lock_root_resolver = lock_root_resolver or inspect_posix_namespace_lock_root
    checks = [
        _item(
            "python",
            status="ok",
            message=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ),
        _item("platform", status="ok", message=platform.system()),
        _git(),
    ]
    workspace_item, opened = _workspace(workspace)
    checks.append(workspace_item)
    root = Path(workspace).expanduser()
    index_path = opened.index_path if opened is not None else root / ".memoryforge/index.sqlite"
    checks.append(_index(index_path))
    checks.append(_projection(opened))
    checks.append(_lock_directory(opened, lock_root_resolver=lock_root_resolver))
    checks.append(_model())
    checks.append(_feishu())
    checks.append(_codex())
    checks.append(_agents_block(opened))
    remediation = [
        str(check["remediation"]) for check in checks if check.get("remediation") is not None
    ]
    return {
        "status": "error" if any(check["status"] == "error" for check in checks) else "ok",
        "checks": checks,
        "remediation": remediation,
    }


def _item(
    name: str,
    *,
    status: str,
    message: str | None = None,
    remediation: str | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {"name": name, "status": status}
    if message is not None:
        item["message"] = message
    if remediation is not None:
        item["remediation"] = remediation
    return item


def _git() -> dict[str, object]:
    executable = shutil.which("git")
    if executable is None:
        return _item(
            "git",
            status="error",
            remediation="Install Git and add it to PATH.",
        )
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _item(
            "git",
            status="error",
            message=str(exc),
            remediation="Make the installed Git executable runnable.",
        )
    if completed.returncode != 0:
        return _item(
            "git",
            status="error",
            message=completed.stderr.strip(),
            remediation="Reinstall Git or fix its executable permissions.",
        )
    return _item("git", status="ok", message=completed.stdout.strip())


def _codex() -> dict[str, object]:
    executable = shutil.which("codex")
    if executable is None:
        return _item(
            "codex",
            status="not_configured",
            message="Codex CLI not found on PATH.",
            remediation="Install the Codex CLI, then run 'memoryforge connect codex'.",
        )
    try:
        completed = subprocess.run(
            [executable, "mcp", "list", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _item(
            "codex",
            status="error",
            message=str(exc),
            remediation="Make the Codex CLI executable runnable.",
        )
    if completed.returncode != 0:
        return _item(
            "codex",
            status="error",
            message=completed.stderr.strip() or "codex mcp list failed",
            remediation="Fix the Codex CLI configuration, then re-run doctor.",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _item(
            "codex",
            status="error",
            message="codex mcp list --json returned unparseable output.",
            remediation="Update the Codex CLI to a version with machine-readable MCP output.",
        )
    if isinstance(payload, dict):
        registered = [name for name in payload if name.startswith("memoryforge-")]
    elif isinstance(payload, list):
        registered = [
            str(entry["name"])
            for entry in payload
            if isinstance(entry, dict) and str(entry.get("name", "")).startswith("memoryforge-")
        ]
    else:
        registered = []
    if not registered:
        return _item(
            "codex",
            status="ok",
            message="Codex CLI available; no MemoryForge MCP server registered "
            "(run 'memoryforge connect codex' to register one).",
        )
    return _item(
        "codex",
        status="ok",
        message=f"{len(registered)} MemoryForge MCP server(s) registered.",
    )


def _agents_block(opened: Workspace | None) -> dict[str, object]:
    if opened is None:
        return _item(
            "agents_block",
            status="not_configured",
            message="Workspace unavailable; project AGENTS.md files not inspected.",
        )
    managed = 0
    for checkout in list_git_checkouts(opened.root):
        agents_path = Path(checkout.checkout_path) / "AGENTS.md"
        try:
            text = agents_path.read_text(encoding="utf-8")
        except OSError:
            continue
        mcp_ok = AGENTS_MCP_BEGIN in text and AGENTS_MCP_END in text
        recall_ok = AGENTS_RECALL_BEGIN in text and AGENTS_RECALL_END in text
        if mcp_ok or recall_ok:
            managed += 1
    if managed == 0:
        return _item(
            "agents_block",
            status="not_configured",
            message="No managed MemoryForge block in registered project AGENTS.md files.",
            remediation=(
                "Run 'memoryforge connect codex' for each project that should "
                "use on-demand knowledge."
            ),
        )
    return _item(
        "agents_block",
        status="ok",
        message=f"{managed} registered project(s) carry a managed MemoryForge block.",
    )


def _workspace(workspace: Path) -> tuple[dict[str, object], Workspace | None]:
    try:
        opened = Workspace.open_readonly(workspace)
    except Exception as exc:
        return (
            _item(
                "workspace",
                status="error",
                message=str(exc),
                remediation="Run 'memoryforge init <workspace>' or fix the workspace path.",
            ),
            None,
        )
    return _item("workspace", status="ok", message=str(opened.root)), opened


def _index(index_path: Path) -> dict[str, object]:
    try:
        with connect_readonly(index_path) as connection:
            quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
            if quick_check != ["ok"]:
                raise sqlite3.DatabaseError("SQLite quick_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise sqlite3.IntegrityError("SQLite foreign_key_check failed")
            copied = sqlite3.connect(":memory:")
            try:
                connection.backup(copied)
                copied.execute("INSERT INTO source_fts(source_fts) VALUES ('integrity-check')")
                copied.execute(
                    """
                    INSERT INTO wiki_fact_fts(wiki_fact_fts, rank)
                    VALUES ('integrity-check', 1)
                    """
                )
            finally:
                copied.close()
    except Exception as exc:
        return _item(
            "index",
            status="error",
            message=str(exc),
            remediation="Run 'memoryforge init <workspace>' or restore .memoryforge/index.sqlite.",
        )
    return _item("index", status="ok")


def _projection(workspace: Workspace | None) -> dict[str, object]:
    if workspace is None:
        return _item(
            "projection",
            status="error",
            remediation="Restore a valid Workspace before checking its Wiki projection.",
        )
    try:
        commit = workspace.current_commit()
        paths = workspace.version_store.list_wiki_paths_at(commit)
        contents = workspace.version_store.read_wiki_texts_at(commit, paths=paths) if paths else {}
        expected_sources = {
            (page_path, source_id)
            for page_path, source_ids in candidate_page_sources(contents).items()
            for source_id in source_ids
        }
        expected_facts = {
            (page_path, fact.fact_id)
            for page_path, content in contents.items()
            for fact in parse_page_facts(page_path, content)
        }
        with connect_readonly(workspace.index_path) as connection:
            actual_sources = {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT page_path, source_id FROM page_sources"
                ).fetchall()
            }
            actual_facts = {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT page_path, fact_id FROM wiki_facts"
                ).fetchall()
            }
        workspace.version_store.require_clean_paths(("wiki",))
        if expected_sources != actual_sources or expected_facts != actual_facts:
            raise WorkspaceIntegrityError("Git Wiki and SQLite projection differ")
    except Exception as exc:
        return _item(
            "projection",
            status="error",
            message=str(exc),
            remediation=(
                "Recover any interrupted apply, then run rollback or restore the Workspace "
                "from a verified backup."
            ),
        )
    return _item("projection", status="ok", message=commit)


def _lock_directory(
    workspace: Workspace | None,
    *,
    lock_root_resolver: Callable[[], Path],
) -> dict[str, object]:
    try:
        if sys.platform == "win32":
            if workspace is None:
                raise FileNotFoundError("Workspace lock directory is unavailable")
            path = workspace.internal_dir
        else:
            path = lock_root_resolver()
        if not path.exists():
            return _item(
                "lock_directory",
                status="ok",
                message="The private lock directory will be created on first write.",
            )
        if not path.is_dir():
            raise FileNotFoundError("lock directory is missing")
        if not os.access(path, os.W_OK | os.X_OK):
            raise PermissionError("lock directory is not writable")
    except Exception as exc:
        return _item(
            "lock_directory",
            status="error",
            message=str(exc),
            remediation=(
                "Create a private ~/.memoryforge-locks directory owned by the current user "
                "with mode 0700."
                if sys.platform != "win32"
                else "Make the Workspace .memoryforge directory writable by the current user."
            ),
        )
    return _item("lock_directory", status="ok")


def _model() -> dict[str, object]:
    try:
        ProviderConfig.from_environment()
    except Exception:
        status = "not_configured"
    else:
        status = "configured"
    return _item("model", status=status)


def _feishu() -> dict[str, object]:
    return _item(
        "feishu",
        status="configured" if shutil.which("lark-cli") is not None else "not_configured",
    )
