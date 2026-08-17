"""Budgeted navigation maps for global project questions."""

from __future__ import annotations

import json
from pathlib import Path

from memoryforge.compiler.index_rendering import _index_summaries
from memoryforge.storage.database import connect_readonly
from memoryforge.storage.workspace import Workspace, repository_page_paths

MAP_MAX_CHARACTERS = 4000


def build_context_map(
    workspace: Path,
    *,
    repository_id: str | None,
    allow_local: bool,
    max_characters: int = MAP_MAX_CHARACTERS,
) -> dict[str, object]:
    """Return visible INDEX entries in stable order within one hard budget."""
    opened = Workspace.open_readonly(workspace)
    visible = _visible_page_paths(opened, allow_local=allow_local)
    if repository_id is not None:
        visible &= set(repository_page_paths(opened.root, repository_id))
    index = opened.wiki_dir / "INDEX.md"
    if index.is_symlink() or not index.is_file():
        return {"entries": [], "characters": 0, "truncated": False}

    entries: list[dict[str, object]] = []
    characters = 0
    summaries = _index_summaries(index.read_text(encoding="utf-8"))
    eligible = [summary for summary in summaries if summary.path in visible]
    for summary in eligible:
        page = opened.root / summary.path
        if page.is_symlink() or not page.is_file():
            continue
        entry: dict[str, object] = {
            "title": summary.title,
            "page_path": summary.path,
            "summary": summary.summary,
            "kind": summary.page_type,
            "navigation_only": True,
        }
        size = len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
        if characters + size > max_characters:
            break
        entries.append(entry)
        characters += size
    return {
        "entries": entries,
        "characters": characters,
        "truncated": len(entries) < len(eligible),
    }


def visible_context_page_paths(
    workspace: Path,
    *,
    repository_id: str | None,
    allow_local: bool,
) -> frozenset[str]:
    """Return pages safe to expose before reading their summaries or facts."""
    opened = Workspace.open_readonly(workspace)
    visible = _visible_page_paths(opened, allow_local=allow_local)
    if repository_id is not None:
        visible &= set(repository_page_paths(opened.root, repository_id))
    return frozenset(visible)


def _visible_page_paths(workspace: Workspace, *, allow_local: bool) -> set[str]:
    with connect_readonly(workspace.index_path) as connection:
        rows = connection.execute(
            """
            SELECT
                page_sources.page_path,
                MAX(CASE WHEN versions.sensitivity = 'public' THEN 0 ELSE 1 END)
                    AS has_local
            FROM page_sources
            JOIN applied_source_versions AS applied
              ON applied.source_id = page_sources.source_id
            JOIN source_versions AS versions
              ON versions.id = applied.source_version_id
            GROUP BY page_sources.page_path
            ORDER BY page_sources.page_path
            """
        ).fetchall()
    return {str(row["page_path"]) for row in rows if allow_local or int(row["has_local"]) == 0}
