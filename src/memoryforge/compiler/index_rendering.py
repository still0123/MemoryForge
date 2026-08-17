"""Knowledge index rendering and frontmatter parsing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, cast

from memoryforge.storage.workspace import Workspace

PageType = Literal["entity", "concept", "synthesis"]
_PAGE_TYPES: tuple[PageType, ...] = ("entity", "concept", "synthesis")
_FRONTMATTER = re.compile(r"\A---\n(?P<fields>.*?)\n---\n", re.DOTALL)
_INDEX_ENTRY = re.compile(
    r"- \[(?P<title>(?:\\.|[^\]])+)\]"
    r"\((?P<path>[^)]+)\) — (?P<summary>.+)"
)
_INDEX_ACCESS_KEY = re.compile(r"\b(?:AKLT|LTAI)[A-Za-z0-9+/=]{12,}")
_INDEX_SECRET_VALUE = re.compile(
    r"(?P<prefix>(?:[a-z0-9_]*(?:password|passwd|token|secret|access[a-z0-9_]*key)"
    r"[a-z0-9_]*|['\"](?:ak|sk)['\"]|\b(?:ak|sk)\b)\s*[:=]\s*['\"])[^'\"]+"
    r"(?P<suffix>['\"])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PageSummary:
    path: str
    title: str
    page_type: PageType
    summary: str


def render_index(
    workspace: Workspace,
    candidate_files: dict[str, str],
    *,
    removed_paths: set[str] | None = None,
) -> str:
    index = workspace.wiki_dir / "INDEX.md"
    removed = removed_paths or set()
    existing = _index_summaries(index.read_text(encoding="utf-8") if index.is_file() else "")
    changed = [
        summary
        for path, content in candidate_files.items()
        if path != "wiki/INDEX.md"
        if (summary := _parse_page_summary(path, content)) is not None
    ]
    changed_paths = {page.path for page in changed}
    pages = sorted(
        [page for page in existing if page.path not in changed_paths and page.path not in removed]
        + changed,
        key=lambda page: (page.page_type, page.title.lower(), page.path),
    )
    sections = {
        "entity": "Entities",
        "concept": "Concepts",
        "synthesis": "Synthesis",
    }
    lines = [
        "# Knowledge Index",
        "",
        "Use this page to find the relevant Wiki page before reading it.",
    ]
    for page_type in _PAGE_TYPES:
        typed_pages = [page for page in pages if page.page_type == page_type]
        if not typed_pages:
            continue
        lines.extend(["", f"## {sections[page_type]}", ""])
        for page in typed_pages:
            link = page.path.removeprefix("wiki/")
            lines.append(
                f"- [{_escape_link_text(page.title)}]({link}) — "
                f"{_redact_index_summary(page.summary)}"
            )
    return "\n".join(lines) + "\n"


def _redact_index_summary(summary: str) -> str:
    redacted = _INDEX_ACCESS_KEY.sub("<redacted>", summary)
    return _INDEX_SECRET_VALUE.sub(
        lambda match: f"{match['prefix']}<redacted>{match['suffix']}",
        redacted,
    )


def _index_summaries(content: str) -> list[PageSummary]:
    section_types = {
        "Entities": "entity",
        "Concepts": "concept",
        "Synthesis": "synthesis",
    }
    page_type: PageType | None = None
    pages: list[PageSummary] = []
    for line in content.splitlines():
        if line.startswith("## "):
            named_type = section_types.get(line.removeprefix("## "))
            page_type = cast(PageType, named_type) if named_type is not None else None
            continue
        match = _INDEX_ENTRY.fullmatch(line)
        if match is None or page_type is None:
            continue
        pages.append(
            PageSummary(
                path=f"wiki/{match.group('path')}",
                title=_unescape_link_text(match.group("title")),
                page_type=page_type,
                summary=match.group("summary"),
            )
        )
    return pages


def _parse_page_summary(path: str, content: str) -> PageSummary | None:
    match = _FRONTMATTER.match(content)
    if match is None:
        return None
    fields = _frontmatter_fields(content)
    page_type = fields.get("type")
    if page_type not in _PAGE_TYPES:
        return None
    title = _frontmatter_text(fields.get("title", ""))
    summary = _frontmatter_text(fields.get("summary", ""))
    if not title or not summary:
        return None
    return PageSummary(path=path, title=title, page_type=page_type, summary=summary)


def _frontmatter_fields(content: str) -> dict[str, str]:
    match = _FRONTMATTER.match(content)
    if match is None:
        return {}
    fields: dict[str, str] = {}
    for line in match.group("fields").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def _escape_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _unescape_link_text(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def _frontmatter_text(value: str) -> str:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decoded if isinstance(decoded, str) else value
