"""Deterministic local Obsidian navigation views for a Markdown Workspace."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from memoryforge.core.models import Sensitivity
from memoryforge.storage.workspace import Workspace

OUTPUT_RELATIVE = "obsidian"
SHARED_STATE_TAG = "shared-state"


@dataclass(frozen=True)
class _PageEntry:
    path: str
    title: str
    sensitivity: str
    tags: tuple[str, ...]


def build_obsidian(workspace: Path) -> dict[str, object]:
    """Write navigation-only Markdown views under ``obsidian``."""
    opened = Workspace.open_readonly(workspace)
    entries = _page_entries(opened)
    grouped: dict[str, list[_PageEntry]] = {"private": [], "stable": [], "shared": []}
    for entry in sorted(entries, key=lambda item: item.path):
        grouped[_classify(entry)].append(entry)

    output_dir = opened.root / OUTPUT_RELATIVE
    _ensure_output_dir(output_dir)
    _ignore_output(output_dir)

    files = {
        "Home.md": _render_home(grouped),
        "private-processes.md": _render_view(
            "私有过程",
            "view/private-process",
            "任一来源为 `local_only` 的页面只出现在这里。",
            grouped["private"],
        ),
        "stable-knowledge.md": _render_view(
            "稳定知识",
            "view/stable-knowledge",
            "没有 `local_only` 来源、也没有显式 `shared-state` 标记的公开页面。",
            grouped["stable"],
        ),
        "shared-state.md": _render_view(
            "共享业务状态",
            "view/shared-state",
            "只有全部来源为 `public` 且任一来源显式带 `shared-state` 标签时才进入这里。",
            grouped["shared"],
        ),
    }
    for name, content in files.items():
        _write_output_file(output_dir / name, content)

    return {
        "status": "built",
        "output": OUTPUT_RELATIVE,
        "files": sorted(files),
        "counts": {
            "private_processes": len(grouped["private"]),
            "stable_knowledge": len(grouped["stable"]),
            "shared_state": len(grouped["shared"]),
        },
    }


def _page_entries(workspace: Workspace) -> list[_PageEntry]:
    uri = f"{workspace.index_path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                page_sources.page_path,
                applied_version.sensitivity,
                applied_version.tags_json
            FROM page_sources
            JOIN sources ON sources.source_id = page_sources.source_id
            JOIN applied_source_versions AS applied
              ON applied.source_id = sources.source_id
            JOIN source_versions AS applied_version
              ON applied_version.id = applied.source_version_id
             AND applied_version.source_id = sources.id
            ORDER BY page_sources.page_path, sources.source_id
            """
        ).fetchall()

    by_path: dict[str, _PageEntry] = {}
    for row in rows:
        path = str(row["page_path"])
        sensitivity = str(row["sensitivity"])
        tags = _tags_from_json(str(row["tags_json"]))
        current = by_path.get(path)
        if current is None:
            by_path[path] = _PageEntry(
                path=path,
                title=_page_title(workspace.root, path),
                sensitivity=sensitivity,
                tags=tags,
            )
            continue
        by_path[path] = _PageEntry(
            path=path,
            title=current.title,
            sensitivity=(
                Sensitivity.LOCAL_ONLY.value
                if sensitivity == Sensitivity.LOCAL_ONLY.value
                else current.sensitivity
            ),
            tags=tuple(sorted(set(current.tags + tags))),
        )
    return list(by_path.values())


def _classify(page: _PageEntry) -> str:
    if page.sensitivity == Sensitivity.LOCAL_ONLY.value:
        return "private"
    if SHARED_STATE_TAG in page.tags:
        return "shared"
    return "stable"


def _page_title(root: Path, page_path: str) -> str:
    fallback = PurePosixPath(page_path).stem
    try:
        content = (root / page_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return fallback
    if content.startswith("---\n"):
        closing = content.find("\n---\n", len("---\n"))
        if closing >= 0:
            for line in content[len("---\n") : closing].splitlines():
                key, separator, value = line.partition(":")
                if key.strip() == "title" and separator:
                    title = _parse_title_value(value.strip())
                    if title is not None:
                        return title
    for line in content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return title or fallback
    return fallback


def _parse_title_value(value: str) -> str | None:
    if not value:
        return None
    if value[0] not in {"'", '"'}:
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, str) and decoded else None


def _tags_from_json(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(tag for tag in decoded if isinstance(tag, str))


def _render_home(grouped: dict[str, list[_PageEntry]]) -> str:
    counts = {
        "private_processes": len(grouped["private"]),
        "stable_knowledge": len(grouped["stable"]),
        "shared_state": len(grouped["shared"]),
    }
    lines = [
        *(_frontmatter("MemoryForge Obsidian", ("memoryforge", "obsidian", "generated"))),
        "",
        "# MemoryForge Obsidian",
        "",
        "本地只读导航层。底层稳定页面仍在 `wiki/pages/`，这里只生成链接，不复制正文。",
        "请把整个 MemoryForge Workspace 作为 Obsidian Vault 打开，再进入 `obsidian/Home.md`。",
        "",
        f"- [私有过程](private-processes.md) · {counts['private_processes']} 页",
        f"- [稳定知识](stable-knowledge.md) · {counts['stable_knowledge']} 页",
        f"- [共享业务状态](shared-state.md) · {counts['shared_state']} 页",
        "",
        "## 隐私边界",
        "",
        "- `local_only` 页面只进入私有过程，绝不进入共享业务状态。",
        "- 共享业务状态只在来源显式带 `shared-state` 标签时出现。",
        "- 本目录位于 `obsidian/`，不参与 `query`、`search` 或页面来源投影。",
        "- 重复执行只覆盖本目录中的导航文件，不修改 `raw/`、`wiki/` 或 SQLite 投影。",
    ]
    return "\n".join(lines) + "\n"


def _render_view(title: str, view_tag: str, rule: str, pages: list[_PageEntry]) -> str:
    lines = [
        *(_frontmatter(title, ("memoryforge", "obsidian", view_tag, "generated"))),
        "",
        f"# {title}",
        "",
        rule,
        "",
    ]
    if not pages:
        lines.append("暂无页面。")
    else:
        for page in pages:
            tags = json.dumps(page.tags, ensure_ascii=False)
            lines.append(
                f"- [{_escape_link_text(page.title)}]({_relative_page_link(page.path)})"
                f" — `{page.sensitivity}` · tags: {tags}"
            )
    return "\n".join(lines) + "\n"


def _frontmatter(title: str, tags: tuple[str, ...]) -> list[str]:
    return [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"tags: {json.dumps(list(tags), ensure_ascii=False)}",
        "---",
    ]


def _escape_link_text(title: str) -> str:
    normalized = " ".join(title.split())
    return normalized.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _ensure_output_dir(path: Path) -> None:
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise ValueError("obsidian output must be a real directory")
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _relative_page_link(page_path: str) -> str:
    output_depth = len(PurePosixPath(OUTPUT_RELATIVE).parts)
    return "/".join((*(".." for _ in range(output_depth)), *PurePosixPath(page_path).parts))


def _write_output_file(path: Path, content: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("obsidian output file must be a regular file")
    path.write_text(content, encoding="utf-8")


def _ignore_output(output_dir: Path) -> None:
    gitignore = output_dir / ".gitignore"
    if gitignore.is_symlink() or (gitignore.exists() and not gitignore.is_file()):
        raise ValueError("obsidian .gitignore must be a regular file")
    gitignore.write_text("*\n", encoding="utf-8")
